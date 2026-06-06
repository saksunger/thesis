"""Build the Zenodo deposit tarball for the thesis.

Walks the committed `data/manifest.sha256` and packs every listed file
into a single deterministic `.tar.gz` under `dist/`, alongside:

- a copy of the manifest itself (for `make verify-cache` post-unpack),
- a copy of `docs/zenodo_metadata.json` (so the deposit is self-describing),
- a `BUNDLE_INFO.txt` provenance header (git commit, build time, byte count).

The tarball is reproducible: file order, mtime, owner, and permissions are
normalised so two builds of the same `data/manifest.sha256` produce
byte-identical output.

Usage
-----
::

    python -m scripts.build_zenodo_bundle                          # default: dist/thesis_artifacts_<version>.tar.gz
    python -m scripts.build_zenodo_bundle --version v1.0.0
    python -m scripts.build_zenodo_bundle --out dist/x.tar.gz

The default version comes from `docs/zenodo_metadata.json` (`version`
field). The tarball SHA256 is printed at the end — copy this into the
Zenodo upload form's notes field for forensic traceability.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Iterable

from tools import manifest as _M

DEFAULT_METADATA_PATH = Path("docs/zenodo_metadata.json")
DEFAULT_MANIFEST_PATH = Path("data/manifest.sha256")
DEFAULT_OUT_DIR = Path("dist")
BUNDLE_INFO_NAME = "BUNDLE_INFO.txt"


def _git_short_sha(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def _load_manifest_files(repo_root: Path, manifest_path: Path) -> list[Path]:
    pairs = _M.parse_manifest(manifest_path.read_text(encoding="ascii"))
    out: list[Path] = []
    for _sha, rel in pairs:
        p = repo_root / rel
        if not p.is_file():
            raise FileNotFoundError(f"manifest references missing file: {rel}")
        out.append(p)
    return out


def _sha256_path(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _normalised_tarinfo(name: str, size: int, mtime: float = 0.0) -> tarfile.TarInfo:
    """Build a TarInfo with deterministic uid/gid/mode/mtime for reproducibility."""
    ti = tarfile.TarInfo(name=name)
    ti.size = size
    ti.mtime = int(mtime)
    ti.mode = 0o644
    ti.uid = 0
    ti.gid = 0
    ti.uname = ""
    ti.gname = ""
    ti.type = tarfile.REGTYPE
    return ti


def _bundle_info_text(
    *,
    version: str,
    git_sha: str,
    manifest_sha: str,
    n_files: int,
    total_bytes: int,
    build_time_iso: str,
) -> str:
    return (
        "ThesisRepro Zenodo bundle — provenance header\n"
        "============================================\n"
        f"version          : {version}\n"
        f"source commit    : {git_sha}\n"
        f"manifest sha256  : {manifest_sha}\n"
        f"file count       : {n_files}\n"
        f"total bytes      : {total_bytes}\n"
        f"build time (UTC) : {build_time_iso}\n"
        "\n"
        "Unpack this tarball into the repo root. Then run `make verify-cache`\n"
        "to confirm every file's SHA256 matches the in-bundle manifest.sha256\n"
        "(also committed in the upstream repository at data/manifest.sha256).\n"
        "\n"
        "Full reproducibility guide: docs/reproducibility.md in the repo.\n"
    )


def _resolve_version(version_arg: str | None, metadata_path: Path) -> str:
    if version_arg:
        return version_arg
    if not metadata_path.exists():
        return "vDEV"
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        return str(meta.get("version") or "vDEV")
    except (OSError, json.JSONDecodeError):
        return "vDEV"


def build(
    *,
    repo_root: Path,
    manifest_path: Path,
    metadata_path: Path,
    out_path: Path,
    version: str,
) -> tuple[Path, str, int, int]:
    """Build the tarball. Returns (path, sha256, file_count, total_bytes)."""

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest not found: {manifest_path}. "
            f"Run `make manifest` first."
        )

    files = _load_manifest_files(repo_root, manifest_path)
    n_files = len(files)
    total_bytes = sum(p.stat().st_size for p in files)
    manifest_sha = _sha256_path(manifest_path)
    git_sha = _git_short_sha(repo_root)
    build_time = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

    info_text = _bundle_info_text(
        version=version,
        git_sha=git_sha,
        manifest_sha=manifest_sha,
        n_files=n_files,
        total_bytes=total_bytes,
        build_time_iso=build_time,
    ).encode("utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")

    # Sort files deterministically (already sorted from manifest, but be safe).
    files = sorted(files, key=lambda p: p.relative_to(repo_root).as_posix())

    with tarfile.open(tmp_out, mode="w:gz", compresslevel=6) as tar:
        # Provenance header first so reviewers see it on `tar tzf`.
        info_ti = _normalised_tarinfo(BUNDLE_INFO_NAME, len(info_text))
        tar.addfile(info_ti, BytesIO(info_text))

        # The manifest itself (so `make verify-cache` works post-unpack).
        manifest_bytes = manifest_path.read_bytes()
        manifest_ti = _normalised_tarinfo(
            f"data/{manifest_path.name}", len(manifest_bytes),
        )
        tar.addfile(manifest_ti, BytesIO(manifest_bytes))

        # Optional embedded copy of zenodo metadata for self-describing
        # deposits (separate from the user-facing metadata you upload to
        # the Zenodo form).
        if metadata_path.exists():
            meta_bytes = metadata_path.read_bytes()
            meta_ti = _normalised_tarinfo(
                f"docs/{metadata_path.name}", len(meta_bytes),
            )
            tar.addfile(meta_ti, BytesIO(meta_bytes))

        # Then every manifested artefact, deterministically.
        for p in files:
            rel = p.relative_to(repo_root).as_posix()
            ti = _normalised_tarinfo(rel, p.stat().st_size)
            with p.open("rb") as f:
                tar.addfile(ti, f)

    tmp_out.replace(out_path)
    bundle_sha = _sha256_path(out_path)
    return out_path, bundle_sha, n_files, total_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST_PATH,
        help="SHA256 manifest of files to include (default: data/manifest.sha256).",
    )
    parser.add_argument(
        "--metadata", type=Path, default=DEFAULT_METADATA_PATH,
        help="Zenodo metadata JSON to embed in the bundle (default: docs/zenodo_metadata.json).",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output tarball path (default: dist/thesis_artifacts_<version>.tar.gz).",
    )
    parser.add_argument(
        "--version", type=str, default=None,
        help="Bundle version (default: read from metadata 'version', falls back to vDEV).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="Repo root for resolving manifest-relative paths.",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    version = _resolve_version(args.version, args.metadata)
    out_path = args.out or (DEFAULT_OUT_DIR / f"thesis_artifacts_{version}.tar.gz")

    print(
        f"build_zenodo_bundle: manifest={args.manifest} version={version} -> {out_path}",
        file=sys.stderr,
    )
    out, bundle_sha, n_files, total_bytes = build(
        repo_root=repo_root,
        manifest_path=args.manifest,
        metadata_path=args.metadata,
        out_path=out_path,
        version=version,
    )

    final_size = out.stat().st_size
    print("---", file=sys.stderr)
    print(f"bundle path     : {out}", file=sys.stderr)
    print(f"bundle sha256   : {bundle_sha}", file=sys.stderr)
    print(f"bundle size     : {final_size:,} bytes ({final_size / 1e9:.2f} GB)", file=sys.stderr)
    print(f"file count      : {n_files}", file=sys.stderr)
    print(f"uncompressed    : {total_bytes:,} bytes ({total_bytes / 1e9:.2f} GB)", file=sys.stderr)
    print(
        f"compression     : {(1 - final_size / total_bytes) * 100:.1f}% saved",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
