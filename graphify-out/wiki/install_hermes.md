# install_hermes

> 25 nodes

## Key Concepts

- **install_hermes()** (13 connections) — `src/hal0/agents/hermes_provision.py`
- **InstallReport** (9 connections) — `src/hal0/agents/hermes_provision.py`
- **cleanup_stale_agent_dropins()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **reconcile_ownership_on_repair()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **bootstrap_cli()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **InstallStep** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_step_changed()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **StaleDropinCleanupResult** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **OwnershipReconcileResult** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **.step()** (2 connections) — `src/hal0/agents/hermes_provision.py`
- **.failed()** (2 connections) — `src/hal0/agents/hermes_provision.py`
- **.mutated()** (2 connections) — `src/hal0/agents/hermes_provision.py`
- **.converged()** (2 connections) — `src/hal0/agents/hermes_provision.py`
- **.ok()** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Outcome of :func:`cleanup_stale_agent_dropins`.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Remove stale hal0-agent@ drop-in fragments the template doesn't ship.      Scans** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Outcome of :func:`reconcile_ownership_on_repair`.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Reconcile HERMES_HOME/agents + the venv to hal0:hal0 (repair path only).      ``** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **One step's outcome in an :class:`InstallReport`.      ``changed`` is the converg** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Aggregate outcome of one :func:`install_hermes` pass.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Names of the host-mutating steps that changed state this run.          The brain** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **True when no host-mutating step changed anything (a no-op re-run).** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Derive a step's convergence signal from its :class:`PhaseResult`.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Provision Hermes in one linear, convergent pass.      resolve python → pinned SD** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **CLI entry point — thin delegator to :func:`install_hermes`.      Returns a POSIX** (1 connections) — `src/hal0/agents/hermes_provision.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (10 shared connections)
- [Path](Path.md) (5 shared connections)
- [Any](Any.md) (4 shared connections)
- [_StepCtx](_StepCtx.md) (2 shared connections)
- [useSlots](useSlots.md) (1 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 71 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*