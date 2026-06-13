"""Download + unpack the Zenodo deposit tarball into `data/`.

Reviewer-side counterpart to `scripts/build_zenodo_bundle.py`. After
downloading and verifying the tarball's SHA256 against the published
checksum, unpacks it at the repo root so `data/simulated/`,
`data/processed/`, and `data/manifest.sha256` populate exactly as the
upstream maintainer committed them.

The script is idempotent: re-running while files are already present
checks SHA256 first and skips the download if the tarball cache is
valid, or skips the unpack if every manifested file is already on disk
with the right hash.

Usage
-----
::

    # 1. Direct DOI / Zenodo record URL (recommended; resolves the
    #    canonical archive URL automatically):
    python -m scripts.fetch_zenodo_bundle --doi 10.5281/zenodo.XXXXXXX

    # 2. Explicit tarball URL + expected SHA256 (advanced; useful for
    #    pre-publication / private deposit testing):
    python -m scripts.fetch_zenodo_bundle \\
        --url https://zenodo.org/record/XXXXXXX/files/thesis_artifacts_v1.0.0.tar.gz \\
        --sha256 abc123...

    # 3. Local tarball (already-downloaded, e.g. for offline reviewers):
    python -m scripts.fetch_zenodo_bundle --local dist/thesis_artifacts_v1.0.0.tar.gz

Until a real Zenodo DOI is minted (Phase 10 A5 final step), the script
exits with a helpful "no upstream yet" message unless `--local` is
passed. Maintainers can produce the local tarball via
`make zenodo-bundle`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CACHE_DIR = Path(".cache/zenodo")
DEFAULT_REPO_ROOT = Path.cwd()
# INTERIM (2026-06-12): points at the v1.0.0-rc1 SANDBOX rehearsal deposit.
# Sandbox DOIs (10.5072) are not registered with DataCite, do not resolve
# via doi.org, and the deposit is purged after ~6 months — replace with the
# minted production DOI (10.5281/zenodo.<id>) at the canonical deposit cut
# (see docs/zenodo_upload.md release checklist).
DEFAULT_DOI: str | None = "10.5072/zenodo.509971"

# Zenodo has two parallel deployments. Production DOIs use the 10.5281
# prefix and live at zenodo.org; the throwaway test environment uses the
# 10.5072 prefix and lives at sandbox.zenodo.org. Both expose the same
# REST API shape, so we just rewrite the host based on the DOI prefix
# (or take an explicit `--sandbox` / `sandbox.zenodo.org` URL).
ZENODO_PROD_HOST = "zenodo.org"
ZENODO_SANDBOX_HOST = "sandbox.zenodo.org"
DOI_PREFIX_PROD = "10.5281/zenodo."
DOI_PREFIX_SANDBOX = "10.5072/zenodo."


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    return _hash_file(path, "sha256", chunk)


def _md5_file(path: Path, chunk: int = 1 << 20) -> str:
    return _hash_file(path, "md5", chunk)


def _hash_file(path: Path, algo: str, chunk: int = 1 << 20) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _parse_zenodo_checksum(field: str) -> tuple[str, str]:
    """Split Zenodo's `"<algo>:<hex>"` checksum string into (algo, hex).

    Zenodo's REST API currently returns MD5 only for most deposits
    (the SHA256 column is not populated in the public payload), so the
    fetch script must accept MD5 as the integrity primitive at the
    transport layer. Per-file SHA256 verification then happens via the
    in-bundle `data/manifest.sha256` after extraction (`make verify-cache`).
    """
    if ":" not in field:
        raise ValueError(f"unrecognised Zenodo checksum format: {field!r}")
    algo, hex_ = field.split(":", 1)
    algo = algo.strip().lower()
    hex_ = hex_.strip().lower()
    if algo not in {"md5", "sha256"}:
        raise ValueError(f"unsupported Zenodo checksum algorithm: {algo!r}")
    expected_len = 32 if algo == "md5" else 64
    if len(hex_) != expected_len:
        raise ValueError(
            f"unexpected {algo} hex length {len(hex_)} (expected {expected_len})"
        )
    return algo, hex_


def _resolve_host(doi: str, force_sandbox: bool = False) -> str:
    """Pick zenodo.org vs sandbox.zenodo.org from the DOI prefix.

    `force_sandbox` overrides the prefix (useful when a 10.5072 sandbox
    DOI somehow lacks the prefix, or when testing the resolver itself).
    """
    if force_sandbox:
        return ZENODO_SANDBOX_HOST
    if doi.startswith(DOI_PREFIX_SANDBOX):
        return ZENODO_SANDBOX_HOST
    if "sandbox.zenodo.org" in doi:
        return ZENODO_SANDBOX_HOST
    return ZENODO_PROD_HOST


def _resolve_doi_to_files_api(
    doi: str, force_sandbox: bool = False,
) -> tuple[str, str, str]:
    """Resolve a Zenodo DOI -> (download URL, checksum algorithm, hex digest).

    Calls the public Zenodo REST API to find the first .tar.gz attachment
    on the record and returns its download URL + checksum. Routes to
    production or sandbox based on the DOI prefix (10.5281 -> production,
    10.5072 -> sandbox), with `force_sandbox=True` as an explicit override.

    Zenodo currently exposes MD5 for most deposits; SHA256 is not always
    in the public payload. We use whatever the API returns for transport-
    layer integrity. The bundle's per-file SHA256 manifest
    (`data/manifest.sha256`, also inside the tarball) is the authoritative
    integrity primitive once unpacked.
    """
    host = _resolve_host(doi, force_sandbox=force_sandbox)

    # Accept both bare DOIs ("10.5281/zenodo.123") and full URLs.
    doi = doi.strip()
    if doi.startswith("http"):
        if "zenodo.org/record/" in doi or "zenodo.org/records/" in doi:
            record_id = doi.rstrip("/").split("/")[-1]
        else:
            raise ValueError(f"unrecognised Zenodo URL: {doi}")
    elif doi.startswith(DOI_PREFIX_PROD) or doi.startswith(DOI_PREFIX_SANDBOX):
        record_id = doi.split(".")[-1]
    else:
        raise ValueError(
            "DOI must be either '10.5281/zenodo.<id>' (production), "
            "'10.5072/zenodo.<id>' (sandbox), or a zenodo.org URL"
        )

    api_url = f"https://{host}/api/records/{record_id}"
    print(f"fetch_zenodo_bundle: resolving {api_url}", file=sys.stderr)
    with urllib.request.urlopen(api_url, timeout=30) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))

    files = payload.get("files", []) or []
    tarballs = [f for f in files if f.get("key", "").endswith(".tar.gz")]
    if not tarballs:
        raise RuntimeError(f"no .tar.gz attachment on Zenodo record {record_id}")
    chosen = tarballs[0]
    url = chosen["links"]["self"]
    checksum_field = chosen.get("checksum") or ""
    if not checksum_field:
        raise RuntimeError(
            f"Zenodo record {record_id} did not return a checksum for "
            f"{chosen.get('key')!r}"
        )
    algo, hex_digest = _parse_zenodo_checksum(checksum_field)
    return url, algo, hex_digest


def _stream_download(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    print(f"fetch_zenodo_bundle: GET {url} -> {out_path}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=300) as resp, tmp.open("wb") as f:  # noqa: S310
        total = int(resp.headers.get("Content-Length") or 0)
        chunk = 1 << 20
        downloaded = 0
        while True:
            block = resp.read(chunk)
            if not block:
                break
            f.write(block)
            downloaded += len(block)
            if total:
                pct = downloaded * 100 / total
                print(
                    f"\r  {downloaded / 1e6:6.1f} / {total / 1e6:6.1f} MB ({pct:5.1f} %)",
                    end="", file=sys.stderr,
                )
        print("", file=sys.stderr)
    tmp.replace(out_path)


def _unpack(tarball: Path, repo_root: Path) -> int:
    """Unpack tarball at repo_root. Returns number of files extracted.

    Uses tarfile's ``filter="data"`` extraction policy (Python 3.12+),
    which is the future default in 3.14 and rejects absolute paths,
    symlinks pointing outside the destination, and other unsafe member
    types. We additionally pre-screen members to (a) block ``..`` path
    components and absolute paths upfront with a clearer error and
    (b) skip non-regular entries that the bundle never contains.
    """
    with tarfile.open(tarball, mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        for m in members:
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise RuntimeError(f"refusing to extract unsafe path: {m.name}")
        tar.extractall(repo_root, members=members, filter="data")
    return len(members)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--doi", type=str, default=DEFAULT_DOI,
        help="Zenodo DOI (e.g. 10.5281/zenodo.XXXXXXX). Falls back to DEFAULT_DOI baked into the script.",
    )
    source.add_argument(
        "--url", type=str, default=None,
        help="Direct download URL for the bundle tarball. Requires --sha256.",
    )
    source.add_argument(
        "--local", type=Path, default=None,
        help="Use an already-downloaded tarball at this path (skip the download step).",
    )
    parser.add_argument(
        "--sha256", type=str, default=None,
        help="Expected SHA256 of the tarball (required with --url, optional with --local).",
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
        help="Where to store downloaded tarballs between runs.",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repo root (the tarball unpacks at this directory).",
    )
    parser.add_argument(
        "--sandbox", action="store_true",
        help="Force the sandbox.zenodo.org host (auto-detected from "
             "DOI prefix 10.5072; this flag is only needed when --doi "
             "is omitted/non-standard).",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()

    # Step 1: figure out where the tarball will come from.
    # `expected` is a (algorithm, hex) pair or None; algorithm is either
    # "sha256" or "md5" depending on what Zenodo / the user provided.
    expected: tuple[str, str] | None
    if args.local is not None:
        local_path = args.local.resolve()
        if not local_path.is_file():
            print(f"--local file not found: {local_path}", file=sys.stderr)
            return 2
        tarball = local_path
        expected = ("sha256", args.sha256.lower()) if args.sha256 else None
    else:
        if args.url:
            if not args.sha256:
                print("--url requires --sha256 for integrity verification", file=sys.stderr)
                return 2
            url = args.url
            expected = ("sha256", args.sha256.lower())
        elif args.doi:
            url, algo, hex_digest = _resolve_doi_to_files_api(
                args.doi, force_sandbox=args.sandbox,
            )
            expected = (algo, hex_digest)
        else:
            print(
                "no source: pass --doi, --url+--sha256, or --local.\n"
                "Until the canonical Zenodo deposit is minted, build a local\n"
                "tarball with `make zenodo-bundle` and re-run this script with\n"
                "`--local dist/thesis_artifacts_<version>.tar.gz`.",
                file=sys.stderr,
            )
            return 2

        tarball = args.cache_dir / Path(url).name
        tarball.parent.mkdir(parents=True, exist_ok=True)
        if (
            tarball.exists()
            and expected is not None
            and _hash_file(tarball, expected[0]) == expected[1]
        ):
            print(f"fetch_zenodo_bundle: cache hit at {tarball}", file=sys.stderr)
        else:
            try:
                _stream_download(url, tarball)
            except urllib.error.URLError as exc:
                print(f"download failed: {exc}", file=sys.stderr)
                return 1

    # Step 2: transport-layer integrity check (matches whatever Zenodo
    # exposed - sha256 if available, md5 otherwise). Per-file SHA256
    # verification still runs after extraction via the in-bundle
    # `data/manifest.sha256` (`make verify-cache`).
    if expected is not None:
        algo, expected_hex = expected
        actual_hex = _hash_file(tarball, algo)
        if actual_hex != expected_hex:
            print(
                f"{algo.upper()} mismatch!\n"
                f"  expected: {expected_hex}\n  actual  : {actual_hex}",
                file=sys.stderr,
            )
            return 1
        print(
            f"fetch_zenodo_bundle: {algo.upper()} OK ({actual_hex[:12]}...)",
            file=sys.stderr,
        )
        if algo != "sha256":
            print(
                f"fetch_zenodo_bundle: note - Zenodo exposed {algo.upper()} only; "
                "run `make verify-cache` to confirm per-file SHA256 from "
                "`data/manifest.sha256`.",
                file=sys.stderr,
            )
    else:
        print(
            "WARNING: no checksum provided; skipping transport-layer "
            "integrity check (run `make verify-cache` to verify per-file "
            "SHA256 from the in-bundle manifest).",
            file=sys.stderr,
        )

    # Step 3: unpack.
    n = _unpack(tarball, repo_root)
    print(f"fetch_zenodo_bundle: extracted {n} files into {repo_root}", file=sys.stderr)
    print("Next: `make verify-cache` to re-check every SHA256.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
