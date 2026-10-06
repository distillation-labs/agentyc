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
mod cdp;
pub mod chrome_bridge;
pub mod context;
pub mod dialogs;
pub mod downloads;
pub mod error;
pub mod event_router;
pub mod events;
pub mod host;
pub mod leases;
pub mod ledger;
pub mod local_ipc;
pub mod mocks;
pub mod native_messaging;
pub mod observability;
pub mod protocol;
pub mod refs;
pub mod scheduler;
pub mod snapshots;
pub mod trace_policy;
pub mod waits;

pub use actionability::{
    ACTIONABILITY_PAYLOAD_KEY, ActionKind, Actionability, ActionabilityChecker,
    ActionabilityEvidence, ActionabilityInput, ActionabilityProof, ActionabilityResult,
    ActionabilityState, ELEMENT_REF_PAYLOAD_KEY, EVIDENCE_PAYLOAD_KEY, FRAME_SCOPE_PAYLOAD_KEY,
    PROVENANCE_PAYLOAD_KEY, PostconditionVerifier, REF_PAYLOAD_KEY, check_actionability,
    requires_element_actionability, validate_request_actionability, verify_postcondition,
};
pub use actions::{ActionLookup, ActionResult, ArtifactHandle};
pub use agentyc_core;
pub use bridge::{
    Bridge, BridgeDispatchResult, BridgeReconcileResult, BridgeRouter, ExtensionEpochs, FakeBridge,
    FenceResult, NullBridge,
};
pub use broker::{Broker, Connection, HostDegradedReason, HostLifecycle, canonical_action_hash};
pub use context::{
    ContextBody, ContextBuilder, ContextCacheKey, ContextFocus, ContextMetadata, ContextMode,
    ContextOptions, ContextOutput, ContextRepresentation, ContextRequest, ContextRequestMode,
    FocusKey, RedactionPolicy, TokenMetrics, Tokenizer, TokenizerError, UnicodeScalarTokenizer,
};
pub use dialogs::{
    DialogDecision, DialogFailure, DialogId, DialogKind, DialogManager, DialogRecord,
    DialogRequest, DialogResolution,
};
pub use downloads::{
    DownloadFailure, DownloadManager, DownloadRecord, DownloadRequest, DownloadStatus,
};
pub use error::{HostError, LedgerError};
pub use event_router::{
    EventRouter, RouterIngest, RouterLimits, RouterResyncReason, RouterWatermark,
};
pub use events::{EventBatch, EventQuery};
pub use host::{
    ENDPOINT_METADATA_FILENAME, ENDPOINT_METADATA_SCHEMA_VERSION, EndpointMetadata, Host,
    NATIVE_FORWARD_SOCKET_FILENAME, endpoint_metadata_path, native_forward_socket_path,
    publish_endpoint_metadata, read_endpoint_metadata, remove_endpoint_metadata_if_owner,
};
pub use leases::{
    AuthorityTicket, ControlReturn, ControlTicket, LeaseGrant, TakeoverResult, UserIntentTicket,
};
pub use ledger::{
    FencePurpose, LEDGER_SCHEMA_VERSION, Ledger, LedgerLimits, LedgerState, PendingFenceRecord,
    TakeoverProofRecord,
};
pub use local_ipc::{
    DEFAULT_LOCAL_SOCKET_FILENAME, LocalHostServer, LocalSocketClient, configured_socket_path,
};
pub use mocks::{MockAction, MockAuthorization, MockFailure, MockManager, MockRecord, MockRequest};
pub use native_messaging::{
    DEFAULT_NATIVE_HANDSHAKE_TIMEOUT, DEFAULT_NATIVE_REQUEST_TIMEOUT, MAX_NATIVE_CONTROL_BYTES,
    MAX_NATIVE_EVENT_QUEUE, MAX_NATIVE_PENDING_REQUESTS, MAX_NATIVE_READ_CHUNK_BYTES,
    NativeArtifact, NativeHello, NativeHostError, NativeMessagingBridge, NativeMessagingConfig,
    normalize_extension_origin,
};
#[cfg(unix)]
pub use native_messaging::{NativeForwardServer, forward_stdio_to_owner};
pub use observability::{
    LogEntry, LogLevel, LogicalScope, NetworkEntry, ObservabilityLimits, ObservabilityStore,
    ObservationScope, TraceEntry, capability_unavailable, redact_headers, redact_text,
};
pub use protocol::{LocalProtocolClient, LocalProtocolServer, ProtocolClient, ProtocolServer};
pub use refs::{
    RefInvalidationReason, RefRecord, RefRegistry, RefRegistryLimits, RefStaleHint, RefTombstone,
    RefValidation,
};
pub use scheduler::{
    Backpressure, BackpressureKind, MutationPermit, ReadPermit, Scheduler, SchedulerLimits,
    SchedulerSnapshot, SchedulerWaitError,
};
pub use snapshots::{
    CacheDirtyReason, CachedSnapshot, ConcurrentSnapshotCache, DirtyReason, PageGeneration,
    RebuildAdmission, SnapshotCache, SnapshotCacheError, SnapshotCacheKey, SnapshotCacheLookup,
    SnapshotCacheRecord, SnapshotCacheState, SnapshotDirtyReason, SnapshotMetadata,
    SnapshotMetadataRead, SnapshotRead, SnapshotRebuildGuard, SnapshotRebuildToken, empty_snapshot,
};
pub use trace_policy::{
    AuthorizationOutcome, HostConfirmation, IntentBinding, IntentTicketStore,
    MAX_INTENT_TICKET_TTL_MS, MAX_INTENT_TICKETS, PageAuthorityClaim, PolicyScope,
    SensitiveBoundary, operation_requires_intent, payload_requires_intent, reject_page_authority,
    request_requires_intent,
};
pub use waits::{
    CancellationToken, Clock, DownloadState, DownloadWait, ElementState, ElementWait, FakeClock,
    HistoryDirection, HistoryWait, NavigationKind, NetworkIdleWait, PageWait, ReloadWait,
    RequestWait, ResponseWait, StableDomWait, TextMatcher, UrlWait, WaitCondition, WaitEngine,
    WaitHandle, WaitOutcome, WaitPoll, WaitRegistration,
};
