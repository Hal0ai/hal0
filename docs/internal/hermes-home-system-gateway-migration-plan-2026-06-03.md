# Hermes Home-Normalization + System-Scope Gateway — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the in-tree WIP so a clean install brings up Hermes as `hal0` on `/var/lib/hal0/.hermes` with a system-scope gateway, then cut this box over in-place preserving live state.

**Architecture:** Two phases. Phase 1 is repo work — commit the WIP and wire `hermes gateway install --system --run-as-user hal0` into `install.sh` so fresh installs create+start the system gateway (the provisioner only writes the secrets drop-in, never the main unit). Phase 2 is a one-time, ordered cutover of the live box — stop both `state.db` writers, copy the home, install the system gateway, tear down the legacy root user-gateway, repoint+restart the dashboard.

**Tech Stack:** Python 3.12 (`hal0` pkg, editable at `/opt/hal0`), `hermes_cli` (venv `/var/lib/hal0/venvs/hermes`), systemd (system + user scopes), bash installer, pytest + ruff. All work runs on the LXC via `ssh hal0` (root@10.0.1.142).

**Branch:** `feat/hermes-home-normal-location-system-gateway`. Spec: `docs/internal/hermes-home-system-gateway-migration-2026-06-03.md`. WIP backup patch: `/var/lib/hal0/backups/hermes-home-migration-WIP-20260603_001147.patch` (+ dangling stash `53b8672`).

**Convention notes:**
- CI runs `ruff check` AND `ruff format --check` as separate fatal steps — both must pass.
- Full `pytest tests/` hangs locally (lemond health waits) — run the targeted subsets below; let CI gate the whole suite.
- All `systemctl --user` calls as root over SSH need `export XDG_RUNTIME_DIR=/run/user/0`.

---

## Phase 1 — Repo: fresh install lands correctly

### Task 1: Verify the WIP, then commit it

**Files:**
- Add (untracked): `installer/wrappers/hermes`
- Commit (already modified): `src/hal0/agents/hermes_provision.py`, `installer/systemd/hal0-agent@hermes.service.d/override.conf`, `installer/wrappers/hal0-hermes`, `installer/agents/hermes-agent.sh`, `src/hal0/agents/personas.py`, `src/hal0/api/agents/chat_proxy.py`, `src/hal0/cli/agent_commands.py`, `src/hal0/cli/agent_shim.py`, docs, and the updated tests.

- [ ] **Step 1: Run the WIP's own tests to confirm green before committing**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/python -m pytest \
  tests/agents/test_hermes_provision.py \
  tests/agents/test_hermes_wrapper.py \
  tests/systemd/test_unit_files.py \
  tests/cli/test_agent_shim.py \
  tests/cli/test_agents_personas.py -q'
```
Expected: all PASS. If any fail, STOP and fix the WIP before committing (do not commit red).

- [ ] **Step 2: Lint + format check the changed Python**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/ruff check src/hal0/agents/hermes_provision.py src/hal0/agents/personas.py src/hal0/cli/agent_shim.py src/hal0/cli/agent_commands.py src/hal0/api/agents/chat_proxy.py tests/agents/test_hermes_provision.py tests/agents/test_hermes_wrapper.py && /opt/hal0/.venv/bin/ruff format --check src/hal0/agents/hermes_provision.py tests/agents/test_hermes_wrapper.py'
```
Expected: `All checks passed!` and no format diffs. Fix any reported issues.

- [ ] **Step 3: Stage everything including the untracked canonical wrapper**

```bash
ssh hal0 'cd /opt/hal0 && git add installer/wrappers/hermes installer/wrappers/hal0-hermes installer/systemd/hal0-agent@hermes.service.d/override.conf installer/agents/hermes-agent.sh src/hal0/agents/hermes_provision.py src/hal0/agents/personas.py src/hal0/api/agents/chat_proxy.py src/hal0/cli/agent_commands.py src/hal0/cli/agent_shim.py docs/ tests/ && git status --short'
```
Expected: all listed files staged (`A`/`M`), nothing relevant left untracked.

- [ ] **Step 4: Commit**

```bash
ssh hal0 'cd /opt/hal0 && git -c user.name="halo" -c user.email="alexander@awideweb.com" commit -m "feat(agents): normalize HERMES_HOME to ~/.hermes + system-scope gateway secrets drop-in (#437)

- HERMES_HOME default -> /var/lib/hal0/.hermes everywhere
- canonical /usr/local/bin/hermes wrapper (no HERMES_HOME pin); hal0-hermes -> symlink
- _phase_gateway_secrets_wire writes system-scope /etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf
- tests + docs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"'
```
Expected: commit succeeds.

---

### Task 2: Wire the system gateway into the installer (the fresh-install gap)

The installer enables the dashboard but never installs the gateway main unit. Add it.

**Files:**
- Test: `tests/systemd/test_unit_files.py` (append a guard test)
- Modify: `installer/install.sh` (after the `systemctl enable --now hal0-agent@hermes.service` line, ~1208)

- [ ] **Step 1: Write the failing guard test**

Append to `tests/systemd/test_unit_files.py`:

```python
def test_installer_installs_system_gateway():
    """install.sh must create AND enable the system-scope hermes gateway.

    The provisioner only writes the secrets drop-in; the main unit comes
    from `hermes gateway install --system --run-as-user hal0`. Without
    this the gateway (Telegram/Discord) never starts on a fresh install.
    """
    from pathlib import Path

    install_sh = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
    text = install_sh.read_text(encoding="utf-8")
    assert "gateway install --system --run-as-user hal0" in text
    assert "enable --now hermes-gateway.service" in text
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/python -m pytest tests/systemd/test_unit_files.py::test_installer_installs_system_gateway -q'
```
Expected: FAIL (`assert ... in text` — strings not present yet).

- [ ] **Step 3: Add the gateway install to install.sh**

In `installer/install.sh`, immediately after the line:

```bash
        systemctl enable --now hal0-agent@hermes.service
```

insert (matching the surrounding indentation and the `hermes`-enabled guard block it already lives in):

```bash
        # Gateway (Telegram/Discord) runs as a SYSTEM service under the
        # hal0 user — same posture as the dashboard above. The provisioner
        # has already written the secrets drop-in
        # (/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf);
        # hermes_cli lays down the main unit here. daemon-reload picks up
        # the drop-in BEFORE first start so platforms connect on boot.
        # HERMES_HOME is unset for this call so the generator bakes the
        # hal0 default (~/.hermes), not any value inherited from the
        # installer's environment.
        info "installing system-scope hermes gateway (User=hal0)"
        env -u HERMES_HOME /usr/local/bin/hermes gateway install --system --run-as-user hal0
        systemctl daemon-reload
        systemctl enable --now hermes-gateway.service
        info "hermes-gateway.service enabled (system, User=hal0)"
```

- [ ] **Step 4: Run the guard test to confirm it passes**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/python -m pytest tests/systemd/test_unit_files.py::test_installer_installs_system_gateway -q'
```
Expected: PASS.

- [ ] **Step 5: Shellcheck + format the installer change**

```bash
ssh hal0 'cd /opt/hal0 && bash -n installer/install.sh && (command -v shellcheck >/dev/null && shellcheck -S error installer/install.sh || echo "shellcheck absent — bash -n only")'
```
Expected: `bash -n` clean (no syntax error); shellcheck no error-level findings (or absent).

- [ ] **Step 6: Lint/format the test file + commit**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/ruff check tests/systemd/test_unit_files.py && /opt/hal0/.venv/bin/ruff format --check tests/systemd/test_unit_files.py && git add installer/install.sh tests/systemd/test_unit_files.py && git -c user.name="halo" -c user.email="alexander@awideweb.com" commit -m "feat(installer): install + enable system-scope hermes-gateway on fresh install

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"'
```
Expected: ruff clean, commit succeeds.

---

### Task 3: Repo regression sweep

- [ ] **Step 1: Run the full agent/systemd/cli test subsets**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/python -m pytest tests/agents/ tests/systemd/ tests/cli/ -q'
```
Expected: all PASS. Fix any regression before Phase 2.

- [ ] **Step 2: Repo-wide ruff (cheap, catches stragglers)**

```bash
ssh hal0 'cd /opt/hal0 && /opt/hal0/.venv/bin/ruff check src/ tests/ && /opt/hal0/.venv/bin/ruff format --check src/hal0/agents/ src/hal0/cli/ tests/agents/ tests/systemd/'
```
Expected: clean.

---

## Phase 2 — One-time cutover of THIS box (preserve live state)

> Operational, not TDD. Each task has exact commands + an expected result that gates the next. Old home is preserved until the final verification passes.

### Task 4: Pre-flight + full backup

- [ ] **Step 1: Snapshot the live home (root-owned copy preserves perms)**

```bash
ssh hal0 'cp -a /var/lib/hal0/agents/hermes /var/lib/hal0/agents/hermes.bak-cutover-$(date +%Y%m%d_%H%M%S) && ls -d /var/lib/hal0/agents/hermes.bak-cutover-*'
```
Expected: a `hermes.bak-cutover-*` dir exists.

- [ ] **Step 2: Back up the legacy root gateway unit (for rollback)**

```bash
ssh hal0 'cp -a /root/.config/systemd/user/hermes-gateway.service /root/hermes-gateway.service.rollback && cp -a /root/.config/systemd/user/hermes-gateway.service.d /root/hermes-gateway.service.d.rollback 2>/dev/null; ls -la /root/hermes-gateway.service.rollback'
```
Expected: rollback copy exists.

- [ ] **Step 3: Confirm the secret vault is intact (tokens present)**

```bash
ssh hal0 'grep -cE "^(TELEGRAM|DISCORD)_BOT_TOKEN=" /var/lib/hal0/secrets/agents/hermes.env'
```
Expected: `2`.

### Task 5: Stop both `state.db` writers (quiesce SQLite WAL)

- [ ] **Step 1: Stop the legacy root gateway and the dashboard**

```bash
ssh hal0 'export XDG_RUNTIME_DIR=/run/user/0; systemctl --user stop hermes-gateway.service; systemctl stop hal0-agent@hermes.service; sleep 2; systemctl --user is-active hermes-gateway.service; systemctl is-active hal0-agent@hermes.service'
```
Expected: both report `inactive`/`failed` (stopped), not `active`.

- [ ] **Step 2: Confirm no hermes process still holds state.db**

```bash
ssh hal0 'fuser -v /var/lib/hal0/agents/hermes/state.db 2>&1 || echo "no holders"'
```
Expected: `no holders` (or empty). If a process remains, kill it before copying.

### Task 6: Migrate the home → `/var/lib/hal0/.hermes`

- [ ] **Step 1: rsync live contents into the claimed default home (preserve owner)**

```bash
ssh hal0 'rsync -a --info=stats1 /var/lib/hal0/agents/hermes/ /var/lib/hal0/.hermes/ && chown -R hal0:hal0 /var/lib/hal0/.hermes'
```
Expected: rsync completes; number of files transferred reported.

- [ ] **Step 2: Drop stale runtime/gateway state so the new gateway starts clean**

```bash
ssh hal0 'cd /var/lib/hal0/.hermes && rm -f gateway.lock gateway.pid gateway_state.json processes.json .write_test && ls gateway.pid 2>&1 || echo "stale runtime state cleared"'
```
Expected: `stale runtime state cleared`.

- [ ] **Step 3: Verify the irreplaceable state landed + marker present**

```bash
ssh hal0 'cd /var/lib/hal0/.hermes && ls -la config.yaml .env state.db kanban.db .hal0-managed && echo "sessions:" && ls sessions | wc -l && echo "memories:" && ls memories'
```
Expected: `config.yaml`, `.env`, `state.db`, `kanban.db`, `.hal0-managed` all present; sessions count > 0.

### Task 7: Repoint the dashboard unit to the new home

- [ ] **Step 1: Deploy the new override.conf (HERMES_HOME=.hermes) to /etc**

```bash
ssh hal0 'cp /opt/hal0/installer/systemd/hal0-agent@hermes.service.d/override.conf /etc/systemd/system/hal0-agent@hermes.service.d/override.conf && grep HERMES_HOME /etc/systemd/system/hal0-agent@hermes.service.d/override.conf'
```
Expected: `Environment="HERMES_HOME=/var/lib/hal0/.hermes"`.

- [ ] **Step 2: daemon-reload**

```bash
ssh hal0 'systemctl daemon-reload && echo reloaded'
```
Expected: `reloaded`.

### Task 8: Install the canonical CLI + system gateway unit

- [ ] **Step 1: Install the new /usr/local/bin/hermes wrapper + hal0-hermes symlink**

```bash
ssh hal0 'install -m0755 /opt/hal0/installer/wrappers/hermes /usr/local/bin/hermes && ln -sf /usr/local/bin/hermes /usr/local/bin/hal0-hermes && ls -la /usr/local/bin/hermes /usr/local/bin/hal0-hermes'
```
Expected: `/usr/local/bin/hermes` is a regular exe; `hal0-hermes` → `hermes` symlink.

- [ ] **Step 2: Generate the system-scope gateway main unit (clean HERMES_HOME env)**

```bash
ssh hal0 'env -u HERMES_HOME /usr/local/bin/hermes gateway install --system --run-as-user hal0 2>&1 | tail -5'
```
Expected: reports writing `/etc/systemd/system/hermes-gateway.service`.

- [ ] **Step 3: Verify the generated unit has the RIGHT user + home; pin home via drop-in if not**

```bash
ssh hal0 'grep -E "User=|Group=|HERMES_HOME=" /etc/systemd/system/hermes-gateway.service'
```
Expected: `User=hal0`, `Group=hal0`, `Environment="HERMES_HOME=/var/lib/hal0/.hermes"`.

If `HERMES_HOME` is wrong/missing, pin it with a drop-in (survives regeneration):

```bash
ssh hal0 'mkdir -p /etc/systemd/system/hermes-gateway.service.d && printf "[Service]\nEnvironment=\"HERMES_HOME=/var/lib/hal0/.hermes\"\n" > /etc/systemd/system/hermes-gateway.service.d/20-hal0-home.conf && cat /etc/systemd/system/hermes-gateway.service.d/20-hal0-home.conf'
```
Expected (only if needed): drop-in written.

- [ ] **Step 4: Ensure the secrets drop-in exists (provisioner phase output), reload**

```bash
ssh hal0 'ls -la /etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf 2>&1; if [ ! -f /etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf ]; then mkdir -p /etc/systemd/system/hermes-gateway.service.d && printf "[Service]\nEnvironmentFile=/var/lib/hal0/secrets/agents/hermes.env\n" > /etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf && echo "wrote secrets drop-in"; fi && systemctl daemon-reload && systemctl show hermes-gateway.service -p EnvironmentFiles'
```
Expected: `EnvironmentFiles=/var/lib/hal0/secrets/agents/hermes.env (ignore_errors=no)`.

### Task 9: Tear down the legacy root user-gateway (this box only)

- [ ] **Step 1: Disable + remove the root user-scope gateway so two gateways never share the token**

```bash
ssh hal0 'export XDG_RUNTIME_DIR=/run/user/0; systemctl --user disable --now hermes-gateway.service 2>&1; rm -f /root/.config/systemd/user/hermes-gateway.service; rm -rf /root/.config/systemd/user/hermes-gateway.service.d; systemctl --user daemon-reload; systemctl --user list-unit-files "hermes-gateway*" 2>&1 | tail -3'
```
Expected: no `hermes-gateway` unit listed under `--user`.

### Task 10: Start the system services

- [ ] **Step 1: Enable + restart the system gateway, start the dashboard**

`restart` (not `start`) because `hermes gateway install --system` in Task 8 may have already auto-started the unit before the home/secrets drop-ins were finalized — a restart guarantees it re-reads the final unit + both drop-ins.

```bash
ssh hal0 'systemctl enable hermes-gateway.service && systemctl restart hermes-gateway.service && systemctl start hal0-agent@hermes.service && sleep 12 && systemctl is-active hermes-gateway.service hal0-agent@hermes.service'
```
Expected: both `active`.

### Task 11: Verify the cutover

- [ ] **Step 1: Both services run as hal0 (not root)**

```bash
ssh hal0 'for u in hermes-gateway.service hal0-agent@hermes.service; do p=$(systemctl show $u -p MainPID --value); echo "$u pid=$p user=$(ps -o user= -p $p 2>/dev/null)"; done'
```
Expected: both `user=hal0`.

- [ ] **Step 2: Platforms connected + tokens in env (now read by pid1/root for a User=hal0 unit)**

```bash
ssh hal0 'P=$(systemctl show hermes-gateway.service -p MainPID --value); echo "tokens in env:"; tr "\0" "\n" < /proc/$P/environ | grep -cE "TELEGRAM_BOT_TOKEN|DISCORD_BOT_TOKEN"; python3 -c "import json;d=json.load(open(\"/var/lib/hal0/.hermes/gateway_state.json\"));print(\"pid\",d[\"pid\"]);[print(p,v[\"state\"]) for p,v in d[\"platforms\"].items()]"'
```
Expected: token count `2`; telegram + discord `connected`; gateway_state pid matches MainPID.

- [ ] **Step 3: Telegram token live + dashboard reachable**

```bash
ssh hal0 'TOK=$(grep -oP "^TELEGRAM_BOT_TOKEN=\K.*" /var/lib/hal0/secrets/agents/hermes.env); curl -s --max-time 8 "https://api.telegram.org/bot$TOK/getMe" | python3 -c "import sys,json;d=json.load(sys.stdin);print(\"telegram ok=\",d.get(\"ok\"),d.get(\"result\",{}).get(\"username\"))"; curl -s -o /dev/null -w "dashboard http=%{http_code}\n" http://127.0.0.1:8080/api/status'
```
Expected: `telegram ok= True hal0ai_bot`; `dashboard http=200`.

- [ ] **Step 4: State intact under the new home; nothing under user@0**

```bash
ssh hal0 'export XDG_RUNTIME_DIR=/run/user/0; echo "sessions:"; ls /var/lib/hal0/.hermes/sessions | wc -l; echo "kanban+memories:"; ls /var/lib/hal0/.hermes/kanban.db /var/lib/hal0/.hermes/memories; echo "user@0 hermes procs:"; (systemctl --user list-units "hermes*" --no-legend 2>/dev/null | grep -c hermes || echo 0)'
```
Expected: sessions > 0; kanban.db + memories present; user@0 hermes proc count `0`.

### Task 12: Cleanup + record

- [ ] **Step 1: After verification PASSES, remove the legacy home + backups**

```bash
ssh hal0 'rm -rf /var/lib/hal0/agents/hermes && echo "legacy home removed"; ls -d /var/lib/hal0/agents/hermes.bak-cutover-* 2>/dev/null && echo "(backup retained — delete manually once confident)"'
```
Expected: legacy active home gone; timestamped backup retained for now.

- [ ] **Step 2: Update the auto-memory clobber note to reflect system-scope reality**

Edit `/home/halo/.claude/projects/-home-halo/memory/hermes_gateway_envfile_clobber.md`: change the gateway description from `systemd --user`/`/root/.config` to system-scope `/etc/systemd/system/hermes-gateway.service` (`User=hal0`), drop-in at `/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf`, HERMES_HOME=`/var/lib/hal0/.hermes`. Note the cutover landed 2026-06-03.

- [ ] **Step 3: Push the branch + open the PR**

```bash
ssh hal0 'cd /opt/hal0 && git push -u origin feat/hermes-home-normal-location-system-gateway 2>&1 | tail -3'
gh pr create --repo Hal0ai/hal0 --base main --head feat/hermes-home-normal-location-system-gateway \
  --title "feat(agents): hermes home-normalization + system-scope gateway (closes #437)" \
  --body "Normalizes HERMES_HOME to ~/.hermes (/var/lib/hal0/.hermes), runs the gateway as a system service (User=hal0) out of root, adds canonical /usr/local/bin/hermes CLI, and wires the installer to create+enable the system gateway on fresh install. Live box cut over in-place 2026-06-03 (state preserved). Closes #437."
```
Expected: branch pushed, PR URL returned.

- [ ] **Step 4: Verify CI is green on the PR**

```bash
gh pr checks --repo Hal0ai/hal0 feat/hermes-home-normal-location-system-gateway 2>&1 | tail -10
```
Expected: required checks pass (investigate any red before merge — do not admin-merge through failing python/ui).

---

## Rollback (if Phase 2 verification fails)

1. Stop the system gateway: `systemctl disable --now hermes-gateway.service`; remove `/etc/systemd/system/hermes-gateway.service`(+`.d/`).
2. Restore the dashboard override: revert `/etc/systemd/system/hal0-agent@hermes.service.d/override.conf` to `HERMES_HOME=/var/lib/hal0/agents/hermes`; `daemon-reload`.
3. Restore the root user-gateway: `cp /root/hermes-gateway.service.rollback /root/.config/systemd/user/hermes-gateway.service` (+ `.d.rollback`); `XDG_RUNTIME_DIR=/run/user/0 systemctl --user daemon-reload && systemctl --user enable --now hermes-gateway.service`.
4. Restart dashboard: `systemctl start hal0-agent@hermes.service`.
The legacy home was never deleted until Task 12, so live state is intact throughout.
