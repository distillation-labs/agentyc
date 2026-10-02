# Phase 7 rollout support runbook

This runbook is for release operators. The direct gate is fail-closed: an
offline result, descriptor-only result, skipped scenario, or missing artifact
is a blocker, not a green substitute.

## 1. Classify the gate result

Run:

```bash
python3 scripts/run_release_gate.py --require-live \
  --artifact-dir artifacts/p7-release-gate
```

Inspect `artifacts/p7-release-gate/report.json`:

- `live_passed` with `release_eligible: true` is the only direct release pass.
- `offline_passed` is a local schema/hook check and is never release evidence.
- `blocked` lists the gate and blocker code to recover.

Do not edit a report to remove a blocker. Re-run the producer with a new
artifact directory or replace the source with an independently captured,
redacted record.

## 2. Missing or offline benchmark evidence

Symptoms: `performance/evidence-missing`, `benchmark is offline evidence`,
missing release-gate sections, or `not_measured_offline` metrics in live mode.

1. Keep the release blocked.
2. Capture a real headed/host-backed benchmark with the required build tuple,
   sample counts, raw sample declarations, confidence intervals, and live
   resource/token/context/reliability metrics.
3. Confirm every metric has `value`, `status: measured`, and a `ceiling` or
   `minimum`.
4. Re-run the gate with `--benchmark-artifact` pointing at the redacted artifact.

The local benchmark scaffold and a 30-sample smoke run cannot close the gate.

## 3. Existing-Chrome enrollment or coexistence failure

Symptoms: `real_chrome/evidence-missing`, descriptor validation failure,
`live_descriptor_validated_not_executed`, or a nonzero safety counter.

1. Do not provide a CDP/WebSocket URL, raw target/session/tab ID, profile path,
   extension ID, or browser executable to `run_existing_chrome.py`.
2. Prepare a schema-2 descriptor that explicitly enrolls the profile, host, and
   extension and identifies an already-running browser with launch, download,
   and CDP use all false.
3. Capture the ten required scenarios independently. Every scenario must be
   `live_passed`; descriptor-only evidence remains non-green.
4. Preserve the user's unrelated tab and focus. Any close, focus theft,
   cross-space mutation, or stale-agent mutation is a no-go.
5. Re-run with the descriptor. The runner itself never attaches or launches a
   browser.

## 4. Installation, update, or rollback failure

Symptoms: lifecycle status is missing/not measured, rollback safety is absent,
kill switch is not verified, or an incompatible ledger is reported.

1. Stop new mutations and arm/verify the mutation kill switch.
2. Do not delete or replace an unknown registration, journal, or ledger.
3. Preserve pages, user tabs, and the Chrome process; do not use a global close.
4. Reconcile only the exact owned test registration through the explicit drill.
5. Capture fresh install, update, uninstall, downgrade, and rollback evidence.
6. Ensure the lifecycle record says exactly `installed`, `passed`, `passed`,
   `passed`, `rolled_back` and the rollback-safety fields are true/false as
   required by the schema.
7. Re-run the gate with `--installation-artifact`.

A failed or uncertain rollback remains blocked even if the registration appears
absent. Unknown state requires operator reconciliation.

## 5. Hook divergence or missing repetitions

Symptoms: `hook-invalid`, divergent hashes, missing repetitions, or a fault not
accounted for.

1. Preserve the seed and artifact report.
2. Re-run replay, chaos, and soak with the default 100 repetitions or more.
3. Investigate nondeterminism, missing outcomes, replayed mutations, resource
   slope, and space isolation before retrying the release gate.
4. Do not lower `--repetitions` below 100 to make the gate pass.

Chaos must cover the declared fault matrix. Soak must include resource slopes
and isolation evidence.

## 6. Envelope or redaction failure

Symptoms: `stable after central redaction`, missing envelope fields, raw path,
secret, page body, debugger endpoint, or browser ID.

1. Treat the artifact as unsafe and discard it from release evidence.
2. Remove raw logs from the publication path; do not hand-edit sensitive values
   into a report.
3. Regenerate through the producer's central redaction boundary.
4. Verify the persisted JSON is unchanged by a second redaction pass.

## 7. Recovery decision

Release only when the direct gate and legacy gate both pass. If any live lane is
unavailable in the environment—especially headed Chrome, an enrolled host and
extension, live benchmark infrastructure, or real installation/update/rollback
execution—record that as an environment blocker and stop. Do not convert it to
an offline pass or a skipped scenario.
