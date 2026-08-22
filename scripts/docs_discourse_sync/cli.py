#!/usr/bin/env python3
"""Sync ``docs/`` into Discourse "Doc Categories" at forum.hal0.dev.

Usage::

    # Offline structural check — no network, no Discourse credentials.
    # Discovers + transforms every doc and reports any TransformError.
    python -m scripts.docs_discourse_sync.cli --check

    # Plan a sync against the real forum without writing anything: every
    # topic is resolved by external_id (read-only) and diffed; nothing is
    # created, updated, or uploaded.
    DISCOURSE_URL=https://forum.hal0.dev \\
    DISCOURSE_API_KEY=... DISCOURSE_API_USERNAME=... DOCS_CATEGORY_ID=7 \\
    python -m scripts.docs_discourse_sync.cli --dry-run

    # The real thing.
    DISCOURSE_URL=... DISCOURSE_API_KEY=... DISCOURSE_API_USERNAME=... DOCS_CATEGORY_ID=7 \\
    python -m scripts.docs_discourse_sync.cli --assets-root ../hal0-web/public

Required env vars for anything but ``--check``: ``DISCOURSE_URL``,
``DISCOURSE_API_KEY``, ``DISCOURSE_API_USERNAME``, ``DOCS_CATEGORY_ID``
(the last can also be passed as ``--category-id``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import discovery, frontmatter, redirect_map, transform
from .discourse_client import DiscourseClient
from .sync import sync_docs

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--docs-dir",
        type=Path,
        default=_REPO_ROOT / "docs",
        help="docs/ root (default: repo docs/)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="discover + transform every doc and exit — no network, no Discourse env vars needed",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve topics and plan actions, but never create/update/upload",
    )
    ap.add_argument("--discourse-url", default=os.environ.get("DISCOURSE_URL"))
    ap.add_argument("--api-key", default=os.environ.get("DISCOURSE_API_KEY"))
    ap.add_argument("--api-username", default=os.environ.get("DISCOURSE_API_USERNAME"))
    ap.add_argument(
        "--category-id",
        type=int,
        default=int(os.environ["DOCS_CATEGORY_ID"]) if os.environ.get("DOCS_CATEGORY_ID") else None,
    )
    ap.add_argument(
        "--assets-root",
        type=Path,
        default=None,
        help="directory that resolves docs' /screenshots/*.png references "
        "(the hal0-web checkout's public/ — screenshots aren't tracked in this repo)",
    )
    ap.add_argument("--site-base-url", default="https://hal0.dev")
    ap.add_argument("--requests-per-minute", type=int, default=60)
    ap.add_argument(
        "--redirect-map-out",
        type=Path,
        default=_REPO_ROOT / "scripts" / "docs_discourse_sync" / "redirect-map.json",
    )
    return ap


def _discover_or_die(docs_dir: Path) -> list[discovery.Doc]:
    try:
        return discovery.discover_docs(docs_dir)
    except (
        transform.TransformError,
        frontmatter.FrontmatterError,
        discovery.DiscoveryError,
    ) as exc:
        print(f"docs-discourse-sync: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    docs = _discover_or_die(args.docs_dir)
    print(f"Discovered {len(docs)} doc(s) under {args.docs_dir}")
    for warning in (w for doc in docs for w in doc.warnings):
        print(f"  warning: {warning}")

    if args.check:
        for doc in sorted(docs, key=lambda d: d.external_id):
            print(f"  {doc.external_id}  <-  {doc.source_path.relative_to(_REPO_ROOT)}")
        return 0

    missing = [
        name
        for name, value in (
            ("--discourse-url/DISCOURSE_URL", args.discourse_url),
            ("--api-key/DISCOURSE_API_KEY", args.api_key),
            ("--api-username/DISCOURSE_API_USERNAME", args.api_username),
            ("--category-id/DOCS_CATEGORY_ID", args.category_id),
        )
        if not value
    ]
    if missing:
        print(
            f"docs-discourse-sync: missing required config: {', '.join(missing)}", file=sys.stderr
        )
        return 2

    with DiscourseClient(
        base_url=args.discourse_url,
        api_key=args.api_key,
        api_username=args.api_username,
        dry_run=args.dry_run,
        requests_per_minute=args.requests_per_minute,
    ) as client:
        report = sync_docs(
            docs,
            client=client,
            category_id=args.category_id,
            assets_root=args.assets_root,
            site_base_url=args.site_base_url,
        )

    for action in report.actions:
        prefix = "would " if args.dry_run and action.kind not in ("noop", "index-noop") else ""
        print(f"  {prefix}{action.kind:12} {action.external_id}  {action.detail}")
    for warning in report.warnings:
        print(f"  warning: {warning}")

    summary = {kind: report.count(kind) for kind in ("create", "update", "noop", "link-rewrite")}
    print(
        f"Docs — created {summary['create']}, updated {summary['update']}, "
        f"unchanged {summary['noop']}, link-rewrites {summary['link-rewrite']}"
    )
    idx_summary = {
        kind: report.count(kind) for kind in ("index-create", "index-update", "index-noop")
    }
    print(
        f"Index topics — created {idx_summary['index-create']}, "
        f"updated {idx_summary['index-update']}, unchanged {idx_summary['index-noop']}"
    )

    redirect_map.write_redirect_map(report.redirect_map, args.redirect_map_out)
    print(f"Redirect map ({len(report.redirect_map)} entries) written to {args.redirect_map_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
