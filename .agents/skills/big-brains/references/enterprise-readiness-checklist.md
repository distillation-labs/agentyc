# Initiative and enterprise-readiness checklist

Use this extension for multi-team, multi-quarter, migration, compliance, or high-risk external work.

## Ownership and governance

- [ ] Named product, engineering, operational, security, and support owners.
- [ ] Stakeholder decision gates and escalation path documented.
- [ ] Data classification, retention, residency, and access requirements verified.
- [ ] Vendor/provider contract, pricing, quota, support, and exit terms reviewed.

## Migration and compatibility

- [ ] Schema/data migration and backfill strategy is explicit.
- [ ] Dual-read/dual-write or compatibility window behavior is defined where needed.
- [ ] Idempotent restart and resume behavior is tested.
- [ ] Rollback or forward-fix path is safe after partial migration.
- [ ] User communication, training, and support FAQ are prepared.

## Rollout

- [ ] Cohorts, feature flags, environment progression, and duration are named.
- [ ] Promotion thresholds and rollback triggers have numerical or observable signals.
- [ ] Canary and held-out validation evidence is recorded.
- [ ] Monitoring dashboards and alerts exist before expansion.
- [ ] Go/no-go owner and decision date are recorded.

## Operational handoff

- [ ] Runbook covers common failures, diagnosis, mitigation, and escalation.
- [ ] On-call ownership and service-level expectations are documented.
- [ ] Known limitations and deferred work have owners and revisit conditions.
- [ ] The final artifact, source ledger, validation commands, and rollback action are linked from the plan README.
