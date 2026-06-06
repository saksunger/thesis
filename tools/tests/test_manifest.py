"""Tests for tools.manifest (Phase 10 reproducibility manifest)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tools import manifest as M


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """Materialise a fake repo tree under tmp_path; return repo root."""
    for rel, payload in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    return tmp_path


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# iter_files
# ---------------------------------------------------------------------------


class TestIterFiles:
    def test_picks_only_included_extensions(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "data/simulated/timeline_x/samples.parquet": b"p",
            "data/simulated/timeline_x/run_metadata.json": b"j",
            "data/simulated/timeline_x/.gitkeep": b"",
            "data/simulated/timeline_x/README.md": b"# notes",
            "data/processed/x/table.csv": b"a,b\n",
            "data/processed/x/figure.png": b"\x89PNG",
            "data/processed/x/notes.txt": b"ignored",
        })

        out = M.iter_files(repo, ["data/simulated", "data/processed"])

        rels = sorted(p.relative_to(repo).as_posix() for p in out)
        assert rels == [
            "data/processed/x/figure.png",
            "data/processed/x/table.csv",
            "data/simulated/timeline_x/run_metadata.json",
            "data/simulated/timeline_x/samples.parquet",
        ]

    def test_deterministic_sort_order(self, tmp_path: Path) -> None:
        # Create files in reverse alphabetic order to check sort, not fs-order.
        repo = _make_repo(tmp_path, {
            "data/simulated/c/x.parquet": b"c",
            "data/simulated/a/x.parquet": b"a",
            "data/simulated/b/x.parquet": b"b",
        })
        out = M.iter_files(repo, ["data/simulated"])
        rels = [p.relative_to(repo).as_posix() for p in out]
        assert rels == [
            "data/simulated/a/x.parquet",
            "data/simulated/b/x.parquet",
            "data/simulated/c/x.parquet",
        ]

    def test_global_sort_across_roots(self, tmp_path: Path) -> None:
        """Manifest order must NOT depend on the `roots` argument order."""
        repo = _make_repo(tmp_path, {
            "data/simulated/x.parquet": b"s",
            "data/processed/y.csv": b"p",
        })
        order1 = M.iter_files(repo, ["data/simulated", "data/processed"])
        order2 = M.iter_files(repo, ["data/processed", "data/simulated"])
        rels1 = [p.relative_to(repo).as_posix() for p in order1]
        rels2 = [p.relative_to(repo).as_posix() for p in order2]
        assert rels1 == rels2
        # And the order is alphabetic: processed < simulated.
        assert rels1 == ["data/processed/y.csv", "data/simulated/x.parquet"]

    def test_skips_missing_root(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "data/simulated/x.parquet": b"ok",
        })
        # data/processed/ does not exist — should not crash.
        out = M.iter_files(repo, ["data/simulated", "data/processed"])
        assert len(out) == 1


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


class TestSha256File:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        payload = b"some bytes" * 4096
        p = tmp_path / "x.bin"
        p.write_bytes(payload)
        assert M.sha256_file(p) == _sha(payload)

    def test_streams_in_chunks(self, tmp_path: Path) -> None:
        # File larger than CHUNK_SIZE to exercise the streaming loop.
        payload = os.urandom(M.CHUNK_SIZE * 2 + 17)
        p = tmp_path / "big.bin"
        p.write_bytes(payload)
        assert M.sha256_file(p) == _sha(payload)


# ---------------------------------------------------------------------------
# generate / render_manifest
# ---------------------------------------------------------------------------


class TestGenerate:
    def _hash_body_lines(self, text: str) -> list[str]:
        """Return only the SHA lines, stripping the `#`-prefixed header."""
        return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]

    def test_writes_sha256sum_compatible_format(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "data/simulated/t/samples.parquet": b"hello",
            "data/processed/t/table.csv": b"a,b\n1,2\n",
        })
        out = tmp_path / "data" / "manifest.sha256"

        rc = M.generate(repo, out, ["data/simulated", "data/processed"])
        assert rc == 0

        text = out.read_text(encoding="ascii")
        body = self._hash_body_lines(text)
        assert len(body) == 2
        for line in body:
            sha, _, rel = line.partition("  ")
            assert len(sha) == 64
            assert all(c in "0123456789abcdef" for c in sha)
            assert rel.startswith("data/")

        rels = [ln.split("  ", 1)[1] for ln in body]
        assert rels == sorted(rels)

    def test_header_is_present_and_self_documenting(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "data/simulated/t/x.parquet": b"v",
        })
        out = tmp_path / "manifest.sha256"
        M.generate(repo, out, ["data/simulated"])
        text = out.read_text(encoding="ascii")
        comments = [ln for ln in text.splitlines() if ln.startswith("#")]
        assert any("Generated at" in ln for ln in comments)
        assert any("Source commit" in ln for ln in comments)
        assert any("Entry count" in ln for ln in comments)

    def test_round_trip_idempotent_modulo_header(self, tmp_path: Path) -> None:
        """Body must be byte-identical across runs. Header has a timestamp."""
        repo = _make_repo(tmp_path, {
            "data/simulated/t/x.parquet": b"v1",
            "data/processed/t/y.csv": b"v2",
        })
        out1 = tmp_path / "m1.sha256"
        out2 = tmp_path / "m2.sha256"
        M.generate(repo, out1, ["data/simulated", "data/processed"])
        M.generate(repo, out2, ["data/simulated", "data/processed"])
        body1 = self._hash_body_lines(out1.read_text())
        body2 = self._hash_body_lines(out2.read_text())
        assert body1 == body2

    def test_empty_when_no_files(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.sha256"
        rc = M.generate(tmp_path, out, ["data/simulated"])
        assert rc == 0
        # Header is still written; body is empty.
        text = out.read_text(encoding="ascii")
        assert self._hash_body_lines(text) == []


# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_parses_valid_lines(self) -> None:
        sha_a = "0" * 64
        sha_b = "f" * 64
        text = (
            f"{sha_a}  data/simulated/t/x.parquet\n"
            f"{sha_b}  data/processed/t/y.csv\n"
        )
        out = M.parse_manifest(text)
        assert out == [
            (sha_a, "data/simulated/t/x.parquet"),
            (sha_b, "data/processed/t/y.csv"),
        ]

    def test_skips_blank_and_comment_lines(self) -> None:
        sha = "0" * 64
        text = (
            "\n"
            "# header comment\n"
            f"{sha}  data/simulated/t/x.parquet\n"
            "\n"
        )
        out = M.parse_manifest(text)
        assert len(out) == 1

    def test_rejects_malformed_lines(self) -> None:
        # single space instead of two — sha256sum format requires two.
        sha = "0" * 64
        with pytest.raises(ValueError, match="malformed"):
            M.parse_manifest(f"{sha} data/x.parquet\n")

    def test_rejects_non_hex_sha(self) -> None:
        sha_bad = "zz" + "0" * 62
        with pytest.raises(ValueError, match="sha256"):
            M.parse_manifest(f"{sha_bad}  data/x.parquet\n")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = _make_repo(tmp_path, {
            "data/simulated/t/x.parquet": b"v1",
            "data/processed/t/y.csv": b"v2",
        })
        manifest = tmp_path / "manifest.sha256"
        M.generate(repo, manifest, ["data/simulated", "data/processed"])
        return repo, manifest

    def test_clean_match_returns_zero(self, tmp_path: Path) -> None:
        repo, manifest = self._setup(tmp_path)
        assert M.verify(repo, manifest) == 0

    def test_missing_file_returns_one(self, tmp_path: Path) -> None:
        repo, manifest = self._setup(tmp_path)
        (repo / "data/simulated/t/x.parquet").unlink()
        assert M.verify(repo, manifest) == 1

    def test_mismatched_hash_returns_one(self, tmp_path: Path) -> None:
        repo, manifest = self._setup(tmp_path)
        # Mutate a tracked file.
        (repo / "data/processed/t/y.csv").write_bytes(b"corrupted!")
        assert M.verify(repo, manifest) == 1

    def test_extra_files_are_ignored(self, tmp_path: Path) -> None:
        """Files not in the manifest must NOT cause verification to fail."""
        repo, manifest = self._setup(tmp_path)
        # Add a brand-new file.
        (repo / "data/processed/t/extra.csv").write_bytes(b"new")
        assert M.verify(repo, manifest) == 0

    def test_missing_manifest_returns_two(self, tmp_path: Path) -> None:
        assert M.verify(tmp_path, tmp_path / "nope.sha256") == 2

    def test_malformed_manifest_returns_two(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.sha256"
        bad.write_text("not a valid manifest\n")
        assert M.verify(tmp_path, bad) == 2


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_generate_then_verify_roundtrip(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, {
            "data/simulated/t/x.parquet": b"v1",
            "data/processed/t/y.csv": b"v2",
        })
        manifest = tmp_path / "manifest.sha256"
        rc_gen = M.main([
            "--generate",
            "--repo-root", str(repo),
            "--out", str(manifest),
        ])
        assert rc_gen == 0

        rc_ver = M.main([
            "--verify",
            "--repo-root", str(repo),
            "--manifest", str(manifest),
        ])
        assert rc_ver == 0

    def test_rejects_both_generate_and_verify(self) -> None:
        with pytest.raises(SystemExit):
            M.main(["--generate", "--verify"])

    def test_rejects_neither_generate_nor_verify(self) -> None:
        with pytest.raises(SystemExit):
            M.main([])
