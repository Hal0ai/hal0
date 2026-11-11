"""``[memory.projects]``: the project registry in hal0.toml (ADR-0058 decision 4a).

Slugs normalise at load with the resolver's own rule; an alias may not point
at a denied name or form a cycle. Every key has a reader: the resolver
(through ``registry_from_config``) and the ``memory_projects`` doctor row.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.api._settings_apply import REGISTRY
from hal0.config.schema import Hal0Config, MemoryConfig, MemoryProjectsConfig
from hal0.memory.projects import DEFAULT_DENY, registry_from_config, resolve_project


def test_the_shipped_defaults():
    projects = MemoryProjectsConfig()
    # No operator's alias ships: the table starts empty.
    assert projects.aliases == {}
    assert projects.registered == []
    assert projects.deny == [
        "tmp",
        "temp",
        "root",
        "home",
        "workdir",
        "work",
        "scratch",
        "scratchpad",
        "unknown",
        "default",
    ]
    # One owner for the default list: the resolver module.
    assert projects.deny == list(DEFAULT_DENY)
    assert MemoryConfig().projects == projects
    assert Hal0Config().memory.projects == projects


def test_default_lists_are_not_shared_between_instances():
    first, second = MemoryProjectsConfig(), MemoryProjectsConfig()
    first.deny.append("x")
    first.aliases["a"] = "b"
    assert "x" not in second.deny and second.aliases == {}


def test_entries_normalise_at_load():
    projects = MemoryProjectsConfig(
        aliases={"Atlas Mono": "Atlas", "docs/site": "Handbook"},
        deny=["TMP", "My Scratch", "tmp"],
        registered=["Atlas", "atlas", "Borealis  "],
    )
    assert projects.aliases == {"atlas-mono": "atlas", "docs-site": "handbook"}
    assert projects.deny == ["tmp", "my-scratch"]
    assert projects.registered == ["atlas", "borealis"]


def test_the_table_parses_from_toml_shaped_input():
    config = Hal0Config.model_validate(
        {
            "memory": {
                "projects": {
                    "aliases": {"atlas-mono": "atlas"},
                    "deny": ["tmp", "sandbox"],
                    "registered": ["atlas"],
                }
            }
        }
    )
    projects = config.memory.projects
    assert projects.aliases == {"atlas-mono": "atlas"}
    assert projects.deny == ["tmp", "sandbox"]
    assert projects.registered == ["atlas"]


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"aliases": {"a": "a"}}, "forms a cycle: a -> a"),
        ({"aliases": {"a": "b", "b": "a"}}, "forms a cycle"),
        ({"aliases": {"a": "b", "b": "c", "c": "a"}}, "forms a cycle"),
        ({"aliases": {"A": "a"}}, "forms a cycle"),  # the same name after normalising
        ({"aliases": {"atlas-mono": "tmp"}}, "denied name"),
        ({"aliases": {"atlas-mono": "TMP"}}, "denied name"),
        ({"aliases": {"atlas-mono": "sandbox"}, "deny": ["sandbox"]}, "denied name"),
        # A chain that ends at a denied name is refused at its first link too.
        ({"aliases": {"a": "b", "b": "tmp"}}, "denied name"),
        ({"aliases": {"atlas-mono": "unresolved"}}, "denied name"),
        ({"aliases": {"atlas-mono": "unresolved"}, "deny": []}, "denied name"),
        ({"aliases": {"Atlas Mono": "x", "atlas-mono": "y"}}, "twice with different targets"),
        ({"aliases": {"///": "atlas"}}, "is not a project name"),
        ({"aliases": {"atlas-mono": ".."}}, "is not a project name"),
        ({"aliases": {"atlas-mono": 3}}, "is not a project name"),
        ({"deny": ["tmp", ""]}, "is not a project name"),
        ({"deny": [3]}, "is not a project name"),
        ({"registered": ["atlas", "..."]}, "is not a project name"),
        ({"registered": "atlas"}, "list"),
        ({"aliases": ["atlas"]}, "dict"),
        ({"alias": {"a": "b"}}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_tables_are_refused_at_load(table, message):
    with pytest.raises(ValidationError) as excinfo:
        MemoryProjectsConfig.model_validate(table)
    assert message in str(excinfo.value)


def test_valid_alias_shapes():
    # A chain is fine, and so is the same alias spelled twice with one target.
    chained = MemoryProjectsConfig(aliases={"a": "b", "b": "c"})
    assert registry_from_config(chained).canonical("a") == "c"
    same = MemoryProjectsConfig(aliases={"Atlas Mono": "atlas", "atlas-mono": "Atlas"})
    assert same.aliases == {"atlas-mono": "atlas"}
    # A denied directory name may be aliased TO a real project.
    rescued = MemoryProjectsConfig(aliases={"work": "atlas"})
    assert resolve_project(field="work", registry=registry_from_config(rescued)).slug == "atlas"
    # An empty deny list is allowed: the operator turned the list off.
    assert MemoryProjectsConfig(deny=[]).deny == []


def test_the_resolver_reads_every_key():
    projects = MemoryProjectsConfig(
        aliases={"atlas-mono": "atlas"}, deny=["sandbox"], registered=["borealis"]
    )
    registry = registry_from_config(projects)
    assert resolve_project(field="atlas-mono", registry=registry).slug == "atlas"
    assert resolve_project(field="sandbox", registry=registry).denied
    # Setting deny replaces the default list.
    assert resolve_project(field="tmp", registry=registry).slug == "tmp"
    assert resolve_project(field="borealis", registry=registry).registered
    assert resolve_project(field="atlas", registry=registry).registered
    assert not resolve_project(field="cirrus", registry=registry).registered


def test_every_key_is_in_the_settings_apply_registry():
    for key in MemoryProjectsConfig.model_fields:
        entry = REGISTRY[f"memory.projects.{key}"]
        assert entry["apply_class"] == "service-restart"
        assert entry["services"] == ["hal0-api"]
