//! Transport-neutral contracts for logical browser task spaces.
//!
//! This crate owns logical identities, lifecycle records, local protocol envelopes,
//! bounded framing, snapshot provenance/deltas, action receipts, and events. It
//! intentionally has no browser, CDP, MCP, async-runtime, or filesystem types.
#![forbid(unsafe_code)]

pub mod actions;
pub mod errors;
pub mod events;
pub mod ids;
pub mod protocol;
pub mod records;
pub mod snapshots;
pub mod states;

pub use actions::{
    ActionOperation, ActionReceipt, ActionRequest, ActionTransitionError, Postcondition,
    UnknownReason,
};
pub use errors::{CoreError, ErrorCode, ErrorGuidance, FrameError};
pub use events::{EventCursor, EventKind, EventRecord, EventScope, GenerationWatermark};
pub use ids::*;
pub use protocol::{
    ArtifactEnvelope, ArtifactKind, CancelEnvelope, ClientMetadata,
    DEFAULT_MAX_FRAME_PAYLOAD_BYTES, Envelope, EventEnvelope, FRAME_PREFIX_BYTES, FrameDecoder,
    HelloEnvelope, HelloOkEnvelope, HostMetadata, MAX_ARTIFACT_CHUNK_BYTES,
    MAX_CONTROL_FRAME_PAYLOAD_BYTES, PROTOCOL_VERSION, RequestEnvelope, ResponseEnvelope,
    ResumeEnvelope, ResumeResult, decode_frame, decode_utf8, encode_frame, negotiate_version,
};
pub use records::{Lease, PageDescriptor, RetentionPolicy, SpaceDescriptor};
pub use snapshots::{
    DeltaError, DeltaLimits, DeltaOperation, ElementKind, ElementRef, SnapshotBody,
    SnapshotDecision, SnapshotDelta, SnapshotDocument, SnapshotElement, SnapshotEnvelope,
    SnapshotProvenance, SnapshotValidationError, TokenBudget, choose_snapshot_decision,
};
pub use states::{
    ActionStatus, CacheState, Capability, CompletionSource, DispatchState, NextAction,
    PageBindingState, PageLifecycle, PageOwnership, ProfileBindingState, ReconciliationState,
    ResyncReason, SnapshotCoverage, SnapshotMode, SpaceLifecycle,
};
