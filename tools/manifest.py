"""SHA256 manifest of cached simulator + processed artifacts.

Used by the Phase 10 reproducibility package to (a) freeze a content-addressed
snapshot of every parquet / CSV / JSON / PNG / .mat that the thesis cites and
(b) let independent reviewers verify their downloaded Zenodo bundle matches
the deposit byte-for-byte.

Scope
-----
Hashes every file under `data/simulated/` and `data/processed/` with an
extension in :data:`INCLUDED_EXTENSIONS`. Excludes:

- ``data/raw_public/**`` — third-party datasets (NordicDat, Bangladesh) have
  their own provenance per ``data/raw_public/README.md``.
- ``data/external/**`` — misc third-party material.
- ``.gitkeep`` / ``README.md`` / scratch files — non-result metadata that
  changes independently of the analysis.

Verification semantics
----------------------
``--verify`` only checks files listed in the manifest. Extra files in
``data/`` are ignored (they are out-of-scope for the deposit). Missing
files and SHA mismatches both fail the run with exit code 1.

Usage
-----
::

    python -m tools.manifest --generate                       # write data/manifest.sha256
    python -m tools.manifest --verify                         # check files vs manifest
    python -m tools.manifest --generate --out my.sha256       # custom output path
    python -m tools.manifest --verify --manifest my.sha256    # custom manifest path

Output format
-------------
GNU-coreutils ``sha256sum``-compatible: ``<64-hex>  <relative_path>`` per
line, sorted by path, ASCII-only, trailing newline. Reviewers without
Python can also run ``sha256sum -c data/manifest.sha256`` from the repo
root.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_ROOTS = ("data/simulated", "data/processed")

INCLUDED_EXTENSIONS = frozenset({
    ".parquet",
    ".csv",
    ".json",
    ".png",
    ".mat",
})

CHUNK_SIZE = 1 << 20  # 1 MiB streaming read


def iter_files(repo_root: Path, roots: Iterable[str]) -> list[Path]:
    """Return a deterministic globally-sorted list of in-scope files.

    Sorts across all roots so the manifest order does not depend on the
    `roots` argument order — produces a stable diff target across
    runs even if the caller reorders the roots list.
    """

    out: list[Path] = []
    for rel_root in roots:
        root = (repo_root / rel_root).resolve()
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in INCLUDED_EXTENSIONS:
                continue
            out.append(p)
    out.sort(key=lambda p: p.relative_to(repo_root).as_posix())
    return out


def sha256_file(path: Path) -> str:
    """Stream-hash a file, returning the lowercase hex digest."""

    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def render_manifest(repo_root: Path, files: Iterable[Path]) -> str:
    """Serialise (hash, relative_path) pairs in sha256sum-compatible form."""

    lines = []
    for p in files:
        rel = p.relative_to(repo_root)
        lines.append(f"{sha256_file(p)}  {rel.as_posix()}")
    return "\n".join(lines) + ("\n" if lines else "")


def generate(repo_root: Path, out_path: Path, roots: Iterable[str]) -> int:
    files = iter_files(repo_root, roots)
    payload = render_manifest(repo_root, files)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="ascii")
    print(
        f"manifest: wrote {len(files)} entries to {out_path}",
        file=sys.stderr,
    )
    return 0


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """Parse manifest body into (sha, relative_path) pairs. Robust to blanks."""

    pairs: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # GNU sha256sum format uses TWO spaces between hash and filename.
        try:
            sha, rel = line.split("  ", 1)
        except ValueError:
            raise ValueError(f"malformed manifest line: {raw!r}") from None
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
            raise ValueError(f"not a sha256: {sha!r}")
        pairs.append((sha.lower(), rel))
    return pairs


def verify(repo_root: Path, manifest_path: Path) -> int:
    if not manifest_path.exists():
        print(f"manifest: not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        pairs = parse_manifest(manifest_path.read_text(encoding="ascii"))
    except ValueError as exc:
        print(f"manifest: parse error: {exc}", file=sys.stderr)
        return 2

    n_ok = 0
    n_missing = 0
    n_mismatch = 0
    for expected_sha, rel in pairs:
        p = repo_root / rel
        if not p.exists():
            print(f"MISSING  {rel}", file=sys.stderr)
            n_missing += 1
            continue
        actual_sha = sha256_file(p)
        if actual_sha != expected_sha:
            print(
                f"MISMATCH {rel}  (expected {expected_sha[:12]}..., got {actual_sha[:12]}...)",
                file=sys.stderr,
            )
            n_mismatch += 1
        else:
            n_ok += 1

    total = n_ok + n_missing + n_mismatch
    summary = (
        f"manifest verify: {n_ok}/{total} OK, "
        f"{n_missing} missing, {n_mismatch} mismatch"
    )
    print(summary, file=sys.stderr)
    return 0 if (n_missing == 0 and n_mismatch == 0) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--generate", action="store_true",
        help="Walk --roots and write a fresh manifest to --out / --manifest.",
    )
    mode.add_argument(
        "--verify", action="store_true",
        help="Check that every file in --manifest exists and hashes match.",
    )

    parser.add_argument(
        "--manifest", type=Path, default=Path("data/manifest.sha256"),
        help="Manifest path. With --verify, the file to check against. "
             "With --generate, the default output path (overridden by --out).",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Override the output path for --generate.",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="Root directory for relative paths (default: cwd).",
    )
    parser.add_argument(
        "--roots", nargs="+", default=list(DEFAULT_ROOTS),
        help=f"Directories to walk for --generate (default: {' '.join(DEFAULT_ROOTS)}).",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    out_path = args.out or args.manifest

    if args.generate:
        return generate(repo_root, out_path, args.roots)
    return verify(repo_root, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
