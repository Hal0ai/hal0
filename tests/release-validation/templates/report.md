# hal0 <VERSION> — release validation

**Date:** <DATE> · **Kit version:** <KIT_VERSION> · **Mode:** <report|file>
**Boxes:** <box id, hostname, IP, OS, hardware, install method, from-version for update boxes>

**Method:** <phases actually run, agent count per phase, model tier per phase. Note any lane
skipped and why.>

**Test models staged:** <ids and sizes>

**Headline totals:** <N read-only checks (M pass) · N stateful checks (M pass) · N regression
probes (M fixed / M regressed / M partial / M blocked)>

---

## Verdict

<Ship / do not ship, in one sentence, followed by the specific blockers.>

**Minimum gate for the next candidate:** <issue numbers and why each one is on the list.>

**What held up well:** <the negative space — surfaces that were exercised and were solid. This
section is what stops the report reading as if the product is on fire, and it tells the next
run which lanes are earning their keep.>

---

## Regression results

| Regression | Issue | Result | Evidence |
|---|---|---|---|

<Every entry in regressions.yaml appears here. `blocked` entries must say what blocked them.>

---

## Filed issues

| # | Severity | Title |
|---|---|---|

**Reclassified by adversarial verification (not filed):**

<Finding, verdict, and the reasoning that killed or downgraded it. These become
known-issues.yaml entries with a `why:` — write them so a future agent does not relitigate.>

**Known-issue dupes (not re-filed):** <finding → existing issue number>

---

## Lane reports

### Read-only sweep

<Per lane: pass/total, then the findings with exact command and decisive output line.>

### Stateful lane

<Per lane, in execution order.>

### Update lane

<Before/after diff summary, then findings.>

---

## Kit changes proposed

<Output of the curation phase: new known-issues entries, new regression entries, lane brief
additions, checks worth promoting into pytest or scripts/release-test.sh. Applied by PR, not
silently.>
