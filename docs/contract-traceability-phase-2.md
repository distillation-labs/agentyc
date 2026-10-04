# Phase 2 contract traceability

This registry connects each Phase 2 task and quality check to deterministic repository-contract evidence. The evidence mode is **deterministic repository-contract evidence**. It does not claim **live Chrome**, live host/extension/MCP integration, or release readiness. Every known parity issue is recorded as an **integration gap** rather than inferred away.

## Status and boundaries

- Phase 1 is complete and preserved in `docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-1-architecture.md`; its security, ownership, profile-disclosure, recovery, and nonclaim boundaries remain in force.
- Phase 2 is complete under its deterministic contract/evidence exit gate. This is not a production or release certificate.
- The machine-readable manifest is `tests/phase-2-manifest.yaml`.
- The review artifact is `artifacts/p2-contracts-review.md`.
- The MCP fixture index is `tests/fixtures/mcp/index.v1.json`.
- MCP is compatibility-only. The versioned fixtures record rmcp 1.7's accepted `2024-11-05` era and do not claim `2025-11-25` or `2026-07-28`.

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

Tests: `packages/agentyc-browser/test/phase2.test.mjs::createSpace requires exact disclosure acknowledgement and sends canonical fields`, `packages/agentyc-browser/test/phase2.test.mjs::waits carry logical scope, use the bounded default, and reject invalid timeouts`, `packages/agentyc-browser/test/phase2.test.mjs::claim, renew, and takeover apply the same default lease TTL`, `packages/agentyc-browser/test/phase2.test.mjs::reconciliation requires a lease epoch before dispatch`, `packages/agentyc-browser/test/sdk.test.mjs::logical space and lazy page handles never expose browser identities`, `packages/agentyc-browser/test/sdk.test.mjs::read requests reconnect once, while side-effect loss becomes unknown`, `packages/agentyc-browser/test/sdk.test.mjs::local protocol transport uses Rust-compatible framing, handshake, events, and resume`, `crates/agentyc/src/commands/direct/pages.rs::planned_page_results_are_logical_and_listable`.

Evidence: `packages/agentyc-browser/src/operations.mjs`, `packages/agentyc-browser/src/client.mjs`, `crates/agentyc/src/commands/direct.rs`, `crates/agentyc/src/commands/direct/spaces.rs`, `crates/agentyc/src/commands/direct/pages.rs`, `docs/cli.md`.

### P2-T7 — MCP compatibility matrix and fixtures

Modules: `crates/agentyc-mcp/src/host_adapter.rs`, `crates/agentyc-mcp/src/host_server.rs`, `crates/agentyc-mcp/src/remote_host_server.rs`, `crates/agentyc-mcp/src/legacy.rs`, `tests/mcp_protocol.rs`, `docs/mcp-compatibility.md`.

Fixtures: `tests/fixtures/mcp/index.v1.json`, `tests/fixtures/mcp/manifests/default.v1.json`, `tests/fixtures/mcp/manifests/extended.v1.json`, `tests/fixtures/mcp/schemas/tools.v1.json`, `tests/fixtures/mcp/errors/v1.json`, `tests/fixtures/mcp/workflows/stdio.v1.json`, `tests/fixtures/mcp/workflows/http.v1.json`, `tests/fixtures/mcp/workflows/host-backed.v1.json`, `tests/fixtures/mcp/transcripts/stdio-initialize.v1.jsonl`, `tests/fixtures/mcp/transcripts/tool-error.v1.jsonl`, `tests/fixtures/mcp/transcripts/http-session.v1.jsonl`.

Tests: `tests/mcp_protocol.rs::test_tool_count_is_61`, `tests/mcp_protocol.rs::test_all_tool_names_present`, `tests/mcp_protocol.rs::test_server_name_in_tool_descriptions`.

Evidence: `crates/agentyc-mcp/src/host_adapter.rs`, `crates/agentyc-mcp/src/host_server.rs`, `crates/agentyc-mcp/src/remote_host_server.rs`, `crates/agentyc-mcp/src/legacy.rs`, `tests/mcp_protocol.rs`, `docs/mcp-compatibility.md`.

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

The central registry in `packages/agentyc-browser/src/operations.mjs` is the source for these keys. Each is represented in the manifest with wire, SDK, CLI, MCP, support status, evidence, and any explicit gap reason:

`space.create`, `space.list`, `space.prune`, `space.claim`, `space.renew`, `space.takeover`, `space.takeover_with_control_ticket`, `space.return`, `space.finish`, `space.release`, `page.create`, `page.create_managed`, `page.close`, `page.list`, `page.inventory`, `action.execute`, `action.status`, `action.reconcile`, `snapshot.read`, `events.read`, `wait.for`, `host.status`, `action.cancel`, `page.navigate`, `page.adopt`, `action.navigate`, `action.click`, `action.input`, `action.evaluate`, `action.scroll`, `action.wait`, `action.screenshot`, `action.storage_write`, `action.cookie_write`, `action.upload`, `action.close`.

Direct CLI `page close` is lease-authorized and maps to the same `page.close` wire method as the SDK and host adapter. Other direct-only operations without measured host-backed MCP routes remain explicitly marked as MCP adapter deferrals, not parity claims.

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

The default manifest records 61 tools; the extended manifest records 76 in the checked-in catalog order. The schemas and transcripts are sanitized: no credentials, external URLs, page bodies, live browser identifiers, or CDP endpoints. These are later phases' release inputs, not a release claim in Phase 2.

## Error and identity boundaries

`tests/fixtures/mcp/errors/v1.json` covers every canonical error code with retryability, guidance, next step, CLI mapping, SDK mapping, and MCP classification. `space_id` is canonical. Raw tab/target/session fields are never authority. Compatibility-only fields are allowlisted explicitly in `crates/agentyc-mcp/src/legacy.rs`, `docs/api.md`, and the redacted schema fixture.

## Explicit nonclaims

Phase 1 is preserved and remains complete, but its deterministic evidence does not claim production distribution or existing-profile rollout. Phase 2 is complete under deterministic contract evidence: this artifact and manifest do not claim live Chrome, host/extension/MCP transport, or production release behavior. Phase 3 is active for the broker/ledger implementation. Later phases own ordinary-user distribution, OOPIF/session-graph support, selected-page retention, deployed-tokenizer performance, rollback/kill-switch drills, load/soak/chaos, human responsiveness, and the Phase 8 MCP release gate.
