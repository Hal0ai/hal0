"""hal0 docs -> Discourse "Doc Categories" one-way publisher.

``docs/`` in this repo is canonical; this package renders the published
sections (getting-started, concepts, guides, operate, reference[/api]) to
Discourse-flavoured markdown and syncs them into forum.hal0.dev as topics
in a Discourse doc category, keyed by a stable ``external_id`` derived from
each file's path. Modeled on discourse/discourse-developer-docs' own
``sync_docs`` script (same external_id-as-idempotency-key pattern, same
dry-run-guards-writes shape), adapted for MDX/Starlight source input instead
of plain markdown, and for hal0's own multi-section IA instead of one flat
docs collection.

Entry point: ``python -m scripts.docs_discourse_sync.cli`` (see cli.py for
flags; ``--dry-run`` never calls a mutating endpoint).
"""

from __future__ import annotations
