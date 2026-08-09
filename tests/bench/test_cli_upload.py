"""CLI wiring for `hal0 bench upload` — monkeypatches the upload module's
client function (the HTTP path is covered for real in test_upload.py)."""

from __future__ import annotations

import pytest

from hal0.bench.cli import main


@pytest.fixture
def bundle_file(tmp_path):
    p = tmp_path / "b.hal0bench.tar.gz"
    p.write_bytes(b"\x1f\x8bdata")
    return p


def test_upload_prints_url(bundle_file, monkeypatch, capsys):
    import hal0.bench.upload as up

    monkeypatch.setattr(
        up, "upload_bundle", lambda path, api=None, token=None: {"url": "https://hal0.dev/x"}
    )
    rc = main(["upload", str(bundle_file)])
    assert rc == 0
    assert "https://hal0.dev/x" in capsys.readouterr().out


def test_upload_error_exits_1(bundle_file, monkeypatch, capsys):
    import hal0.bench.upload as up

    def _boom(path, api=None, token=None):
        raise up.UploadError("upload rejected (422): ['bad hash']", status=422)

    monkeypatch.setattr(up, "upload_bundle", _boom)
    rc = main(["upload", str(bundle_file)])
    assert rc == 1
    assert "bad hash" in capsys.readouterr().err


def test_upload_missing_file_exits_1(tmp_path, capsys):
    rc = main(["upload", str(tmp_path / "nope.tar.gz")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
