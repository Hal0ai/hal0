// Memory v2 web graph — client-side filter dimming against the REAL /graph
// wire shape (#1996).
//
// `/api/memory/banks/{bank}/graph` is a verbatim hindsight-api passthrough
// (src/hal0/api/routes/memory_admin.py `_FORWARDS`). Upstream 0.9.2
// (`MemoryEngine.get_graph_data`) emits each node as
// `{data: {id, label, text, date, context, entities, color}}` — no `type`,
// no `topic`, no `tags`. A unit's fact type and tags live only in the
// sibling `table_rows` list, keyed by the same id. The payload below is
// shaped exactly like that response; the web view used to dim on
// `node.data.topic` / `node.data.type`, which only the mock provided, so on
// a real bank any tag or type filter dimmed every node.
import React from "react";
import { describe, expect, it } from "vitest";

// Same global-React-before-dynamic-import dance as the salience test.
(globalThis as unknown as { React: typeof React }).React = React;
const { buildNodeFacets, matchesWebFilters } =
  await import("../memory-web-graph.jsx");

// Shaped like hindsight-api 0.9.2 GET /v1/default/banks/{bank}/graph.
const REAL_GRAPH = {
  nodes: [
    {
      data: {
        id: "u-infra",
        label: "Proxmox host runs the k3s cl...",
        text: "Proxmox host runs the k3s cluster",
        date: "2026-08-01T10:00:00+00:00",
        context: "ops chat",
        entities: "Proxmox, k3s",
        color: "#42a5f5",
      },
    },
    {
      data: {
        id: "u-music",
        label: "Likes ambient music",
        text: "Likes ambient music",
        date: "2026-08-02T10:00:00+00:00",
        context: "",
        entities: "None",
        color: "#e0e0e0",
      },
    },
    {
      data: {
        id: "u-untagged",
        label: "Noticed backups run nightly",
        text: "Noticed backups run nightly",
        date: "",
        context: "",
        entities: "None",
        color: "#e0e0e0",
      },
    },
  ],
  edges: [
    {
      data: {
        id: "u-infra-u-music-semantic",
        source: "u-infra",
        target: "u-music",
        linkType: "semantic",
        weight: 0.8,
        entityName: "",
        color: "#ff69b4",
        lineStyle: "solid",
      },
    },
  ],
  table_rows: [
    {
      id: "u-infra",
      text: "Proxmox host runs the k3s cluster",
      context: "ops chat",
      occurred_start: null,
      occurred_end: null,
      mentioned_at: "2026-08-01T10:00:00+00:00",
      date: "2026-08-01 10:00",
      entities: "Proxmox, k3s",
      document_id: "doc-1",
      chunk_id: null,
      fact_type: "world",
      tags: ["infra", "homelab"],
      created_at: "2026-08-01T10:00:00+00:00",
      proof_count: null,
    },
    {
      id: "u-music",
      text: "Likes ambient music",
      context: "N/A",
      occurred_start: null,
      occurred_end: null,
      mentioned_at: "2026-08-02T10:00:00+00:00",
      date: "2026-08-02 10:00",
      entities: "None",
      document_id: null,
      chunk_id: null,
      fact_type: "experience",
      tags: ["music"],
      created_at: "2026-08-02T10:00:00+00:00",
      proof_count: null,
    },
    {
      id: "u-untagged",
      text: "Noticed backups run nightly",
      context: "N/A",
      occurred_start: null,
      occurred_end: null,
      mentioned_at: null,
      date: "N/A",
      entities: "None",
      document_id: null,
      chunk_id: null,
      fact_type: "observation",
      tags: [],
      created_at: "2026-08-03T10:00:00+00:00",
      proof_count: 2,
    },
  ],
  total_units: 3,
  limit: 500,
};

function lit(filters: Record<string, unknown>) {
  const facets = buildNodeFacets(REAL_GRAPH);
  return REAL_GRAPH.nodes
    .filter((n) => matchesWebFilters(n, facets, filters))
    .map((n) => n.data.id);
}

describe("memory web graph filter dimming on the real /graph payload (#1996)", () => {
  it("reads fact type and tags from table_rows, keyed by node id", () => {
    const facets = buildNodeFacets(REAL_GRAPH);
    expect(facets.get("u-infra")).toEqual({
      type: "world",
      tags: ["infra", "homelab"],
    });
    expect(facets.get("u-untagged")).toEqual({ type: "observation", tags: [] });
  });

  it("a tag chip keeps the matching nodes lit instead of dimming the whole graph", () => {
    expect(lit({ tags: ["infra"] })).toEqual(["u-infra"]);
    expect(lit({ tags: ["music"] })).toEqual(["u-music"]);
  });

  it("a fact-type toggle keeps nodes of the active types lit", () => {
    expect(lit({ type: "world,observation" })).toEqual([
      "u-infra",
      "u-untagged",
    ]);
  });

  it("no filters leaves every node lit", () => {
    expect(lit({})).toEqual(["u-infra", "u-music", "u-untagged"]);
  });

  it("still honours the mock fixture node shape (type/topic on node.data, no table_rows)", () => {
    const mock = {
      nodes: [
        { data: { id: "m1", type: "world", topic: "infra" } },
        { data: { id: "m2", type: "experience", topic: "music" } },
      ],
      edges: [],
    };
    const facets = buildNodeFacets(mock);
    const on = (filters: Record<string, unknown>) =>
      mock.nodes
        .filter((n) => matchesWebFilters(n, facets, filters))
        .map((n) => n.data.id);
    expect(on({ tags: ["infra"] })).toEqual(["m1"]);
    expect(on({ type: "experience" })).toEqual(["m2"]);
  });
});
