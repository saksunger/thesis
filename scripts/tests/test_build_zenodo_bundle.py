"""Tests for scripts.build_zenodo_bundle (Phase 10 A5)."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from scripts import build_zenodo_bundle as B
from tools import manifest as M


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, payload in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    return tmp_path


def _seed_repo_with_manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a minimal repo with data + manifest + metadata. Returns
    (repo_root, manifest_path, metadata_path)."""
    repo = _make_repo(tmp_path, {
        "data/simulated/timeline_medium/samples.parquet": b"PARQUET-1",
        "data/simulated/timeline_medium_seed42/samples.parquet": b"PARQUET-42",
        "data/processed/figure.png": b"\x89PNGfake",
        "data/processed/summary.csv": b"a,b\n1,2\n",
    })
    manifest_path = repo / "data" / "manifest.sha256"
    files = M.iter_files(
        repo_root=repo,
        roots=("data/simulated", "data/processed"),
        exclude_patterns=(),  # don't filter test fixtures
    )
    manifest_path.write_text(M.render_manifest(repo, files), encoding="ascii")

    metadata_path = repo / "docs" / "zenodo_metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps({"version": "v0.0.1-test", "title": "x"}),
        encoding="utf-8",
    )
    return repo, manifest_path, metadata_path


# ---------------------------------------------------------------------------
# _load_manifest_files
# ---------------------------------------------------------------------------


class TestLoadManifestFiles:
    def test_returns_existing_files(self, tmp_path: Path) -> None:
        repo, manifest_path, _ = _seed_repo_with_manifest(tmp_path)
        files = B._load_manifest_files(repo, manifest_path)
        rels = sorted(p.relative_to(repo).as_posix() for p in files)
        assert rels == [
            "data/processed/figure.png",
            "data/processed/summary.csv",
            "data/simulated/timeline_medium/samples.parquet",
            "data/simulated/timeline_medium_seed42/samples.parquet",
        ]

    def test_raises_when_file_missing(self, tmp_path: Path) -> None:
        repo, manifest_path, _ = _seed_repo_with_manifest(tmp_path)
        (repo / "data/processed/summary.csv").unlink()
        with pytest.raises(FileNotFoundError, match="summary.csv"):
            B._load_manifest_files(repo, manifest_path)


# ---------------------------------------------------------------------------
# _resolve_version
# ---------------------------------------------------------------------------


class TestResolveVersion:
    def test_cli_arg_wins(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta.json"
        meta.write_text('{"version": "v9.9.9"}', encoding="utf-8")
        assert B._resolve_version("v1.0.0", meta) == "v1.0.0"

    def test_falls_back_to_metadata(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta.json"
        meta.write_text('{"version": "v0.1.2"}', encoding="utf-8")
        assert B._resolve_version(None, meta) == "v0.1.2"

    def test_defaults_when_no_metadata(self, tmp_path: Path) -> None:
        assert B._resolve_version(None, tmp_path / "missing.json") == "vDEV"

    def test_defaults_when_metadata_malformed(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta.json"
        meta.write_text("not json", encoding="utf-8")
        assert B._resolve_version(None, meta) == "vDEV"


# ---------------------------------------------------------------------------
# _normalised_tarinfo
# ---------------------------------------------------------------------------


class TestNormalisedTarinfo:
    def test_zero_owner_and_canonical_mode(self) -> None:
        ti = B._normalised_tarinfo("data/x.parquet", size=42, mtime=1234.0)
        assert ti.uid == 0 and ti.gid == 0
        assert ti.uname == "" and ti.gname == ""
        assert ti.mode == 0o644
        assert ti.size == 42
        assert ti.mtime == 1234
        assert ti.type == tarfile.REGTYPE


# ---------------------------------------------------------------------------
# build (end-to-end on a synthetic repo)
# ---------------------------------------------------------------------------


class TestBuild:
    def test_produces_tarball_with_expected_members(self, tmp_path: Path) -> None:
        repo, manifest_path, metadata_path = _seed_repo_with_manifest(tmp_path)
        out = tmp_path / "dist" / "bundle.tar.gz"
        path, sha, n, total = B.build(
            repo_root=repo, manifest_path=manifest_path,
            metadata_path=metadata_path, out_path=out, version="v0.0.1-test",
        )
        assert path == out and path.exists()
        assert len(sha) == 64
        assert n == 4

        with tarfile.open(out, "r:gz") as tar:
            names = sorted(m.name for m in tar.getmembers())
        # BUNDLE_INFO + manifest copy + metadata copy + 4 data files = 7 entries.
        assert names == sorted([
            "BUNDLE_INFO.txt",
            "data/manifest.sha256",
            "data/processed/figure.png",
            "data/processed/summary.csv",
            "data/simulated/timeline_medium/samples.parquet",
            "data/simulated/timeline_medium_seed42/samples.parquet",
            "docs/zenodo_metadata.json",
        ])
        # total_bytes excludes BUNDLE_INFO + manifest + metadata; checks the
        # raw size of the listed data files only.
        assert total == sum([
            len(b"PARQUET-1"), len(b"PARQUET-42"),
            len(b"\x89PNGfake"), len(b"a,b\n1,2\n"),
        ])

    def test_is_reproducible(self, tmp_path: Path) -> None:
        """Two builds of the same repo + manifest yield byte-identical
        tarballs (modulo gzip mtime stripped via normalised TarInfo)."""
        repo, manifest_path, metadata_path = _seed_repo_with_manifest(tmp_path)
        out1 = tmp_path / "bundle1.tar.gz"
        out2 = tmp_path / "bundle2.tar.gz"
        B.build(
            repo_root=repo, manifest_path=manifest_path,
            metadata_path=metadata_path, out_path=out1, version="v0.0.1",
        )
        B.build(
            repo_root=repo, manifest_path=manifest_path,
            metadata_path=metadata_path, out_path=out2, version="v0.0.1",
        )
        # gzip embeds an mtime in its own header; strip it by comparing
        # the inner tar payload contents member-by-member.
        with tarfile.open(out1, "r:gz") as t1, tarfile.open(out2, "r:gz") as t2:
            m1 = sorted(t1.getmembers(), key=lambda m: m.name)
            m2 = sorted(t2.getmembers(), key=lambda m: m.name)
            assert [m.name for m in m1] == [m.name for m in m2]
            assert [m.size for m in m1] == [m.size for m in m2]
            assert [m.mode for m in m1] == [m.mode for m in m2]
            assert all(m.uid == 0 and m.gid == 0 for m in m1)
            for a, b in zip(m1, m2):
                # extractfile may return None for non-regular files; all our
                # bundle members are regular.
                pa = t1.extractfile(a)
                pb = t2.extractfile(b)
                assert pa is not None and pb is not None
                assert pa.read() == pb.read()

    def test_raises_when_manifest_missing(self, tmp_path: Path) -> None:
        repo, _, metadata_path = _seed_repo_with_manifest(tmp_path)
        with pytest.raises(FileNotFoundError, match="manifest not found"):
            B.build(
                repo_root=repo,
                manifest_path=tmp_path / "nope.sha256",
                metadata_path=metadata_path,
                out_path=tmp_path / "x.tar.gz",
                version="v0",
            )

    def test_bundle_info_header_records_provenance(self, tmp_path: Path) -> None:
        repo, manifest_path, metadata_path = _seed_repo_with_manifest(tmp_path)
        out = tmp_path / "b.tar.gz"
        _, _, n, total = B.build(
            repo_root=repo, manifest_path=manifest_path,
            metadata_path=metadata_path, out_path=out, version="vXYZ",
        )
        with tarfile.open(out, "r:gz") as tar:
            info_member = tar.extractfile("BUNDLE_INFO.txt")
            assert info_member is not None
            info = info_member.read().decode("utf-8")
        assert "vXYZ" in info
        assert f"file count       : {n}" in info
        assert f"total bytes      : {total}" in info
        # build time should be ISO UTC.
        assert "T" in info and "+00:00" in info

    def test_works_without_metadata_file(self, tmp_path: Path) -> None:
        """If docs/zenodo_metadata.json is absent, bundle should still build
        (just without the embedded metadata member)."""
        repo, manifest_path, _ = _seed_repo_with_manifest(tmp_path)
        missing_meta = tmp_path / "absent.json"
        out = tmp_path / "b.tar.gz"
        B.build(
            repo_root=repo, manifest_path=manifest_path,
            metadata_path=missing_meta, out_path=out, version="v0",
        )
        with tarfile.open(out, "r:gz") as tar:
            names = {m.name for m in tar.getmembers()}
        assert "BUNDLE_INFO.txt" in names
        assert "data/manifest.sha256" in names
        assert "docs/absent.json" not in names


# ---------------------------------------------------------------------------
# _sha256_path
# ---------------------------------------------------------------------------


class TestSha256Path:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        p = tmp_path / "x.bin"
        payload = b"hello world" * 1000
        p.write_bytes(payload)
        assert B._sha256_path(p) == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# main (CLI smoke)
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_main_writes_bundle(self, tmp_path: Path, monkeypatch) -> None:
        repo, manifest_path, metadata_path = _seed_repo_with_manifest(tmp_path)
        out = tmp_path / "dist" / "x.tar.gz"
        rc = B.main([
            "--manifest", str(manifest_path),
            "--metadata", str(metadata_path),
            "--out", str(out),
            "--repo-root", str(repo),
            "--version", "v0.test",
        ])
        assert rc == 0
        assert out.is_file()
