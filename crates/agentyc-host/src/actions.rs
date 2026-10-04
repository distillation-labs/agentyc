//! Host-facing action journal result types.

use agentyc_core::{
    ActionId, ActionReceipt, ArtifactId, ArtifactKind, ContentHash, PageId, RequestId, SpaceId,
};
use serde::{Deserialize, Serialize};

/// A logical, one-time handle for an artifact produced by an action.
///
/// The handle contains no browser or filesystem identity. Bytes remain owned by
/// the bridge until the authorized caller consumes this exact handle.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArtifactHandle {
    /// Logical artifact identity issued by the extension/host bridge.
    pub artifact_id: ArtifactId,
    /// Action request that produced the artifact.
    pub request_id: RequestId,
    /// Durable action identity that owns the artifact.
    pub action_id: ActionId,
    /// Logical scope of the producing action.
    pub space_id: SpaceId,
    /// Optional logical page target.
    pub page_id: Option<PageId>,
    /// Artifact media category.
    pub artifact_kind: ArtifactKind,
    /// Declared byte length.
    pub total_bytes: u64,
    /// Declared transfer chunk count.
    pub chunk_count: u16,
    /// Validated content digest.
    pub digest: ContentHash,
    /// Whether the extension marked the artifact as redacted.
    pub redacted: bool,
}

/// Result of an action submission or execution step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionResult {
    /// Durable action receipt after the step.
    pub receipt: ActionReceipt,
    /// Artifact handle, when the action produced one.
    pub artifact: Option<ArtifactHandle>,
}

/// A bounded status lookup key used by later protocol adapters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionLookup {
    /// Durable action identity.
    pub action_id: ActionId,
}
