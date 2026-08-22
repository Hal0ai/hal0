"""Tests for scripts/docs_discourse_sync/images.py.

Every screenshot hal0's docs reference lives in the *hal0-web* repo's
public/, not in hal0/docs/ itself — so "local" resolution always goes
through an explicit ``assets_root`` the caller points at that checkout,
and a missing file falls back to the live production URL rather than
failing the sync (see the module docstring for why).
"""

from __future__ import annotations

from pathlib import Path

from scripts.docs_discourse_sync import images


class _FakeUploader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def upload(self, path: Path) -> str:
        self.calls.append(path)
        return f"upload://{path.name}"


def test_image_found_under_assets_root_is_uploaded(tmp_path: Path) -> None:
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "foo.png").write_bytes(b"fake png")
    uploader = _FakeUploader()

    result = images.rewrite_images(
        "![alt text](/screenshots/foo.png)", uploader=uploader, assets_root=tmp_path
    )

    assert result.body_md == "![alt text](upload://foo.png)"
    assert result.uploaded == ["/screenshots/foo.png"]
    assert result.fallback_warnings == []
    assert len(uploader.calls) == 1


def test_missing_image_falls_back_to_production_url(tmp_path: Path) -> None:
    uploader = _FakeUploader()
    result = images.rewrite_images(
        "![alt](/screenshots/missing.png)", uploader=uploader, assets_root=tmp_path
    )
    assert result.body_md == "![alt](https://hal0.dev/screenshots/missing.png)"
    assert result.uploaded == []
    assert len(result.fallback_warnings) == 1
    assert "missing.png" in result.fallback_warnings[0]
    assert uploader.calls == []


def test_no_assets_root_always_falls_back(tmp_path: Path) -> None:
    uploader = _FakeUploader()
    result = images.rewrite_images(
        "![alt](/screenshots/foo.png)", uploader=uploader, assets_root=None
    )
    assert result.body_md == "![alt](https://hal0.dev/screenshots/foo.png)"
    assert uploader.calls == []


def test_repeated_image_reference_uploaded_only_once(tmp_path: Path) -> None:
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "foo.png").write_bytes(b"fake png")
    uploader = _FakeUploader()

    body = "![one](/screenshots/foo.png) and again ![two](/screenshots/foo.png)"
    result = images.rewrite_images(body, uploader=uploader, assets_root=tmp_path)

    assert result.body_md == "![one](upload://foo.png) and again ![two](upload://foo.png)"
    assert len(uploader.calls) == 1


def test_custom_site_base_url_used_in_fallback(tmp_path: Path) -> None:
    uploader = _FakeUploader()
    result = images.rewrite_images(
        "![alt](/screenshots/foo.png)",
        uploader=uploader,
        assets_root=None,
        site_base_url="https://staging.hal0.dev/",
    )
    assert result.body_md == "![alt](https://staging.hal0.dev/screenshots/foo.png)"


def test_non_image_markdown_is_untouched(tmp_path: Path) -> None:
    uploader = _FakeUploader()
    body = "[a link](/docs/concepts/slots) not an image."
    result = images.rewrite_images(body, uploader=uploader, assets_root=None)
    assert result.body_md == body
