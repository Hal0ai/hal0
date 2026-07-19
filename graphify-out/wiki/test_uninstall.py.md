# test_uninstall.py

> 30 nodes

## Key Concepts

- **test_uninstall.py** (16 connections) — `tests/cli/test_uninstall.py`
- **Any** (9 connections)
- **MonkeyPatch** (7 connections)
- **test_uninstall_resolves_fhs_path_when_not_editable()** (6 connections) — `tests/cli/test_uninstall.py`
- **captured_exec()** (4 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_default_args()** (4 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_missing_script_dies()** (4 connections) — `tests/cli/test_uninstall.py`
- **.uninstall()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_uninstall_purge_flag()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_clean_slate_alias()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_default_non_tty_proceeds()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_purge_refuses_without_tty()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_purge_non_tty_with_force_proceeds()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_purge_non_tty_with_hal0_force_env()** (3 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_keep_data_flag()** (2 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_force_flag()** (2 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_short_force_flag()** (2 connections) — `tests/cli/test_uninstall.py`
- **test_uninstall_dev_flag()** (2 connections) — `tests/cli/test_uninstall.py`
- **Path** (2 connections)
- **Tests for the ``hal0 uninstall`` CLI subcommand.  The command is a thin wrapper** (1 connections) — `tests/cli/test_uninstall.py`
- **Intercept os.execvp so we can assert argv without replacing the process.** (1 connections) — `tests/cli/test_uninstall.py`
- **No flags → bash <script> with no extra args.      Bypasses CliRunner (which swap** (1 connections) — `tests/cli/test_uninstall.py`
- **--purge forwards to the script (with --force here to skip the prompt).** (1 connections) — `tests/cli/test_uninstall.py`
- **--clean-slate is an alias of --purge and forwards --purge to the script.** (1 connections) — `tests/cli/test_uninstall.py`
- **Conservative default never prompts, so a bare `hal0 uninstall` is safe     non-i** (1 connections) — `tests/cli/test_uninstall.py`
- *... and 5 more nodes in this community*

## Relationships

- [_FakeAgentManager](_FakeAgentManager.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)

## Source Files

- `tests/cli/test_agent_install_hermes.py`
- `tests/cli/test_uninstall.py`

## Audit Trail

- EXTRACTED: 88 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*