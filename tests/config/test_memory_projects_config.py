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
        ({"aliases": {"a": "b", "b": "a"}}, "forms a cycle: a -> b -> a"),
        ({"aliases": {"a": "b", "b": "c", "c": "a"}}, "forms a cycle"),
        ({"aliases": {"A": "b", "B": "a"}}, "forms a cycle"),
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


@pytest.mark.parametrize(
    ("aliases", "kept"),
    [
        ({"Widgets": "widgets"}, {}),
        ({"widgets": "widgets"}, {}),
        ({"My Repo": "my-repo", "MY REPO": "My  Repo"}, {}),
        # A no-op entry beside real ones is dropped and the rest stand.
        ({"Widgets": "widgets", "widgets-mono": "Widgets"}, {"widgets-mono": "widgets"}),
        ({"Widgets": "widgets", "widgets": "gadgets"}, {"widgets": "gadgets"}),
        # A name that is denied anyway: mapping it to itself changes nothing.
        ({"TMP": "tmp"}, {}),
    ],
)
def test_an_alias_from_a_name_to_itself_is_a_no_op_not_a_cycle(aliases, kept):
    """``{"Widgets": "widgets"}`` is one name spelled twice. The validator and
    ``registry_from_config`` both ignore it, so they cannot disagree."""
    from types import SimpleNamespace

    projects = MemoryProjectsConfig(aliases=aliases)
    assert projects.aliases == kept
    assert dict(registry_from_config(projects).aliases) == kept
    # The same table, never validated: the registry reaches the same result.
    assert dict(registry_from_config(SimpleNamespace(aliases=aliases)).aliases) == kept


def test_building_the_config_does_not_import_the_memory_providers():
    """The schema takes the slug rule and the deny list from the leaf module
    ``hal0.project_slug``. Importing ``hal0.memory.projects`` instead would run
    ``hal0/memory/__init__.py``, which imports the providers and their HTTP
    client: a cost every config load would pay."""
    import subprocess
    import sys

    script = (
        "import sys\n"
        "import hal0.config.schema as s\n"
        "s.MemoryConfig()\n"
        "s.Hal0Config()\n"
        "s.MemoryProjectsConfig(aliases={'My Repo': 'core'}, deny=['TMP'], registered=['Core'])\n"
        "loaded = sorted(m for m in sys.modules if m == 'hal0.memory' "
        "or m.startswith('hal0.memory.'))\n"
        "assert loaded == [], loaded\n"
        "assert 'hal0.project_slug' in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_resolver_module_re_exports_the_leaf_module():
    import hal0.project_slug as leaf
    from hal0.memory import projects

    for name in leaf.__all__:
        assert getattr(projects, name) is getattr(leaf, name), name


def test_valid_alias_shapes():
    # A chain is fine, and so is the same alias spelled twice with one target.
    chained = MemoryProjectsConfig(aliases={"a": "b", "b": "c"})
    assert registry_from_config(chained).canonical("a") == "c"
    same = MemoryProjectsConfig(aliases={"Atlas Mono": "atlas", "atlas-mono": "Atlas"})
    assert same.aliases == {"atlas-mono": "atlas"}
    # A denied directory name may be aliased TO a real project.
    rescued = MemoryProjectsConfig(aliases={"workdir": "atlas"})
    assert resolve_project(field="workdir", registry=registry_from_config(rescued)).slug == "atlas"
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
