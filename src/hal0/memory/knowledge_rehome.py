"""``hal0 memory knowledge rehome``: put a folded bank's unplaced pages in a project folder.

The fold-in (:mod:`hal0.memory.fold_in`) places a coding bank's knowledge
pages in their project's folder by each page's own ``project:`` tag when the
bank's project comes from tags (``Hal0::Agents``, ``pi-coder``, ``--map``). A
page that names no project stays at the root of the platform bank, under the
id :func:`~hal0.memory.knowledge_pages.folded_node_id` gave it, unstamped, and
``/api/memory/compat`` never shows it. This command finishes that job for one
old bank and one project named by the operator, live, after the cutover:

* it finds the bank's nodes in the platform bank from the old bank's own tree
  (the old bank stays on the engine) and the fold-in's deterministic ids: a
  node's new id is ``folded_node_id(bank, old id, kind)``, a page's model
  ``<bank slug>-<old model id>`` (:func:`~hal0.memory.fold_in.model_id_for`).
  Only a subtree outside every ``project:`` folder moves;
* it finds the project's folder as compat does, the first root folder named
  ``project:<slug>`` in id order, and creates it as compat does when there is
  none (:meth:`~hal0.memory.fold_in.FoldIn._project_folder`);
* it stamps every page of the bank as the fold-in stamps a placed page
  (:func:`~hal0.memory.knowledge_pages.page_tags`,
  :func:`~hal0.memory.knowledge_pages.page_trigger`), clears what it holds
  (``POST .../mental-models/{id}/clear``) unless ``--keep-content``, and then
  moves each subtree under the folder with one ``PATCH
  .../knowledge-base/nodes/{id}`` that sets ``parent_id`` (the engine moves
  the subtree with its root: children keep their ``parent_id``). A top-level
  page whose name the folder holds already is renamed ``<name> (<bank>)`` in
  the same PATCH (:func:`~hal0.memory.fold_in.renamed_page_name`);
* it records old id -> new id in the fold-in's page map, so compat answers an
  old id and an old ``[[page:<id>]]`` link for the page it became, and checks
  every node with the fold-in's placement check.

The order keeps compat fail-closed: a page is stamped and emptied while its
subtree is still outside the project folder, where compat does not look, and
the move comes last. A run that stops part-way is completed by the next one,
and a run after a complete one changes nothing.

Like the fold-in, it runs on the box and talks to the engine directly; the
dry run writes nothing and ``--apply`` needs root (the page map is root's).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

import structlog

from hal0.memory import fold_in as fi
from hal0.memory.engine_maintenance import bank_path

log = structlog.get_logger(__name__)

COMMAND = "hal0 memory knowledge rehome"

#: What a kept page's content is, in the report: written under the old trigger.
KEPT_CONTENT_NOTE = (
    "kept content was written under the page's old trigger, which read the whole old bank "
    "with no project scope: it may include non-private facts of other projects from that bank"
)

_PAGE_LINK = re.compile(r"\[\[page:([^\]\s]+)\]\]")


class RehomeRefused(fi.FoldInRefused):
    """Refused before anything was changed; ``plan`` is the report."""


def _links_to(content: Any, old_ids: Iterable[str]) -> int:
    """How many ``[[page:<id>]]`` links in ``content`` name one of ``old_ids``."""
    wanted = set(old_ids)
    if not isinstance(content, str):
        return 0
    return sum(1 for m in _PAGE_LINK.finditer(content) if m.group(1) in wanted)


def _same_tags(have: Any, want: list[str]) -> bool:
    return sorted(str(t) for t in have or ()) == sorted(want)


class KnowledgeRehome(fi.FoldIn):
    """One rehome of bank ``bank``'s unplaced knowledge pages into project ``project``.

    A :class:`~hal0.memory.fold_in.FoldIn` for its engine session, its seams
    (``memory_config``, ``layout``, ``known_clients``, ``privileged``) and
    the helpers it shares with the fold-in: the tree reads, the project
    folder, the page names of the project folders, the rewrite plan the bank
    was folded with, and the placement check. It never isolates the engine
    and never pauses writes.
    """

    def __init__(
        self,
        client: Any,
        *,
        bank: str,
        project: str,
        owner: str | None = None,
        keep_content: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(client, **kwargs)
        self._bank = bank
        self._project = project
        self._owner = owner.strip() if isinstance(owner, str) and owner.strip() else None
        self._keep = keep_content

    def _refusal(self, message: str) -> fi.FoldInError:
        return RehomeRefused(message)

    # ── the plan ─────────────────────────────────────────────────────────

    async def plan(self, *, apply: bool = False) -> dict[str, Any]:
        """What a run would do, read without changing anything. ``refusals`` say why not."""
        report: dict[str, Any] = {
            "command": COMMAND,
            "bank": self._bank,
            "project": self._project,
            "applied": False,
            "refusals": [],
        }
        refusals: list[dict[str, str]] = report["refusals"]

        def refuse(code: str, message: str) -> dict[str, Any]:
            refusals.append({"code": code, "message": message})
            return report

        from hal0.bridge.keys import is_reserved_name, valid_name
        from hal0.memory import write_gate
        from hal0.memory.knowledge_pages import (
            KNOWLEDGE_FOLDER_PREFIX,
            caller_trigger_settings,
            folded_node_id,
            page_map_path,
            page_tags,
            page_trigger,
            project_folder_name,
            project_folders,
            trigger_scope_held,
        )
        from hal0.memory.projects import registry_from_config, resolve_project

        if self._owner is not None and (
            not valid_name(self._owner) or is_reserved_name(self._owner)
        ):
            refuse(
                "invalid_owner",
                f"--owner {self._owner}: not a name a bridge client can have, so no client "
                "could change a page filed under it",
            )
        if apply and not self._privileged():
            refuse("not_privileged", "run this as root (sudo): it writes the page map")
        blocked = fi.blocking_reason()
        from hal0.memory import embedder_switch

        blocked = blocked or embedder_switch.blocking_reason()
        if blocked is not None:
            return refuse("maintenance", blocked)
        gate = write_gate.read_gate()
        if gate is not None:
            return refuse(
                "writes_paused",
                f"memory writes are paused ({gate.get('reason')}); run this once they are open",
            )

        memory_cfg = self._memory_config()
        layout = self._current_layout(memory_cfg)
        report["layout"] = layout.layout
        if not layout.two_bank:
            return refuse(
                "not_two_bank",
                "the layout record does not say two_bank: run `hal0 memory migrate fold-in` "
                "to the cutover first",
            )
        target = str(layout.provider_kwargs(memory_cfg)["bank"])
        report["target"] = target

        registry = registry_from_config(getattr(memory_cfg, "projects", None))
        resolution = resolve_project(field=self._project, registry=registry, coding=True)
        slug = resolution.slug
        if slug is None:
            return refuse(
                "project_unknown",
                f"--project {self._project}: not a project this box resolves (denied, or not "
                "a project name)",
            )
        report["project"] = slug
        folder_name = project_folder_name(slug)

        ledger = fi.load_ledger()
        row = (ledger.get("banks") or {}).get(self._bank)
        if not isinstance(row, dict) or row.get("state") != fi.LEDGER_FOLDED:
            return refuse(
                "not_folded",
                f"the fold-in ledger does not record {self._bank} as folded; only a folded "
                "bank's pages are in the platform bank",
            )
        cls = fi.classify_bank(
            self._bank,
            platform_bank=str(ledger.get("platform_bank") or ""),
            targets=[str(t) for t in (ledger.get("targets") or {}).values()],
            maps=ledger.get("maps") or {},
        )
        if not cls.coding:
            return refuse(
                "not_coding_bank",
                f"{self._bank} is a {cls.cls} bank; only a coding bank's pages go to a project "
                "folder",
            )
        on_engine = set(await self._bank_ids())
        if self._bank not in on_engine:
            return refuse(
                "bank_gone",
                f"{self._bank} is no longer on the engine; its tree is how its pages are found",
            )
        if target not in on_engine:
            return refuse("target_missing", f"the platform bank {target} is not on the engine")

        plan = self._rewrite_plan(cls, None, frozen=row)
        prefix = fi._model_prefix(cls)
        old_roots = await self._tree_roots(self._bank)
        old_index = await self._tree_index(self._bank)
        old_models = {
            str(m.get("id")): m
            for m in await self._list_all(
                f"{bank_path(self._bank)}/mental-models",
                key="items",
                what="reading mental models",
                bank=self._bank,
            )
            if isinstance(m, dict)
        }
        target_roots = await self._tree_roots(target)
        index = await self._tree_index(target)

        def new_id(old: str) -> str:
            node = old_index[old][0]
            return folded_node_id(self._bank, old, str(node.get("kind")))

        ours = {new_id(old) for old in old_index}

        # Every node of the old tree, in the target under the fold-in's id.
        missing: list[str] = []
        mismatched: list[str] = []
        for old, (node, _root) in sorted(old_index.items()):
            got = index.get(new_id(old))
            if got is None:
                missing.append(old)
                continue
            if node.get("kind") == "page":
                expected = fi.model_id_for(prefix, str(node.get("mental_model_id")))
                if prefix is None or got[0].get("mental_model_id") != expected:
                    mismatched.append(old)
        if missing:
            refuse(
                "nodes_missing",
                f"{len(missing)} node(s) of {self._bank}'s tree are not in {target} under the "
                "fold-in's ids: " + ", ".join(missing[:10]),
            )
        if mismatched:
            refuse(
                "model_mismatch",
                f"{len(mismatched)} page(s) in {target} are not backed by the model the fold-in "
                "gave them: " + ", ".join(mismatched[:10]),
            )
        if missing or mismatched:
            return report

        # Each subtree at the old bank's root: placed already, to move, or another project's.
        folders = project_folders(target_roots, slug)
        folder_id = str(folders[0]["id"]) if folders else None
        report["folder"] = {
            "name": folder_name,
            "id": folder_id,
            "exists": folder_id is not None,
        }
        tops: list[dict[str, Any]] = []
        for root in old_roots:
            if not isinstance(root, dict) or not isinstance(root.get("id"), str):
                continue
            old = root["id"]
            nid = new_id(old)
            _node, where = index[nid]
            holder = index.get(where, ({}, where))[0]
            holder_name = str(holder.get("name") or "")
            in_project = (
                where != nid
                and holder.get("kind") == "folder"
                and holder_name.startswith(KNOWLEDGE_FOLDER_PREFIX)
            )
            if in_project and holder_name != folder_name:
                refuse(
                    "other_project",
                    f"{root.get('name')!r} ({nid}) is already in {holder_name}, not {folder_name}; "
                    "rehome does not move a subtree out of another project's folder, so nothing "
                    "is changed",
                )
            tops.append(
                {
                    "old_id": old,
                    "id": nid,
                    "kind": str(root.get("kind")),
                    "name": str(root.get("name") or ""),
                    "state": "placed" if in_project else "move",
                }
            )
        if any(r["code"] == "other_project" for r in refusals):
            return report

        # The page-name rule of the fold-in (``_settle_page_names``): a name another
        # page already holds in the project's folders keeps it, ours is renamed.
        present = await self._project_page_names(target)
        others = {lname for (s, lname), pid in present.items() if s == slug and pid not in ours}
        renames: list[dict[str, str]] = []
        final: dict[str, str] = {}
        conflicts: list[str] = []
        top_pages = [t for t in tops if t["kind"] == "page"]
        plain = [t["name"].lower() for t in top_pages]
        for top in top_pages:
            lname = top["name"].lower()
            if plain.count(lname) > 1:
                conflicts.append(f"{folder_name}/{lname}")
                continue
            if lname in others:
                shown = fi.renamed_page_name(top["name"], self._bank)
                final[top["id"]] = shown
                renames.append({"id": top["id"], "from": top["name"], "to": shown})
            else:
                final[top["id"]] = top["name"]
        taken = set(others)
        for top in top_pages:
            shown = final.get(top["id"])
            if shown is None:
                continue
            if shown.lower() in taken:
                conflicts.append(f"{folder_name}/{shown.lower()}")
            taken.add(shown.lower())
        for top in tops:
            top["shown_name"] = final.get(top["id"], top["name"])
        report["renames"] = renames
        if conflicts:
            refuse(
                "page_name_conflict",
                f"{len(conflicts)} page name(s) cannot be given a place of their own in "
                f"{folder_name} (two of {self._bank}'s own pages share one, or the renamed name "
                "is taken too): " + ", ".join(sorted(set(conflicts))),
            )

        # Every page of every subtree: its owner, labels and trigger, as the fold-in
        # stamps a placed page, from the OLD bank's model (stable across runs).
        moving = {t["old_id"] for t in tops if t["state"] == "move"}
        pages: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        owners: dict[str, int] = {}
        pseudo: list[str] = []
        for old, (node, root) in sorted(old_index.items()):
            nid = new_id(old)
            entry = {
                "old_id": old,
                "id": nid,
                "kind": str(node.get("kind")),
                "name": str(node.get("name") or ""),
                "top": old == root,
                "subtree_moving": root in moving,
            }
            nodes.append(entry)
            if node.get("kind") != "page":
                continue
            model = old_models.get(str(node.get("mental_model_id"))) or {}
            tags = [str(t) for t in model.get("tags") or () if isinstance(t, str)]
            owner = self._owner or fi._page_owner(plan, tags)
            owners[owner] = owners.get(owner, 0) + 1
            if owner.startswith(fi.PSEUDO_OWNER_PREFIX):
                pseudo.append(entry["name"])
            labels, leaf = page_tags(fi._caller_tags(tags), agent=owner, resolution=resolution)
            trigger = page_trigger(
                caller_trigger_settings(fi._decoded(model.get("trigger"))), leaf, slug
            )
            have = index[nid][0]
            stamped = _same_tags(have.get("tags"), labels) and trigger_scope_held(
                fi._decoded(have.get("trigger")), slug
            )
            entry.update(
                {
                    "model_id": str(have.get("mental_model_id")),
                    "owner": owner,
                    "tags": labels,
                    "trigger": trigger,
                    "stamped": stamped,
                    # Emptied while it is still out of compat's sight: every page of a
                    # subtree not moved yet, and a placed page that lost its stamp.
                    "clear": not self._keep and (entry["subtree_moving"] or not stamped),
                }
            )
            pages.append(entry)

        # Private facts as the fold-in counts them (a document routed to the private
        # bank), from the old bank's listings: the dry run says whether keeping is allowed.
        analysis = await self._analyse(cls, row)
        private_facts = int(analysis.get("private_facts") or 0)
        keep_allowed = private_facts == 0
        report["keep_content"] = {
            "requested": self._keep,
            "allowed": keep_allowed,
            "private_facts": private_facts,
            "why": (
                f"{self._bank} holds no private facts, so its pages' content rests on shared "
                f"facts only; {KEPT_CONTENT_NOTE}"
                if keep_allowed
                else f"{self._bank} holds {private_facts} private fact(s): a page built from "
                "the whole bank may rest on them, so its content is cleared"
            ),
        }
        if self._keep and not keep_allowed:
            refuse(
                "keep_content_private",
                f"--keep-content: {self._bank} holds {private_facts} private fact(s), which its "
                "pages' content may rest on; run without --keep-content",
            )
        links_left = 0
        if self._keep:
            target_models = {
                str(m.get("id")): m
                for m in await self._list_all(
                    f"{bank_path(target)}/mental-models",
                    {"detail": "content"},
                    key="items",
                    what="reading mental models",
                    bank=target,
                )
                if isinstance(m, dict)
            }
            links_left = sum(
                _links_to((target_models.get(p["model_id"]) or {}).get("content"), old_index)
                for p in pages
            )
            report["keep_content"]["note"] = KEPT_CONTENT_NOTE
        report["keep_content"]["old_links"] = links_left

        top_ids = {t["old_id"] for t in tops}
        report["nodes"] = {
            "total": len(nodes),
            "pages": len(pages),
            "folders": len(nodes) - len(pages),
            "top_level": len(top_ids),
            "to_move": sum(1 for t in tops if t["state"] == "move"),
            "already_placed": sum(1 for t in tops if t["state"] == "placed"),
            "in_moving_subtrees": sum(1 for n in nodes if n["subtree_moving"]),
        }
        report["tops"] = tops
        report["owner"] = {
            "given": self._owner,
            "owners": owners,
            "pseudo_owned": pseudo,
        }
        report["stamp"] = sum(1 for p in pages if not p["stamped"] or p["subtree_moving"])
        report["content"] = {
            "cleared": 0 if self._keep else sum(1 for p in pages if p["clear"]),
            "kept": self._keep,
        }
        rows = {
            n["old_id"]: {
                "id": n["id"],
                "kind": n["kind"],
                # Where it will be: the folder it moves to, or the one it is in.
                "folder": folder_id if n["subtree_moving"] else index[n["id"]][1],
                "project": slug,
                "bank": self._bank,
            }
            for n in nodes
        }
        try:
            mapped = fi_page_map_rows(target).items()
        except (OSError, ValueError) as exc:
            return refuse("page_map_unreadable", f"the page map cannot be read: {exc}")
        current = {k: v for k, v in mapped if k in rows}
        report["page_map"] = {
            "path": str(page_map_path()),
            "rows": len(rows),
            "to_write": sum(1 for k, v in rows.items() if folder_id is None or current.get(k) != v),
        }
        report["changes"] = (
            report["nodes"]["to_move"]
            + report["stamp"]
            + report["content"]["cleared"]
            + report["page_map"]["to_write"]
            + (0 if folder_id else 1)
        )
        # Kept on the instance for :meth:`rehome`; not part of the report.
        self._work = {
            "target": target,
            "slug": slug,
            "tops": tops,
            "pages": pages,
            "nodes": nodes,
        }
        return report

    # ── the run ──────────────────────────────────────────────────────────

    async def rehome(self) -> dict[str, Any]:
        """Do it: stamp and empty, move, map and check. Refused, changing nothing, as :meth:`plan` says."""
        report = await self.plan(apply=True)
        if report["refusals"]:
            raise RehomeRefused(report["refusals"][0]["message"], plan=report)
        report["applied"] = True
        if not report["changes"]:
            report["changed"] = False
            report["verified"] = await self._verify(report)
            return report
        work = self._work
        target, slug = work["target"], work["slug"]
        created = False
        folder_id = report["folder"]["id"]
        if folder_id is None:
            folder_id = await self._project_folder(self._bank, target, slug)
            created = True
        report["folder"].update({"id": folder_id, "created": created})

        nodes = f"{bank_path(target)}/knowledge-base/nodes"
        stamped = cleared = moved = 0
        # 1. Empty what was built under the old trigger first, so a page is never
        #    shown scoped but still holding content built under its old trigger.
        for page in work["pages"]:
            if not page["clear"]:
                continue
            await self._json(
                "POST",
                f"{bank_path(target)}/mental-models/{page['model_id']}/clear",
                what="clearing a knowledge page",
                bank=target,
            )
            cleared += 1
        # 2. Stamp every page that needs it, where compat cannot see it yet (its
        #    subtree is outside the folder) or cannot see it anyway (no stamp).
        for page in work["pages"]:
            if page["stamped"] and not page["subtree_moving"]:
                continue
            await self._json(
                "PATCH",
                f"{nodes}/{page['id']}",
                body={"tags": page["tags"], "trigger": page["trigger"]},
                what="stamping a knowledge page",
                bank=target,
            )
            stamped += 1
        # 3. Move each subtree with its root, renamed where the folder holds its name.
        for top in work["tops"]:
            if top["state"] != "move":
                continue
            body: dict[str, Any] = {"parent_id": folder_id}
            if top["kind"] == "page" and top["shown_name"] != top["name"]:
                body["name"] = top["shown_name"]
            await self._json(
                "PATCH",
                f"{nodes}/{top['id']}",
                body=body,
                what="moving a knowledge-base node",
                bank=target,
            )
            moved += 1
        report["done"] = {"stamped": stamped, "cleared": cleared, "moved": moved}
        self._say(
            f"{moved} node(s) moved into {report['folder']['name']}, {stamped} page(s) stamped, "
            f"{cleared} emptied"
        )
        report["page_map"]["written"] = await self._map(target, slug)
        report["changed"] = True
        report["verified"] = await self._verify(report)
        return report

    async def _map(self, target: str, slug: str) -> int:
        """Record every node of the bank in the page map, read back from the tree. Rows written."""
        index = await self._tree_index(target)
        rows: dict[str, dict[str, Any]] = {}
        for node in self._work["nodes"]:
            got = index.get(node["id"])
            if got is None:
                continue
            rows[node["old_id"]] = {
                "id": node["id"],
                "kind": node["kind"],
                "folder": got[1],
                "project": slug,
                "bank": self._bank,
            }
        current = fi_page_map_rows(target)
        changed = {k: v for k, v in rows.items() if current.get(k) != v}
        if changed:
            fi.record_page_map(target, changed)
        return len(changed)

    async def _verify(self, report: dict[str, Any]) -> bool:
        """The fold-in's placement check over every node; raises when one is not in place."""
        work = self._work
        placed = {
            n["old_id"]: {"id": n["id"], "kind": n["kind"], "project": work["slug"]}
            for n in work["nodes"]
        }
        missing, misplaced = await self._placement_problems(work["target"], placed)
        # Beyond the fold-in's check, which reads the trigger: compat also shows a
        # page only when its tags pass the caller's project scope
        # (:func:`~hal0.memory.knowledge_pages.page_scope_held`), so that is read
        # back too, by the read rule itself rather than by the stamp just sent.
        from hal0.memory.read_scope import project_scope_groups, tags_readable

        index = await self._tree_index(work["target"])
        groups = project_scope_groups(work["slug"])
        for page in work["pages"]:
            got = index.get(page["id"])
            if got is not None and not tags_readable(groups, got[0].get("tags")):
                misplaced = sorted({*misplaced, page["id"]})
        if missing or misplaced:
            raise fi.FoldInError(
                f"after the rehome, {len(missing)} node(s) are missing from {work['target']} and "
                f"{len(misplaced)} are not in {report['folder']['name']} with its scope: "
                + ", ".join([*missing, *misplaced][:10])
                + f". Run `sudo {COMMAND} --bank {self._bank} --project {work['slug']} --apply` "
                "again",
                bank=self._bank,
                details={"missing": missing, "misplaced": misplaced},
            )
        return True


def fi_page_map_rows(target: str) -> Mapping[str, Mapping[str, Any]]:
    """``target``'s rows of the page map, as on disk. Raises when the map cannot be read."""
    from hal0.memory.knowledge_pages import load_page_map

    rows = (load_page_map().get("targets") or {}).get(target)
    return rows if isinstance(rows, dict) else {}


__all__ = ["COMMAND", "KEPT_CONTENT_NOTE", "KnowledgeRehome", "RehomeRefused"]
