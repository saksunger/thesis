"""Tests for scripts.fetch_zenodo_bundle (Phase 10 A5 - reviewer side).

Covers the host-routing, checksum-parsing, and integrity-check logic
without hitting the live Zenodo API. The download path is exercised
indirectly via the `--local` mode (no network) and the API-resolver via
a monkeypatched `urllib.request.urlopen`.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import fetch_zenodo_bundle as F


# ---------------------------------------------------------------------------
# _resolve_host
# ---------------------------------------------------------------------------


class TestResolveHost:
    def test_production_doi_routes_to_zenodo_org(self) -> None:
        assert F._resolve_host("10.5281/zenodo.123456") == F.ZENODO_PROD_HOST

    def test_sandbox_doi_routes_to_sandbox(self) -> None:
        assert F._resolve_host("10.5072/zenodo.509971") == F.ZENODO_SANDBOX_HOST

    def test_force_sandbox_overrides_prefix(self) -> None:
        assert (
            F._resolve_host("10.5281/zenodo.123", force_sandbox=True)
            == F.ZENODO_SANDBOX_HOST
        )

    def test_sandbox_url_in_doi_string_routes_to_sandbox(self) -> None:
        assert (
            F._resolve_host("https://sandbox.zenodo.org/records/509971")
            == F.ZENODO_SANDBOX_HOST
        )

    def test_unknown_prefix_defaults_to_production(self) -> None:
        assert F._resolve_host("10.9999/foo.bar") == F.ZENODO_PROD_HOST


# ---------------------------------------------------------------------------
# _parse_zenodo_checksum
# ---------------------------------------------------------------------------


class TestParseZenodoChecksum:
    def test_md5_parses_correctly(self) -> None:
        algo, hex_ = F._parse_zenodo_checksum(
            "md5:7192fdfce91b96223f8ede87afa539ff"
        )
        assert algo == "md5"
        assert hex_ == "7192fdfce91b96223f8ede87afa539ff"

    def test_sha256_parses_correctly(self) -> None:
        algo, hex_ = F._parse_zenodo_checksum(
            "sha256:" + "a" * 64
        )
        assert algo == "sha256"
        assert hex_ == "a" * 64

    def test_uppercase_hex_is_lowercased(self) -> None:
        _, hex_ = F._parse_zenodo_checksum("md5:" + "AB" * 16)
        assert hex_ == "ab" * 16

    def test_missing_colon_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised"):
            F._parse_zenodo_checksum("deadbeef")

    def test_unknown_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            F._parse_zenodo_checksum("crc32:deadbeef")

    def test_wrong_md5_length_raises(self) -> None:
        with pytest.raises(ValueError, match="unexpected md5"):
            F._parse_zenodo_checksum("md5:abc")

    def test_wrong_sha256_length_raises(self) -> None:
        with pytest.raises(ValueError, match="unexpected sha256"):
            F._parse_zenodo_checksum("sha256:abc")


# ---------------------------------------------------------------------------
# _hash_file (md5 + sha256 agree with hashlib over arbitrary content)
# ---------------------------------------------------------------------------


class TestHashFile:
    def test_md5_matches_hashlib(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        payload = b"thesis-bundle-reproducibility-check"
        path.write_bytes(payload)
        assert F._hash_file(path, "md5") == hashlib.md5(payload).hexdigest()

    def test_sha256_matches_hashlib(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        payload = b"thesis-bundle-reproducibility-check"
        path.write_bytes(payload)
        assert (
            F._hash_file(path, "sha256")
            == hashlib.sha256(payload).hexdigest()
        )


# ---------------------------------------------------------------------------
# _resolve_doi_to_files_api (mocked API response)
# ---------------------------------------------------------------------------


def _make_fake_urlopen(payload: dict):
    """Return a context-manager factory mimicking urlopen()'s shape."""

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            self.close()

    def _open(url, timeout=None):  # noqa: ANN001
        return _Resp(json.dumps(payload).encode("utf-8"))

    return _open


class TestResolveDoiToFilesApi:
    def test_picks_first_targz_and_returns_md5(self, monkeypatch) -> None:
        payload = {
            "files": [
                {
                    "key": "README.md",
                    "checksum": "md5:" + "0" * 32,
                    "links": {"self": "https://example/readme"},
                },
                {
                    "key": "thesis_artifacts_v1.0.0-rc1.tar.gz",
                    "checksum": "md5:" + "1" * 32,
                    "links": {
                        "self": "https://sandbox.zenodo.org/api/records/509971/files/thesis_artifacts_v1.0.0-rc1.tar.gz/content"
                    },
                },
            ]
        }
        monkeypatch.setattr(
            F.urllib.request, "urlopen", _make_fake_urlopen(payload),
        )
        url, algo, hex_ = F._resolve_doi_to_files_api("10.5072/zenodo.509971")
        assert algo == "md5"
        assert hex_ == "1" * 32
        assert "509971" in url

    def test_picks_sha256_when_zenodo_returns_it(self, monkeypatch) -> None:
        payload = {
            "files": [
                {
                    "key": "thesis.tar.gz",
                    "checksum": "sha256:" + "f" * 64,
                    "links": {"self": "https://example/thesis"},
                },
            ]
        }
        monkeypatch.setattr(
            F.urllib.request, "urlopen", _make_fake_urlopen(payload),
        )
        url, algo, hex_ = F._resolve_doi_to_files_api("10.5281/zenodo.123456")
        assert algo == "sha256"
        assert hex_ == "f" * 64

    def test_missing_tarball_raises(self, monkeypatch) -> None:
        payload = {
            "files": [
                {
                    "key": "README.md",
                    "checksum": "md5:" + "0" * 32,
                    "links": {"self": "https://example/readme"},
                },
            ]
        }
        monkeypatch.setattr(
            F.urllib.request, "urlopen", _make_fake_urlopen(payload),
        )
        with pytest.raises(RuntimeError, match="no .tar.gz"):
            F._resolve_doi_to_files_api("10.5072/zenodo.509971")

    def test_invalid_doi_raises(self) -> None:
        with pytest.raises(ValueError, match="DOI must be"):
            F._resolve_doi_to_files_api("not-a-doi")


# ---------------------------------------------------------------------------
# End-to-end --local round-trip (no network)
# ---------------------------------------------------------------------------


def _build_tiny_tarball(tmp_path: Path) -> tuple[Path, str, str]:
    """Build a 2-file tar.gz, returning (path, md5_hex, sha256_hex)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "data").mkdir()
    (src / "data" / "hello.txt").write_text("hello\n")
    (src / "data" / "world.txt").write_text("world\n")
    tar_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(tar_path, mode="w:gz") as tar:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(src)))
    blob = tar_path.read_bytes()
    return (
        tar_path,
        hashlib.md5(blob).hexdigest(),
        hashlib.sha256(blob).hexdigest(),
    )


class TestLocalRoundTrip:
    def test_local_with_correct_sha256_unpacks_cleanly(
        self, tmp_path: Path, capsys
    ) -> None:
        tar_path, _md5, sha = _build_tiny_tarball(tmp_path)
        extract_root = tmp_path / "extracted"
        extract_root.mkdir()
        rc = F.main(
            [
                "--local", str(tar_path),
                "--sha256", sha,
                "--repo-root", str(extract_root),
            ]
        )
        assert rc == 0
        assert (extract_root / "data" / "hello.txt").read_text() == "hello\n"
        assert (extract_root / "data" / "world.txt").read_text() == "world\n"

    def test_local_with_wrong_sha256_fails_loudly(
        self, tmp_path: Path
    ) -> None:
        tar_path, *_ = _build_tiny_tarball(tmp_path)
        extract_root = tmp_path / "extracted"
        extract_root.mkdir()
        rc = F.main(
            [
                "--local", str(tar_path),
                "--sha256", "deadbeef" * 8,
                "--repo-root", str(extract_root),
            ]
        )
        assert rc == 1
        assert not (extract_root / "data" / "hello.txt").exists()

    def test_local_without_sha256_warns_and_unpacks(
        self, tmp_path: Path, capsys
    ) -> None:
        tar_path, *_ = _build_tiny_tarball(tmp_path)
        extract_root = tmp_path / "extracted"
        extract_root.mkdir()
        rc = F.main(
            [
                "--local", str(tar_path),
                "--repo-root", str(extract_root),
            ]
        )
        assert rc == 0
        assert (extract_root / "data" / "hello.txt").exists()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
