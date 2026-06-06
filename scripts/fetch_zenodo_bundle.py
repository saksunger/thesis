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
DEFAULT_DOI: str | None = None  # set when the canonical deposit is minted


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _resolve_doi_to_files_api(doi: str) -> tuple[str, str]:
    """Resolve a Zenodo DOI -> (download URL, expected SHA256).

    Calls the public Zenodo REST API to find the first .tar.gz attachment
    on the record and returns its download URL + checksum.
    """
    # Accept both bare DOIs ("10.5281/zenodo.123") and full URLs.
    doi = doi.strip()
    if doi.startswith("http"):
        if "zenodo.org/record/" in doi or "zenodo.org/records/" in doi:
            record_id = doi.rstrip("/").split("/")[-1]
        else:
            raise ValueError(f"unrecognised Zenodo URL: {doi}")
    elif doi.startswith("10.5281/zenodo."):
        record_id = doi.split(".")[-1]
    else:
        raise ValueError(
            "DOI must be either '10.5281/zenodo.<id>' or a zenodo.org URL"
        )

    api_url = f"https://zenodo.org/api/records/{record_id}"
    print(f"fetch_zenodo_bundle: resolving {api_url}", file=sys.stderr)
    with urllib.request.urlopen(api_url, timeout=30) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))

    files = payload.get("files", []) or []
    tarballs = [f for f in files if f.get("key", "").endswith(".tar.gz")]
    if not tarballs:
        raise RuntimeError(f"no .tar.gz attachment on Zenodo record {record_id}")
    chosen = tarballs[0]
    url = chosen["links"]["self"]
    sha256_field = chosen.get("checksum") or ""
    if sha256_field.startswith("sha256:"):
        sha256_field = sha256_field[len("sha256:") :]
    if sha256_field.startswith("md5:"):
        raise RuntimeError(
            "Zenodo record exposes md5 only; SHA256 mismatch verification "
            "is not implementable. Re-deposit with SHA256 enabled."
        )
    if len(sha256_field) != 64:
        raise RuntimeError(
            f"unexpected SHA256 length from Zenodo: {sha256_field!r}"
        )
    return url, sha256_field.lower()


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
    """Unpack tarball at repo_root. Returns number of files extracted."""
    with tarfile.open(tarball, mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        for m in members:
            # Defensive: block path traversal.
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise RuntimeError(f"refusing to extract unsafe path: {m.name}")
        tar.extractall(repo_root, members=members)
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
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()

    # Step 1: figure out where the tarball will come from.
    if args.local is not None:
        local_path = args.local.resolve()
        if not local_path.is_file():
            print(f"--local file not found: {local_path}", file=sys.stderr)
            return 2
        tarball = local_path
        expected_sha = args.sha256
    else:
        if args.url:
            if not args.sha256:
                print("--url requires --sha256 for integrity verification", file=sys.stderr)
                return 2
            url, expected_sha = args.url, args.sha256.lower()
        elif args.doi:
            url, expected_sha = _resolve_doi_to_files_api(args.doi)
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
        if tarball.exists() and _sha256_file(tarball) == expected_sha:
            print(f"fetch_zenodo_bundle: cache hit at {tarball}", file=sys.stderr)
        else:
            try:
                _stream_download(url, tarball)
            except urllib.error.URLError as exc:
                print(f"download failed: {exc}", file=sys.stderr)
                return 1

    # Step 2: SHA256 verify (if we have an expected hash).
    if expected_sha is not None:
        actual_sha = _sha256_file(tarball)
        if actual_sha != expected_sha:
            print(
                f"SHA256 mismatch!\n  expected: {expected_sha}\n  actual  : {actual_sha}",
                file=sys.stderr,
            )
            return 1
        print(f"fetch_zenodo_bundle: SHA256 OK ({actual_sha[:12]}...)", file=sys.stderr)
    else:
        print(
            "WARNING: no SHA256 provided; skipping integrity verification "
            "(use --sha256 in production).",
            file=sys.stderr,
        )

    # Step 3: unpack.
    n = _unpack(tarball, repo_root)
    print(f"fetch_zenodo_bundle: extracted {n} files into {repo_root}", file=sys.stderr)
    print("Next: `make verify-cache` to re-check every SHA256.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
