# Phase 2 contract traceability

This registry connects each Phase 2 task and quality check to deterministic repository-contract evidence. The evidence mode is **deterministic repository-contract evidence**. It does not claim **live Chrome**, live host/extension/MCP integration, or release readiness. Every known parity issue is recorded as an **integration gap** rather than inferred away.

## Status and boundaries

- Phase 1 is complete and preserved in `docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-1-architecture.md`; its security, ownership, profile-disclosure, recovery, and nonclaim boundaries remain in force.
- Phase 2 is complete under its deterministic contract/evidence exit gate. This is not a production or release certificate.
- The machine-readable manifest is `tests/phase-2-manifest.yaml`.
- The review artifact is `artifacts/p2-contracts-review.md`.
- The Phase 2 MCP fixture index is `tests/fixtures/mcp/index.v1.json`; its profile and HTTP fixtures are retained as historical contract inputs, not current shipped MCP capabilities.
- Current MCP behavior is defined by [MCP compatibility](mcp-compatibility.md): host-backed logical stdio only; 29 offline routes, 30 declared connected routes with 11 `capability_unavailable`; a limited existing-profile run passed connection and fence/rebind, while snapshot/action reconciliation and release acceptance remain open.
- The rmcp 1.7 protocol-version record in the historical fixtures is not a claim of MCP HTTP transport or release readiness.

## Task traceability

### P2-T1 — core records and IDs

Modules: `crates/agentyc-core/Cargo.toml`, `crates/agentyc-core/src/lib.rs`, `crates/agentyc-core/src/ids.rs`, `crates/agentyc-core/src/records.rs`, `crates/agentyc-core/src/states.rs`, `crates/agentyc-core/src/errors.rs`, `crates/agentyc-core/src/protocol.rs`, `crates/agentyc-core/src/snapshots.rs`, `crates/agentyc-core/src/actions.rs`, `crates/agentyc-core/src/events.rs`.

Fixtures: `crates/agentyc-core/tests/fixtures/request-envelope.json`, `crates/agentyc-core/tests/fixtures/action-unknown.json`, `crates/agentyc-core/tests/fixtures/error-stale-ref.json`.

Tests: `crates/agentyc-core/tests/contract.rs::request_envelope_matches_golden_wire_order`, `crates/agentyc-core/tests/contract.rs::no_raw_browser_identity_keys_are_serialized`, `crates/agentyc-core/tests/negative.rs::raw_identity_values_fail_validated_logical_id_deserialization`, `crates/agentyc-core/tests/golden_fixtures.rs::remaining_typed_contract_fixtures_are_executable_goldens`.

Evidence: `crates/agentyc-core/Cargo.toml`, `crates/agentyc-core/src/lib.rs`, `crates/agentyc-core/src/ids.rs`, `crates/agentyc-core/src/records.rs`, `crates/agentyc-core/src/states.rs`, `crates/agentyc-core/src/errors.rs`, `crates/agentyc-core/src/protocol.rs`.

### P2-T2 — local framing and handshake

Modules: `crates/agentyc-core/src/protocol.rs`, `crates/agentyc-host/src/protocol.rs`, `crates/agentyc-host/src/local_ipc.rs`, `docs/security/host-protocol.md`.

Fixtures: `crates/agentyc-core/tests/fixtures/artifact-begin.json`, `crates/agentyc-core/tests/fixtures/artifact-end.json`, `tests/fixtures/mcp/workflows/stdio.v1.json`.

Tests: `crates/agentyc-core/tests/negative.rs::framing_rejects_bounds_truncation_and_trailing_bytes`, `crates/agentyc-core/tests/negative.rs::invalid_utf8_and_protocol_mismatch_are_stable_errors`, `crates/agentyc-host/tests/protocol_edge_cases.rs::coalesced_out_of_order_responses_keep_their_request_identity`, `crates/agentyc-host/tests/protocol_edge_cases.rs::fragmented_frames_decode_only_after_the_complete_payload_arrives`, `crates/agentyc-host/tests/protocol_edge_cases.rs::truncated_stream_is_rejected_at_finish`, `crates/agentyc-host/tests/protocol_edge_cases.rs::local_protocol_uses_the_big_endian_length_prefix`.

Evidence: `crates/agentyc-core/src/protocol.rs`, `crates/agentyc-host/src/protocol.rs`, `crates/agentyc-host/src/local_ipc.rs`, `docs/security/host-protocol.md`.

### P2-T3 — Native Messaging bridge

Modules: `crates/agentyc-host/src/native_messaging.rs`, `extension/src/protocol.mjs`, `extension/src/native-messaging.mjs`, `docs/security/host-protocol.md`.

Fixtures: `tests/fixtures/mcp/workflows/host-backed.v1.json`, `tests/fixtures/mcp/transcripts/http-session.v1.jsonl`.

Tests: `extension/tests/protocol.test.mjs::bounded envelopes reject malformed, raw-id, and oversized messages`, `extension/tests/protocol.test.mjs::post-handshake envelopes require every live epoch`, `extension/tests/protocol.test.mjs::sequence validation rejects gaps and replay`, `extension/tests/protocol.test.mjs::Native Messaging artifact helpers enforce begin/chunk/end order and digest`, `extension/tests/protocol.test.mjs::Native Messaging handshake validates nonce and sequence independently`.

Evidence: `crates/agentyc-host/src/native_messaging.rs`, `extension/src/protocol.mjs`, `extension/src/native-messaging.mjs`, `docs/security/host-protocol.md`.

### P2-T4 — snapshots, refs, actions, waits, and artifacts

Modules: `crates/agentyc-core/src/snapshots.rs`, `crates/agentyc-core/src/actions.rs`, `crates/agentyc-core/src/events.rs`, `docs/api-local.md`.

Fixtures: `crates/agentyc-core/tests/fixtures/snapshot-delta.json`, `crates/agentyc-core/tests/fixtures/snapshot-full.json`, `crates/agentyc-core/tests/fixtures/snapshot-resync.json`, `crates/agentyc-core/tests/fixtures/ref.json`, `crates/agentyc-core/tests/fixtures/wait-cancel.json`, `tests/fixtures/mcp/schemas/tools.v1.json`.

Tests: `crates/agentyc-core/tests/contract.rs::snapshot_delta_hashes_and_order_are_deterministic`, `crates/agentyc-core/tests/negative.rs::delta_rejects_duplicate_targets_unordered_operations_and_bad_hashes`, `crates/agentyc-core/tests/phase2_state_machine.rs::snapshot_refs_and_action_receipts_fail_closed_across_state_boundaries`, `crates/agentyc-core/tests/golden_fixtures.rs::remaining_typed_contract_fixtures_are_executable_goldens`, `packages/agentyc-browser/test/phase2.test.mjs::actions validate the registry and carry now, idempotency, request identity, and deadline`, `packages/agentyc-browser/test/phase2.test.mjs::wait cancellation is cancelled while dispatched mutation cancellation is unknown`.

Evidence: `crates/agentyc-core/src/snapshots.rs`, `crates/agentyc-core/src/actions.rs`, `crates/agentyc-core/src/events.rs`, `docs/api-local.md`.

### P2-T5 — task spaces, pages, leases, and user control

Modules: `crates/agentyc-core/src/records.rs`, `crates/agentyc-core/src/states.rs`, `docs/api-local.md`.

Fixtures: `crates/agentyc-core/tests/fixtures/user-control-return.json`, `crates/agentyc-core/tests/fixtures/action-unknown.json`, `tests/fixtures/mcp/workflows/host-backed.v1.json`.

Tests: `crates/agentyc-core/tests/phase2_state_machine.rs::ordinary_mutations_are_fenced_by_recovery_expiry_and_epoch`, `crates/agentyc-core/tests/phase2_state_machine.rs::ticket_identity_expiry_and_single_use_are_enforced`, `crates/agentyc-core/tests/phase2_state_machine.rs::fence_return_release_and_cleanup_proofs_form_a_closed_transition`, `crates/agentyc-core/tests/phase2_state_machine.rs::snapshot_refs_and_action_receipts_fail_closed_across_state_boundaries`.

Evidence: `crates/agentyc-core/src/records.rs`, `crates/agentyc-core/src/states.rs`, `docs/api-local.md`.

### P2-T6 — direct CLI and Node SDK mapping

Modules: `packages/agentyc-browser/src/operations.mjs`, `packages/agentyc-browser/src/client.mjs`, `packages/agentyc-browser/src/space.mjs`, `packages/agentyc-browser/src/page.mjs`, `crates/agentyc/src/commands/direct.rs`, `crates/agentyc/src/commands/direct/spaces.rs`, `crates/agentyc/src/commands/direct/pages.rs`, `docs/cli.md`.

Fixtures: `tests/fixtures/mcp/workflows/host-backed.v1.json`, `crates/agentyc-core/tests/fixtures/request-envelope.json`.

Tests: `packages/agentyc-browser/test/phase2.test.mjs::createSpace requires exact disclosure acknowledgement and sends canonical fields`, `packages/agentyc-browser/test/phase2.test.mjs::waits carry logical scope, use the bounded default, and reject invalid timeouts`, `packages/agentyc-browser/test/phase2.test.mjs::claim, renew, takeover, and fence acknowledgement use lease defaults`, `packages/agentyc-browser/test/phase2.test.mjs::reconciliation requires a lease epoch before dispatch`, `packages/agentyc-browser/test/sdk.test.mjs::logical space and lazy page handles never expose browser identities`, `packages/agentyc-browser/test/sdk.test.mjs::read requests reconnect once, while side-effect loss becomes unknown`, `packages/agentyc-browser/test/sdk.test.mjs::local protocol transport uses Rust-compatible framing, handshake, events, and resume`, `crates/agentyc/src/commands/direct/pages.rs::planned_page_results_are_logical_and_listable`.

The current logical operation registry also maps `space.acknowledge_fence` to `TaskSpace.acknowledgeFence`, `space acknowledge-fence`, and MCP route `host_lease_acknowledge_fence`.

Evidence: `packages/agentyc-browser/src/operations.mjs`, `packages/agentyc-browser/src/client.mjs`, `crates/agentyc/src/commands/direct.rs`, `crates/agentyc/src/commands/direct/spaces.rs`, `crates/agentyc/src/commands/direct/pages.rs`, `docs/cli.md`.

### P2-T7 — MCP compatibility evidence (historical Phase 2 record)

Current modules: `crates/agentyc-mcp/src/host_adapter.rs`, `crates/agentyc-mcp/src/host_server.rs`, `crates/agentyc-mcp/src/remote_host_server.rs`, `tests/mcp_protocol.rs`, and `docs/mcp-compatibility.md`. The former direct-CDP MCP source is removed and is not a current evidence path.

Retained Phase 2 fixtures: `tests/fixtures/mcp/index.v1.json`, `tests/fixtures/mcp/manifests/default.v1.json`, `tests/fixtures/mcp/manifests/extended.v1.json`, `tests/fixtures/mcp/schemas/tools.v1.json`, `tests/fixtures/mcp/errors/v1.json`, `tests/fixtures/mcp/workflows/stdio.v1.json`, `tests/fixtures/mcp/workflows/http.v1.json`, `tests/fixtures/mcp/workflows/host-backed.v1.json`, `tests/fixtures/mcp/transcripts/stdio-initialize.v1.jsonl`, `tests/fixtures/mcp/transcripts/tool-error.v1.jsonl`, and `tests/fixtures/mcp/transcripts/http-session.v1.jsonl`. These are archived contract evidence and do not describe a current profile or shipped HTTP transport.

Current offline protocol tests: `tests/mcp_protocol.rs::tool_list_contains_only_host_backed_logical_operations`, `tests/mcp_protocol.rs::space_creation_denies_missing_shared_profile_acknowledgement`, and `tests/mcp_protocol.rs::create_space_lease_and_logical_page_over_stdio`.

Current behavior and blockers: offline lists 29 logical routes; the connected remote catalog declares 30 and 11 return `capability_unavailable`. A limited live run passed MCP stdio, host socket, Native Messaging, and extension fence/rebind. It did not produce a snapshot/ref, and the unknown navigation remains unreconciled; MCP is not distribution-ready.

### P2-T8 — primary-output identity audit

Modules: `SKILL.md`, `docs/cli.md`, `docs/api-local.md`, `docs/architecture-existing-chrome.md`, `packages/agentyc-browser/src/operations.mjs`, `crates/agentyc-core/src/lib.rs`.

Fixtures: `tests/fixtures/mcp/schemas/tools.v1.json`, `tests/fixtures/mcp/errors/v1.json`, `crates/agentyc-core/tests/fixtures/error-stale-ref.json`.

Tests: `crates/agentyc-core/tests/contract.rs::no_raw_browser_identity_keys_are_serialized`, `crates/agentyc-core/tests/negative.rs::raw_identity_values_fail_validated_logical_id_deserialization`, `packages/agentyc-browser/test/sdk.test.mjs::logical space and lazy page handles never expose browser identities`, `extension/tests/protocol.test.mjs::bounded envelopes reject malformed, raw-id, and oversized messages`.

Evidence: `SKILL.md`, `docs/cli.md`, `docs/api-local.md`, `docs/architecture-existing-chrome.md`, `packages/agentyc-browser/src/operations.mjs`, `crates/agentyc-core/src/lib.rs`.

## Quality checklist traceability

- Q2-01: `crates/agentyc-core/Cargo.toml`, `crates/agentyc-core/src/lib.rs`; `crates/agentyc-core/tests/contract.rs::request_envelope_matches_golden_wire_order`.
- Q2-02: `crates/agentyc-core/src/protocol.rs`, `crates/agentyc-host/src/native_messaging.rs`, `docs/security/host-protocol.md`; `crates/agentyc-host/tests/protocol_edge_cases.rs::local_protocol_uses_the_big_endian_length_prefix`, `extension/tests/protocol.test.mjs::Native Messaging handshake validates nonce and sequence independently`.
- Q2-03: `crates/agentyc-core/src/actions.rs`, `packages/agentyc-browser/src/operations.mjs`; `packages/agentyc-browser/test/phase2.test.mjs::actions validate the registry and carry now, idempotency, request identity, and deadline`.
- Q2-04: `crates/agentyc-core/src/snapshots.rs`, `docs/api-local.md`; `crates/agentyc-core/tests/phase2_state_machine.rs::snapshot_refs_and_action_receipts_fail_closed_across_state_boundaries`.
- Q2-05: `packages/agentyc-browser/src/operations.mjs`, `crates/agentyc-mcp/src/host_adapter.rs`, `docs/cli.md`; `packages/agentyc-browser/test/sdk.test.mjs::logical space and lazy page handles never expose browser identities`.
- Q2-06: `crates/agentyc-core/src/records.rs`, `docs/architecture-existing-chrome.md`; `crates/agentyc-core/tests/negative.rs::raw_identity_values_fail_validated_logical_id_deserialization`.
- Q2-07: `crates/agentyc-host/src/native_messaging.rs`, `docs/security/host-protocol.md`; `extension/tests/protocol.test.mjs::post-handshake envelopes require every live epoch`.
- Q2-08: `SKILL.md`, `docs/cli.md`, `crates/agentyc-core/src/lib.rs`; `crates/agentyc-core/tests/contract.rs::no_raw_browser_identity_keys_are_serialized`.
- Q2-09: `crates/agentyc-core/src/snapshots.rs`, `crates/agentyc-core/tests/fixtures/snapshot-delta.json`; `crates/agentyc-core/tests/contract.rs::snapshot_delta_hashes_and_order_are_deterministic`.
- Q2-10: `crates/agentyc-core/src/snapshots.rs`, `docs/api-local.md`; `crates/agentyc-core/tests/golden_fixtures.rs::remaining_typed_contract_fixtures_are_executable_goldens`.
- Q2-11: `crates/agentyc-core/src/errors.rs`, `tests/fixtures/mcp/errors/v1.json`, `crates/agentyc-mcp/src/host_adapter.rs`; `crates/agentyc-core/tests/negative.rs::invalid_utf8_and_protocol_mismatch_are_stable_errors`.

## Operation mapping inventory

This list and its MCP mapping fields are the completed Phase 2 deterministic registry record, not the current MCP support matrix. They do not establish connected-route support. Current MCP route availability and typed gaps are defined in `docs/mcp-compatibility.md`. The central registry in `packages/agentyc-browser/src/operations.mjs` remains the source for direct wire/SDK/CLI operations:

`action.cancel`, `action.click`, `action.close`, `action.execute`, `action.input`, `action.navigate`, `action.reconcile`, `action.screenshot`, `action.scroll`, `action.status`, `action.wait`, `events.read`, `host.status`, `page.adopt`, `page.close`, `page.create`, `page.create_managed`, `page.inventory`, `page.list`, `page.navigate`, `snapshot.read`, `space.claim`, `space.create`, `space.finish`, `space.handoff`, `space.list`, `space.pause`, `space.prune`, `space.release`, `space.renew`, `space.return`, `space.takeover`, `space.takeover_with_control_ticket`, `wait.for`.

The Phase 2 record maps direct CLI `page close` to the `page.close` wire method. These mappings are historical contract traceability, not current MCP parity claims; the current host-backed MCP route catalog and unsupported remote capabilities are documented separately.

## MCP fixture inventory

- `tests/fixtures/mcp/index.v1.json`
- `tests/fixtures/mcp/manifests/default.v1.json`
- `tests/fixtures/mcp/manifests/extended.v1.json`
- `tests/fixtures/mcp/schemas/tools.v1.json`
- `tests/fixtures/mcp/errors/v1.json`
- `tests/fixtures/mcp/workflows/stdio.v1.json`
- `tests/fixtures/mcp/workflows/http.v1.json`
- `tests/fixtures/mcp/workflows/host-backed.v1.json`
- `tests/fixtures/mcp/transcripts/stdio-initialize.v1.jsonl`
- `tests/fixtures/mcp/transcripts/tool-error.v1.jsonl`
- `tests/fixtures/mcp/transcripts/http-session.v1.jsonl`

The default and extended profile fixtures are retained as historical Phase 2 records only; they are not current tool catalogs and do not describe shipped MCP capabilities. The schemas and transcripts are sanitized contract inputs, not current release evidence. Current MCP runs over stdio only and has 11 unavailable routes in its connected catalog. A limited existing-profile smoke passed fence/rebind, but full live workflows and release gates remain open; MCP is not distribution-ready.

## Error and identity boundaries

`tests/fixtures/mcp/errors/v1.json` is a historical Phase 2 error contract fixture. `space_id` is canonical. Current host-backed MCP accepts logical identities; raw tab/target/session identifiers are not authority and the removed direct-CDP adapter is not a current allowlist. See `docs/mcp-compatibility.md` for current errors and route boundaries.

## Explicit nonclaims

Phase 1 is preserved and remains complete, but its deterministic evidence does not claim production distribution or existing-profile rollout. Phase 2 is complete under deterministic contract evidence: this artifact and manifest do not claim live Chrome, host/extension/MCP transport, or production release behavior. Phase 3 is complete and Phase 4 is active for the MV3 extension and task-space UI. Later phases own ordinary-user distribution, OOPIF/session-graph support, selected-page retention, deployed-tokenizer performance, rollback/kill-switch drills, load/soak/chaos, human responsiveness, and the still-pending Phase 8 MCP release gate.
