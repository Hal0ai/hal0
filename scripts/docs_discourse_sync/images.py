"""Resolve and upload the images a doc references, rewriting them to
Discourse ``upload://`` short-URLs.

Every screenshot in hal0's docs is referenced by a site-root path
(``![...](/screenshots/foo.png)``) but the image *file* lives in the
sibling ``hal0-web`` repo's ``public/`` directory, not in this one —
hal0/docs is text-only. ``--assets-root`` therefore has to be pointed at
wherever that checkout is; a doc's image is never "local" to the hal0 repo
itself. When the file can't be found there (no sibling checkout, wrong
path, image renamed), this falls back to linking the live production URL
instead of failing the sync — a docs sync shouldn't hard-fail over one
missing screenshot when the image is already being served at
https://hal0.dev, and the fallback is logged as a warning so it's visible
in `--dry-run` output rather than silently swapped in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>/[^()\s]+)\)")
_DEFAULT_SITE_BASE_URL = "https://hal0.dev"


@dataclass(slots=True)
class ImageRewriteResult:
    body_md: str
    uploaded: list[str]
    fallback_warnings: list[str]


class ImageUploader:
    """Protocol-ish seam so :mod:`sync` can inject the real
    :class:`discourse_client.DiscourseClient` or a test double."""

    def upload(self, path: Path) -> str:  # pragma: no cover - interface
        raise NotImplementedError


def rewrite_images(
    body_md: str,
    *,
    uploader: ImageUploader,
    assets_root: Path | None,
    site_base_url: str = _DEFAULT_SITE_BASE_URL,
) -> ImageRewriteResult:
    uploaded: list[str] = []
    fallback_warnings: list[str] = []
    # Cache within one doc: the same screenshot is occasionally referenced
    # twice (e.g. a hero image up top and a caption crop later).
    resolved: dict[str, str] = {}

    def _sub(m: re.Match[str]) -> str:
        url = m.group("url")
        if url in resolved:
            return f"![{m.group('alt')}]({resolved[url]})"

        local_path = (assets_root / url.lstrip("/")) if assets_root else None
        if local_path is not None and local_path.is_file():
            short_url = uploader.upload(local_path)
            uploaded.append(url)
        else:
            short_url = f"{site_base_url.rstrip('/')}{url}"
            fallback_warnings.append(
                f"image {url} not found under assets-root "
                f"({assets_root or '<none given>'}) — linked to {short_url} instead of uploading"
            )
        resolved[url] = short_url
        return f"![{m.group('alt')}]({short_url})"

    new_body = _IMAGE_RE.sub(_sub, body_md)
    return ImageRewriteResult(
        body_md=new_body, uploaded=uploaded, fallback_warnings=fallback_warnings
    )
