//! Host-owned broker core for logical browser task spaces.
//!
//! The crate owns durable logical state, leases, action receipts, cache
//! provenance, and broker events. Browser-specific identifiers are deliberately
//! absent from public records; a later extension/CDP implementation enters only
//! through [`Bridge`].
#![forbid(unsafe_code)]

pub mod actionability;
pub mod actions;
pub mod bridge;
pub mod broker;
pub mod context;
pub mod error;
pub mod event_router;
pub mod events;
pub mod host;
pub mod leases;
pub mod ledger;
pub mod native_messaging;
pub mod protocol;
pub mod refs;
pub mod snapshots;
pub mod waits;

pub use actionability::{
    ActionKind, Actionability, ActionabilityChecker, ActionabilityEvidence, ActionabilityProof,
    ActionabilityResult, ActionabilityState, PostconditionVerifier, check_actionability,
    verify_postcondition,
};
pub use actions::{ActionLookup, ActionResult};
pub use agentyc_core;
pub use bridge::{
    Bridge, BridgeDispatchResult, BridgeReconcileResult, FakeBridge, FenceResult, NullBridge,
};
pub use broker::{Broker, Connection, HostLifecycle, canonical_action_hash};
pub use context::{
    ContextBody, ContextBuilder, ContextMetadata, ContextMode, ContextOptions, ContextOutput,
    ContextRepresentation, ContextRequest, ContextRequestMode, RedactionPolicy, TokenMetrics,
};
pub use error::{HostError, LedgerError};
pub use event_router::{
    EventRouter, RouterIngest, RouterLimits, RouterResyncReason, RouterWatermark,
};
pub use events::{EventBatch, EventQuery};
pub use host::Host;
pub use leases::{AuthorityTicket, ControlReturn, ControlTicket, LeaseGrant, TakeoverResult};
pub use ledger::{
    FencePurpose, LEDGER_SCHEMA_VERSION, Ledger, LedgerLimits, LedgerState, PendingFenceRecord,
    TakeoverProofRecord,
};
pub use native_messaging::{
    DEFAULT_NATIVE_HANDSHAKE_TIMEOUT, DEFAULT_NATIVE_REQUEST_TIMEOUT, MAX_NATIVE_CONTROL_BYTES,
    MAX_NATIVE_EVENT_QUEUE, MAX_NATIVE_PENDING_REQUESTS, MAX_NATIVE_READ_CHUNK_BYTES, NativeHello,
    NativeHostError, NativeMessagingBridge, NativeMessagingConfig, normalize_extension_origin,
};
pub use protocol::{LocalProtocolClient, LocalProtocolServer, ProtocolClient, ProtocolServer};
pub use refs::{RefInvalidationReason, RefRecord, RefRegistry, RefRegistryLimits, RefTombstone};
pub use snapshots::{
    CachedSnapshot, PageGeneration, SnapshotCache, SnapshotCacheError, SnapshotCacheRecord,
    SnapshotMetadata, SnapshotMetadataRead, SnapshotRead, empty_snapshot,
};
pub use waits::{
    CancellationToken, Clock, FakeClock, WaitCondition, WaitEngine, WaitHandle, WaitOutcome,
    WaitPoll, WaitRegistration,
};
