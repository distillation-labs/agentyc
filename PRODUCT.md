# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Coding agents and developers who need deterministic browser automation in an existing Chrome profile.

## Product Purpose

Agentyc lets agents work with logical browser task spaces in a dedicated Chrome profile. Success means the agent can perform a task through one clear Agentyc interface without taking over unrelated user tabs or claiming profile isolation.

## Positioning

Agentyc uses a host-authoritative broker and stable logical page identities rather than exposing raw browser identifiers or relying on an LLM browser planner.

## Operating Context

The browser is a dedicated Chrome profile launched by the user with a local DevTools Protocol endpoint enabled. It has independent cookies, sessions, and storage; do not copy state from the user's everyday profile. For the workflow defined in this brief, the agent invokes Agentyc MCP.

## Capabilities and Constraints

- Do not launch or download Chrome automatically.
- Keep browser target, tab, debugger, session, and frame identifiers internal.
- The user has clarified the desired boundary: MCP invokes the extension only to create tabs. The extension has no task-space UI and must not handle later navigation, snapshots, actions, or lifecycle controls.
- The current implementation routes later browser operations through the extension. A host-owned CDP backend is required before this boundary is satisfied; do not represent it as implemented until verified.
- The dedicated browser profile has separate state; this does not imply a security sandbox or guarantee site/account isolation.

## Brand Commitments

Use the product name “Agentyc.”

## Evidence on Hand

The repository contains a host-backed CLI/SDK, an MCP adapter, a Chrome MV3 extension, Native Messaging integration, and logical task-space/page contracts. Existing live evidence is partial; it does not establish the new extension boundary or release readiness.

## Product Principles

- MCP is the single agent-facing control plane for the selected workflow.
- The extension performs only the narrowly scoped tab-creation capability.
- The host remains authoritative for logical identity and control.
- Fail closed on unsupported or ambiguous browser outcomes; never replay an unknown mutation blindly.
- Preserve the user's existing Chrome state and unrelated tabs.
