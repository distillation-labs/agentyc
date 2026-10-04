//! Host-owned logical snapshot cache with explicit dirty state.

use std::{
    cmp::Ordering,
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

use agentyc_core::{
    CacheState, ContentHash, ElementKey, FrameId, FrameVersion, Generation, PageDescriptor, PageId,
    RefEpoch, SnapshotBody, SnapshotCoverage, SnapshotEnvelope, SnapshotMode, SnapshotVersion,
    SpaceId, Timestamp, TokenBudget, TopologyVersion,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use agentyc_core::states::DirtyReason as CoreDirtyReason;

/// The logical generations a page proof is bound to.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct PageGeneration {
    /// Generation of the current managed target binding.
    pub target_generation: Generation,
    /// Generation of the current navigation.
    pub navigation_generation: Generation,
    /// Generation of the current document.
    pub document_generation: Generation,
}

impl PageGeneration {
    /// Capture a page's current logical generations.
    pub const fn from_page(page: &PageDescriptor) -> Self {
        Self {
            target_generation: page.target_generation,
            navigation_generation: page.navigation_generation,
            document_generation: page.document_generation,
        }
    }

    /// Return whether this proof matches a page exactly.
    pub fn matches_page(self, page: &PageDescriptor) -> bool {
        self.target_generation == page.target_generation
            && self.navigation_generation == page.navigation_generation
            && self.document_generation == page.document_generation
    }
}

/// Cache invalidation causes retained independently of the event stream.
///
/// These are logical causes only. They never contain browser target, tab, or
/// debugger identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DirtyReason {
    /// The event watermark skipped a sequence or the retained base lagged it.
    EventGap,
    /// A navigation changed the page's logical document.
    Navigation,
    /// The logical document was replaced without a new navigation event.
    DocumentReplaced,
    /// A logical frame was added, removed, or replaced.
    FrameReplaced,
    /// Layout or geometry changed.
    GeometryChanged,
    /// Scroll state changed.
    ScrollChanged,
    /// Raw evaluation may have changed page state outside the snapshot model.
    RawEvaluation,
    /// A takeover changed the authority generation.
    Takeover,
    /// A bridge or host reconnect invalidated prior provenance.
    Reconnect,
    /// A DOM mutation changed the logical tree.
    DomMutation,
    /// An action may have changed the page.
    Action,
    /// The target binding was replaced or lost.
    TargetReplaced,
    /// The bridge session was lost.
    SessionLost,
    /// The cache entry expired before it could be used.
    Expired,
    /// The source could not classify the change.
    Unknown,
}

/// Compatibility names for callers that distinguish cache and snapshot causes.
pub type CacheDirtyReason = DirtyReason;
/// Compatibility name for snapshot-oriented callers.
pub type SnapshotDirtyReason = DirtyReason;

impl DirtyReason {
    /// Return whether the cause makes the previous body unsafe as a base.
    pub const fn requires_resync(self) -> bool {
        matches!(
            self,
            Self::EventGap
                | Self::Navigation
                | Self::DocumentReplaced
                | Self::FrameReplaced
                | Self::RawEvaluation
                | Self::Takeover
                | Self::Reconnect
                | Self::TargetReplaced
                | Self::SessionLost
                | Self::Expired
        )
    }

    /// Return the legacy core dirty reason used by snapshot envelopes/events.
    pub const fn core_reason(self) -> CoreDirtyReason {
        match self {
            Self::Navigation | Self::DocumentReplaced => CoreDirtyReason::Navigation,
            Self::FrameReplaced => CoreDirtyReason::FrameChanged,
            Self::RawEvaluation => CoreDirtyReason::RawEvaluation,
            Self::Takeover => CoreDirtyReason::Takeover,
            Self::Reconnect => CoreDirtyReason::Reconnect,
            Self::TargetReplaced => CoreDirtyReason::TargetReplaced,
            Self::SessionLost => CoreDirtyReason::SessionLost,
            Self::DomMutation => CoreDirtyReason::DomMutation,
            Self::Action => CoreDirtyReason::Action,
            Self::EventGap => CoreDirtyReason::EventGap,
            Self::GeometryChanged => CoreDirtyReason::GeometryChanged,
            Self::ScrollChanged => CoreDirtyReason::ScrollChanged,
            Self::Expired => CoreDirtyReason::Expired,
            Self::Unknown => CoreDirtyReason::Unknown,
        }
    }

    const fn state(self) -> CacheState {
        if self.requires_resync() {
            CacheState::Invalidated
        } else {
            CacheState::Stale
        }
    }
}

/// A cache key containing every representation and provenance dimension that
/// can change the meaning or cost of a snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotCacheKey {
    /// Logical owning space.
    pub space_id: SpaceId,
    /// Logical owning page.
    pub page_id: PageId,
    /// Exact snapshot version represented by this variant.
    #[serde(default)]
    pub snapshot_version: SnapshotVersion,
    /// Snapshot representation mode.
    pub mode: SnapshotMode,
    /// Logical focused element, if the request is focus-scoped.
    pub focus: Option<ElementKey>,
    /// Token budget used to select or truncate the representation.
    pub budget: Option<TokenBudget>,
    /// Actual tokenizer identity used for metrics and selection.
    pub tokenizer: Option<String>,
    /// Logical frame topology version.
    pub topology_version: TopologyVersion,
    /// Page generations proven by the producer.
    pub generation: PageGeneration,
    /// Exact frame-version vector used by the representation.
    #[serde(default)]
    pub frame_versions: BTreeMap<FrameId, FrameVersion>,
}

impl SnapshotCacheKey {
    /// Construct the compatibility key used by the original page-only API.
    pub fn legacy(space_id: SpaceId, page_id: PageId) -> Self {
        Self {
            space_id,
            page_id,
            snapshot_version: SnapshotVersion::new(0),
            mode: SnapshotMode::Full,
            focus: None,
            budget: None,
            tokenizer: None,
            topology_version: TopologyVersion::new(0),
            generation: PageGeneration::default(),
            frame_versions: BTreeMap::new(),
        }
    }

    /// Construct the exact key represented by a snapshot envelope.
    pub fn from_envelope(envelope: &SnapshotEnvelope) -> Self {
        Self {
            space_id: envelope.space_id.clone(),
            page_id: envelope.page_id.clone(),
            snapshot_version: envelope.snapshot_version,
            mode: envelope.mode,
            focus: None,
            budget: envelope.budget,
            tokenizer: envelope.tokenizer.clone(),
            topology_version: envelope.topology_version,
            generation: PageGeneration {
                target_generation: Generation::new(0),
                navigation_generation: envelope.navigation_generation,
                document_generation: envelope.document_generation,
            },
            frame_versions: envelope.frame_versions.clone(),
        }
    }

    /// Construct a compatibility request key whose snapshot version can be set with
    /// [`Self::with_snapshot_version`].
    #[allow(clippy::too_many_arguments)]
    pub fn for_request(
        space_id: SpaceId,
        page_id: PageId,
        mode: SnapshotMode,
        focus: Option<ElementKey>,
        budget: Option<TokenBudget>,
        tokenizer: Option<String>,
        topology_version: TopologyVersion,
        generation: PageGeneration,
        frame_versions: BTreeMap<FrameId, FrameVersion>,
    ) -> Self {
        Self {
            space_id,
            page_id,
            snapshot_version: SnapshotVersion::new(0),
            mode,
            focus,
            budget,
            tokenizer,
            topology_version,
            generation,
            frame_versions,
        }
    }

    /// Return a key with an exact snapshot version.
    #[must_use]
    pub const fn with_snapshot_version(mut self, snapshot_version: SnapshotVersion) -> Self {
        self.snapshot_version = snapshot_version;
        self
    }

    /// Return a key with a different logical focus.
    #[must_use]
    pub fn with_focus(mut self, focus: Option<ElementKey>) -> Self {
        self.focus = focus;
        self
    }
}

impl Ord for SnapshotCacheKey {
    fn cmp(&self, other: &Self) -> Ordering {
        self.space_id
            .cmp(&other.space_id)
            .then_with(|| self.page_id.cmp(&other.page_id))
            .then_with(|| self.snapshot_version.cmp(&other.snapshot_version))
            .then_with(|| snapshot_mode_rank(self.mode).cmp(&snapshot_mode_rank(other.mode)))
            .then_with(|| self.focus.cmp(&other.focus))
            .then_with(|| budget_key(self.budget).cmp(&budget_key(other.budget)))
            .then_with(|| self.tokenizer.cmp(&other.tokenizer))
            .then_with(|| self.topology_version.cmp(&other.topology_version))
            .then_with(|| self.generation.cmp(&other.generation))
            .then_with(|| self.frame_versions.cmp(&other.frame_versions))
    }
}

impl PartialOrd for SnapshotCacheKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn snapshot_mode_rank(mode: SnapshotMode) -> u8 {
    match mode {
        SnapshotMode::Full => 0,
        SnapshotMode::Compact => 1,
        SnapshotMode::Delta => 2,
        SnapshotMode::Resync => 3,
    }
}

fn budget_key(budget: Option<TokenBudget>) -> (Option<u64>, Option<u64>) {
    budget.map_or((None, None), |budget| {
        (budget.serialized_limit, budget.model_context_limit)
    })
}

/// Metadata maintained by the in-memory keyed cache.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotCacheState {
    /// Current cache state.
    pub state: CacheState,
    /// Deduplicated causes that made the entry stale or invalid.
    pub dirty_reasons: Vec<DirtyReason>,
    /// Absolute logical expiry for the entry, if configured.
    pub expires_at: Option<Timestamp>,
    /// Last page generation proven by a successful rebuild.
    pub last_validated_generation: PageGeneration,
    /// Monotonic local write revision used for stale-writer protection.
    pub revision: u64,
}

/// A detailed cache lookup, including state and invalidation provenance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotCacheLookup {
    /// Exact key that was looked up.
    pub key: SnapshotCacheKey,
    /// Cached snapshot envelope.
    pub envelope: SnapshotEnvelope,
    /// Cache state at lookup time.
    pub state: CacheState,
    /// Dirty causes accumulated for this entry.
    pub dirty_reasons: Vec<DirtyReason>,
    /// Absolute logical expiry, if configured.
    pub expires_at: Option<Timestamp>,
    /// Last validated page generation.
    pub last_validated_generation: PageGeneration,
    /// Whether the lookup can be served without a rebuild.
    pub clean: bool,
}

/// Durable cache metadata for one logical page snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotCacheRecord {
    /// Last validated snapshot returned by the bridge.
    pub envelope: SnapshotEnvelope,
    /// Page generations proven when this snapshot was captured.
    #[serde(default)]
    pub generation: PageGeneration,
    /// Whether a mutation invalidated the cached representation.
    pub dirty: bool,
}

/// Result returned by a cache-only read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CachedSnapshot {
    /// Cached logical snapshot.
    pub envelope: SnapshotEnvelope,
    /// Whether the cache was clean when read.
    pub clean: bool,
}

/// Result returned by a broker snapshot read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotRead {
    /// Snapshot returned to the caller.
    pub envelope: SnapshotEnvelope,
    /// Whether the result came from a clean cache or a bridge scan.
    pub cache_state: CacheState,
    /// Whether the bridge was queried for this read.
    pub scan_performed: bool,
}

impl SnapshotRead {
    /// Return only cache/provenance metadata without exposing the snapshot body.
    pub fn metadata(&self) -> SnapshotMetadataRead {
        SnapshotMetadataRead {
            metadata: SnapshotMetadata::from_envelope(&self.envelope),
            cache_state: self.cache_state,
            scan_performed: self.scan_performed,
        }
    }
}

/// Body-free snapshot metadata suitable for adapter-facing clean-cache reads.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotMetadata {
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Owning logical page.
    pub page_id: PageId,
    /// Snapshot version and content identity.
    pub snapshot_version: SnapshotVersion,
    /// Content hash of the snapshot result.
    pub snapshot_hash: ContentHash,
    /// Result hash repeated for delta-aware consumers.
    pub result_hash: ContentHash,
    /// Logical frame topology version.
    pub topology_version: TopologyVersion,
    /// Navigation generation.
    pub navigation_generation: Generation,
    /// Document generation.
    pub document_generation: Generation,
    /// Exact per-frame version vector.
    pub frame_versions: BTreeMap<agentyc_core::FrameId, FrameVersion>,
    /// Representation mode without its body.
    pub mode: SnapshotMode,
    /// Operation count reported by the producer.
    pub operation_count: u32,
    /// Whether all frame versions were coherent.
    pub coherent: bool,
    /// Coverage status.
    pub coverage: SnapshotCoverage,
    /// Cache freshness.
    pub cache_state: CacheState,
    /// Whether a consumer must resync before using a body.
    pub resync_required: bool,
    /// Ref epoch associated with this result.
    pub refs_epoch: RefEpoch,
}

impl SnapshotMetadata {
    /// Copy the non-body fields from a validated snapshot envelope.
    pub fn from_envelope(envelope: &SnapshotEnvelope) -> Self {
        Self {
            space_id: envelope.space_id.clone(),
            page_id: envelope.page_id.clone(),
            snapshot_version: envelope.snapshot_version,
            snapshot_hash: envelope.snapshot_hash.clone(),
            result_hash: envelope.result_hash.clone(),
            topology_version: envelope.topology_version,
            navigation_generation: envelope.navigation_generation,
            document_generation: envelope.document_generation,
            frame_versions: envelope.frame_versions.clone(),
            mode: envelope.mode,
            operation_count: envelope.operation_count,
            coherent: envelope.coherent,
            coverage: envelope.coverage,
            cache_state: envelope.cache_state,
            resync_required: envelope.resync_required,
            refs_epoch: envelope.refs_epoch,
        }
    }
}

/// Public metadata-only result for a broker snapshot read.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotMetadataRead {
    /// Snapshot metadata without DOM or delta body.
    pub metadata: SnapshotMetadata,
    /// Whether the result came from a clean cache or a bridge scan.
    pub cache_state: CacheState,
    /// Whether the bridge was queried for this read.
    pub scan_performed: bool,
}

/// Errors raised by the standalone bounded cache.
#[derive(Debug, Error)]
pub enum SnapshotCacheError {
    /// A snapshot was not internally valid.
    #[error("invalid snapshot envelope: {0}")]
    Invalid(String),
    /// The configured cache entry bound was reached.
    #[error("snapshot cache is full")]
    Full,
    /// A snapshot exceeded the configured byte bound.
    #[error("snapshot exceeds cache byte bound")]
    TooLarge,
    /// The key does not describe the supplied snapshot.
    #[error("snapshot cache key does not match the snapshot")]
    KeyMismatch,
    /// A rebuild is already in flight for this exact key.
    #[error("snapshot rebuild is already in flight")]
    RebuildInFlight,
    /// A rebuild completed after a newer cache revision was written.
    #[error("snapshot rebuild writer is stale")]
    StaleWriter,
    /// A concurrent cache lock was poisoned.
    #[error("snapshot cache lock is poisoned")]
    Poisoned,
}

/// A rebuild token used to reject stale writers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotRebuildToken {
    key: SnapshotCacheKey,
    id: u64,
    revision: u64,
}

impl SnapshotRebuildToken {
    /// Return the exact logical cache key guarded by this token.
    pub fn key(&self) -> &SnapshotCacheKey {
        &self.key
    }

    /// Return the local guard identity.
    pub const fn id(&self) -> u64 {
        self.id
    }
}

/// Admission result for an explicit concurrent rebuild guard.
#[derive(Debug)]
#[allow(clippy::large_enum_variant)]
pub enum RebuildAdmission {
    /// This caller owns the rebuild and may commit one result.
    Leader(SnapshotRebuildGuard),
    /// Another caller already owns the rebuild for this key.
    InFlight,
}

/// A deterministic bounded cache with representation-aware keys.
#[derive(Debug, Clone)]
pub struct SnapshotCache {
    entries: BTreeMap<SnapshotCacheKey, SnapshotCacheRecord>,
    legacy_index: BTreeMap<(SpaceId, PageId), SnapshotCacheKey>,
    states: BTreeMap<SnapshotCacheKey, SnapshotCacheState>,
    revisions: BTreeMap<SnapshotCacheKey, u64>,
    in_flight: BTreeMap<SnapshotCacheKey, u64>,
    next_rebuild_id: u64,
    max_entries: usize,
    max_bytes: usize,
}

impl SnapshotCache {
    /// Construct an empty cache with explicit entry and serialized-byte bounds.
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            entries: BTreeMap::new(),
            legacy_index: BTreeMap::new(),
            states: BTreeMap::new(),
            revisions: BTreeMap::new(),
            in_flight: BTreeMap::new(),
            next_rebuild_id: 1,
            max_entries,
            max_bytes,
        }
    }

    /// Insert a clean snapshot using its exact envelope-derived key.
    pub fn insert(&mut self, envelope: SnapshotEnvelope) -> Result<(), SnapshotCacheError> {
        let key = SnapshotCacheKey::from_envelope(&envelope);
        self.insert_with_key(key, envelope)
    }

    /// Insert a clean snapshot under an exact representation/provenance key.
    pub fn insert_with_key(
        &mut self,
        key: SnapshotCacheKey,
        envelope: SnapshotEnvelope,
    ) -> Result<(), SnapshotCacheError> {
        self.insert_with_expiry(key, envelope, None)
    }

    /// Insert a clean snapshot with an absolute logical expiry.
    pub fn insert_with_expiry(
        &mut self,
        key: SnapshotCacheKey,
        envelope: SnapshotEnvelope,
        expires_at: Option<Timestamp>,
    ) -> Result<(), SnapshotCacheError> {
        self.validate_key(&key, &envelope)?;
        let bytes = serde_json::to_vec(&envelope)
            .map_err(|error| SnapshotCacheError::Invalid(error.to_string()))?;
        if bytes.len() > self.max_bytes {
            return Err(SnapshotCacheError::TooLarge);
        }
        if !self.entries.contains_key(&key) && self.len() >= self.max_entries {
            return Err(SnapshotCacheError::Full);
        }
        self.store_clean(key, envelope, expires_at);
        Ok(())
    }

    /// Return the compatibility page-level lookup without any browser scan.
    pub fn get(&self, space_id: &SpaceId, page_id: &PageId) -> Option<CachedSnapshot> {
        let key = self
            .legacy_index
            .get(&(space_id.clone(), page_id.clone()))?;
        self.get_with_key(key)
    }

    /// Return an exact keyed lookup without any browser scan.
    pub fn get_with_key(&self, key: &SnapshotCacheKey) -> Option<CachedSnapshot> {
        self.entries.get(key).map(|record| CachedSnapshot {
            envelope: record.envelope.clone(),
            clean: self.is_clean(key, record),
        })
    }

    /// Return detailed state for an exact key without any browser scan.
    pub fn lookup(&self, key: &SnapshotCacheKey) -> Option<SnapshotCacheLookup> {
        let record = self.entries.get(key)?;
        let state = self.states.get(key)?;
        Some(SnapshotCacheLookup {
            key: key.clone(),
            envelope: record.envelope.clone(),
            state: state.state,
            dirty_reasons: state.dirty_reasons.clone(),
            expires_at: state.expires_at,
            last_validated_generation: state.last_validated_generation,
            clean: self.is_clean(key, record),
        })
    }

    /// Return detailed state at a logical time, expiring the entry first.
    pub fn lookup_at(
        &mut self,
        key: &SnapshotCacheKey,
        now: Timestamp,
    ) -> Option<SnapshotCacheLookup> {
        self.expire_if_needed(key, now);
        self.lookup(key)
    }

    /// Return a clean compatibility read; dirty or expired entries miss.
    pub fn get_clean(&self, space_id: &SpaceId, page_id: &PageId) -> Option<SnapshotEnvelope> {
        let key = self
            .legacy_index
            .get(&(space_id.clone(), page_id.clone()))?;
        self.get_clean_with_key(key)
    }

    /// Return a clean exact keyed read without any browser scan.
    pub fn get_clean_with_key(&self, key: &SnapshotCacheKey) -> Option<SnapshotEnvelope> {
        let record = self.entries.get(key)?;
        if !self.is_clean(key, record) {
            return None;
        }
        let mut envelope = record.envelope.clone();
        envelope.cache_state = CacheState::Cached;
        Some(envelope)
    }

    /// Return a clean exact keyed read after applying logical expiry.
    pub fn get_clean_at(
        &mut self,
        key: &SnapshotCacheKey,
        now: Timestamp,
    ) -> Option<SnapshotEnvelope> {
        self.expire_if_needed(key, now);
        self.get_clean_with_key(key)
    }

    /// Return the current state for an exact key.
    pub fn state(&self, key: &SnapshotCacheKey) -> Option<CacheState> {
        self.states.get(key).map(|state| state.state)
    }

    /// Return accumulated dirty causes for an exact key.
    pub fn dirty_reasons(&self, key: &SnapshotCacheKey) -> Option<&[DirtyReason]> {
        self.states
            .get(key)
            .map(|state| state.dirty_reasons.as_slice())
    }

    /// Mark every representation for one logical page dirty without scanning it.
    pub fn mark_dirty(&mut self, space_id: &SpaceId, page_id: &PageId) -> bool {
        self.mark_dirty_with_reason(space_id, page_id, DirtyReason::Unknown)
    }

    /// Mark every representation for one page dirty with an explicit cause.
    pub fn mark_dirty_with_reason(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        reason: DirtyReason,
    ) -> bool {
        let keys: Vec<_> = self
            .entries
            .keys()
            .filter(|key| key.space_id == *space_id && key.page_id == *page_id)
            .cloned()
            .collect();
        let changed = !keys.is_empty();
        for key in keys {
            self.mark_key_dirty(&key, reason);
        }
        changed
    }

    /// Mark one exact representation dirty with an explicit cause.
    pub fn mark_dirty_for_key(&mut self, key: &SnapshotCacheKey, reason: DirtyReason) -> bool {
        if !self.entries.contains_key(key) {
            return false;
        }
        self.mark_key_dirty(key, reason);
        true
    }

    /// Mark all representation variants for one logical page invalid with a cause.
    pub fn invalidate_with_reason(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        reason: DirtyReason,
    ) -> bool {
        self.mark_dirty_with_reason(space_id, page_id, reason)
    }

    /// Remove all representation variants for one logical page.
    pub fn invalidate(&mut self, space_id: &SpaceId, page_id: &PageId) -> bool {
        let keys: Vec<_> = self
            .entries
            .keys()
            .filter(|key| key.space_id == *space_id && key.page_id == *page_id)
            .cloned()
            .collect();
        let removed = !keys.is_empty();
        for key in keys {
            self.remove_key(&key);
        }
        self.cancel_scope_rebuilds(space_id, page_id);
        removed
    }

    /// Mark one exact representation invalid with a cause while retaining its metadata.
    pub fn invalidate_key_with_reason(
        &mut self,
        key: &SnapshotCacheKey,
        reason: DirtyReason,
    ) -> bool {
        self.mark_dirty_for_key(key, reason)
    }

    /// Remove one exact representation variant.
    pub fn invalidate_key(&mut self, key: &SnapshotCacheKey) -> bool {
        let removed = self.remove_key(key);
        self.cancel_rebuild(key);
        removed
    }

    /// Begin a guarded rebuild for one exact key.
    pub fn begin_rebuild(
        &mut self,
        key: SnapshotCacheKey,
    ) -> Result<SnapshotRebuildToken, SnapshotCacheError> {
        if self.in_flight.contains_key(&key) {
            return Err(SnapshotCacheError::RebuildInFlight);
        }
        let id = self.next_rebuild_id;
        self.next_rebuild_id = self.next_rebuild_id.saturating_add(1).max(1);
        let revision = self.revisions.get(&key).copied().unwrap_or(0);
        self.in_flight.insert(key.clone(), id);
        Ok(SnapshotRebuildToken { key, id, revision })
    }

    /// Commit a guarded rebuild only if no newer writer invalidated it.
    pub fn complete_rebuild(
        &mut self,
        token: SnapshotRebuildToken,
        envelope: SnapshotEnvelope,
    ) -> Result<(), SnapshotCacheError> {
        let current_id = self.in_flight.get(&token.key).copied();
        let current_revision = self.revisions.get(&token.key).copied().unwrap_or(0);
        if current_id != Some(token.id) || current_revision != token.revision {
            return Err(SnapshotCacheError::StaleWriter);
        }
        self.in_flight.remove(&token.key);
        self.insert_with_key(token.key, envelope)
    }

    /// Abort a guarded rebuild, leaving the old cache value untouched.
    pub fn abort_rebuild(&mut self, token: &SnapshotRebuildToken) -> bool {
        if self.in_flight.get(&token.key) == Some(&token.id) {
            self.in_flight.remove(&token.key);
            true
        } else {
            false
        }
    }

    /// Return whether an exact key currently has a rebuild owner.
    pub fn rebuild_in_flight(&self, key: &SnapshotCacheKey) -> bool {
        self.in_flight.contains_key(key)
    }

    /// Number of retained representation variants.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Return whether the cache contains no entries.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn validate_key(
        &self,
        key: &SnapshotCacheKey,
        envelope: &SnapshotEnvelope,
    ) -> Result<(), SnapshotCacheError> {
        let generation_matches = key.generation.navigation_generation == Generation::new(0)
            || key.generation.navigation_generation == envelope.navigation_generation;
        let document_matches = key.generation.document_generation == Generation::new(0)
            || key.generation.document_generation == envelope.document_generation;
        let topology_matches = key.topology_version == TopologyVersion::new(0)
            || key.topology_version == envelope.topology_version;
        let frames_match =
            key.frame_versions.is_empty() || key.frame_versions == envelope.frame_versions;
        if key.space_id != envelope.space_id
            || key.page_id != envelope.page_id
            || (key.snapshot_version != SnapshotVersion::new(0)
                && key.snapshot_version != envelope.snapshot_version)
            || key.mode != envelope.mode
            || key.budget != envelope.budget
            || key.tokenizer != envelope.tokenizer
            || !generation_matches
            || !document_matches
            || !topology_matches
            || !frames_match
        {
            return Err(SnapshotCacheError::KeyMismatch);
        }
        envelope
            .validate()
            .map_err(|error| SnapshotCacheError::Invalid(error.to_string()))
    }

    fn store_clean(
        &mut self,
        key: SnapshotCacheKey,
        envelope: SnapshotEnvelope,
        expires_at: Option<Timestamp>,
    ) {
        let revision = self
            .revisions
            .get(&key)
            .copied()
            .unwrap_or(0)
            .saturating_add(1);
        let generation = key.generation;
        let page_key = (key.space_id.clone(), key.page_id.clone());
        self.entries.insert(
            key.clone(),
            SnapshotCacheRecord {
                envelope,
                generation,
                dirty: false,
            },
        );
        self.states.insert(
            key.clone(),
            SnapshotCacheState {
                state: CacheState::Fresh,
                dirty_reasons: Vec::new(),
                expires_at,
                last_validated_generation: generation,
                revision,
            },
        );
        self.revisions.insert(key.clone(), revision);
        self.legacy_index.insert(page_key, key.clone());
        self.in_flight.remove(&key);
    }

    fn is_clean(&self, key: &SnapshotCacheKey, record: &SnapshotCacheRecord) -> bool {
        self.states
            .get(key)
            .is_some_and(|state| state.state == CacheState::Fresh && !record.dirty)
    }

    fn mark_key_dirty(&mut self, key: &SnapshotCacheKey, reason: DirtyReason) {
        let Some(record) = self.entries.get_mut(key) else {
            return;
        };
        let state = self
            .states
            .entry(key.clone())
            .or_insert_with(|| SnapshotCacheState {
                state: CacheState::Fresh,
                dirty_reasons: Vec::new(),
                expires_at: None,
                last_validated_generation: record.generation,
                revision: 0,
            });
        let reason_state = reason.state();
        state.state =
            if state.state == CacheState::Invalidated || reason_state == CacheState::Invalidated {
                CacheState::Invalidated
            } else {
                CacheState::Stale
            };
        if !state.dirty_reasons.contains(&reason) {
            state.dirty_reasons.push(reason);
            state.dirty_reasons.sort();
        }
        state.revision = state.revision.saturating_add(1);
        record.dirty = true;
        record.envelope.cache_state = state.state;
        let core_reason = reason.core_reason();
        if !record.envelope.dirty_reasons.contains(&core_reason) {
            record.envelope.dirty_reasons.push(core_reason);
        }
        self.revisions.insert(key.clone(), state.revision);
        self.cancel_rebuild(key);
    }

    fn expire_if_needed(&mut self, key: &SnapshotCacheKey, now: Timestamp) {
        if self
            .states
            .get(key)
            .and_then(|state| state.expires_at)
            .is_some_and(|expiry| expiry.get() <= now.get())
        {
            self.mark_key_dirty(key, DirtyReason::Expired);
        }
    }

    fn remove_key(&mut self, key: &SnapshotCacheKey) -> bool {
        let removed = self.entries.remove(key).is_some();
        self.states.remove(key);
        let page_key = (key.space_id.clone(), key.page_id.clone());
        if self.legacy_index.get(&page_key) == Some(key) {
            self.legacy_index.remove(&page_key);
            if let Some(replacement) = self
                .entries
                .keys()
                .find(|candidate| {
                    candidate.space_id == key.space_id && candidate.page_id == key.page_id
                })
                .cloned()
            {
                self.legacy_index.insert(page_key, replacement);
            }
        }
        self.revisions.insert(
            key.clone(),
            self.revisions
                .get(key)
                .copied()
                .unwrap_or(0)
                .saturating_add(1),
        );
        removed
    }

    fn cancel_rebuild(&mut self, key: &SnapshotCacheKey) {
        self.in_flight.remove(key);
        self.revisions.insert(
            key.clone(),
            self.revisions
                .get(key)
                .copied()
                .unwrap_or(0)
                .saturating_add(1),
        );
    }

    fn cancel_scope_rebuilds(&mut self, space_id: &SpaceId, page_id: &PageId) {
        let keys: Vec<_> = self
            .in_flight
            .keys()
            .filter(|key| key.space_id == *space_id && key.page_id == *page_id)
            .cloned()
            .collect();
        for key in keys {
            self.cancel_rebuild(&key);
        }
    }
}

/// Thread-safe cache wrapper that provides explicit single-flight ownership.
#[derive(Debug, Clone)]
pub struct ConcurrentSnapshotCache {
    inner: Arc<Mutex<SnapshotCache>>,
}

impl ConcurrentSnapshotCache {
    /// Wrap an existing bounded cache.
    pub fn new(cache: SnapshotCache) -> Self {
        Self {
            inner: Arc::new(Mutex::new(cache)),
        }
    }

    /// Construct a thread-safe bounded cache.
    pub fn with_limits(max_entries: usize, max_bytes: usize) -> Self {
        Self::new(SnapshotCache::new(max_entries, max_bytes))
    }

    /// Insert a validated clean representation into the bounded cache.
    pub fn insert(&self, envelope: SnapshotEnvelope) -> Result<(), SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        cache.insert(envelope)
    }

    /// Begin a single-flight rebuild or report that another caller owns it.
    pub fn try_begin_rebuild(
        &self,
        key: SnapshotCacheKey,
    ) -> Result<RebuildAdmission, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        match cache.begin_rebuild(key) {
            Ok(token) => Ok(RebuildAdmission::Leader(SnapshotRebuildGuard {
                cache: self.clone(),
                token,
                finished: false,
            })),
            Err(SnapshotCacheError::RebuildInFlight) => Ok(RebuildAdmission::InFlight),
            Err(error) => Err(error),
        }
    }

    /// Return an exact cache lookup without performing a browser scan.
    pub fn get_with_key(
        &self,
        key: &SnapshotCacheKey,
    ) -> Result<Option<CachedSnapshot>, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.get_with_key(key))
    }

    /// Return detailed state for an exact key without performing a browser scan.
    pub fn lookup(
        &self,
        key: &SnapshotCacheKey,
    ) -> Result<Option<SnapshotCacheLookup>, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.lookup(key))
    }

    /// Return detailed state after applying logical expiry.
    pub fn lookup_at(
        &self,
        key: &SnapshotCacheKey,
        now: Timestamp,
    ) -> Result<Option<SnapshotCacheLookup>, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.lookup_at(key, now))
    }

    /// Return a clean page-level compatibility read without performing a browser scan.
    pub fn get_clean(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
    ) -> Result<Option<SnapshotEnvelope>, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.get_clean(space_id, page_id))
    }

    /// Return a clean exact read without performing a browser scan.
    pub fn get_clean_with_key(
        &self,
        key: &SnapshotCacheKey,
    ) -> Result<Option<SnapshotEnvelope>, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.get_clean_with_key(key))
    }

    /// Return the number of retained representation variants.
    pub fn len(&self) -> Result<usize, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.len())
    }

    /// Return whether this concurrent cache contains no entries.
    pub fn is_empty(&self) -> Result<bool, SnapshotCacheError> {
        let cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.is_empty())
    }

    /// Mark a logical page dirty without performing a browser scan.
    pub fn mark_dirty_with_reason(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        reason: DirtyReason,
    ) -> Result<bool, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.mark_dirty_with_reason(space_id, page_id, reason))
    }

    /// Mark all representation variants invalid with a cause.
    pub fn invalidate_with_reason(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        reason: DirtyReason,
    ) -> Result<bool, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.invalidate_with_reason(space_id, page_id, reason))
    }

    /// Mark every retained representation dirty with one cause.
    pub fn invalidate_all_with_reason(
        &self,
        reason: DirtyReason,
    ) -> Result<usize, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        let keys: Vec<_> = cache.entries.keys().cloned().collect();
        let count = keys.len();
        for key in keys {
            cache.mark_key_dirty(&key, reason);
        }
        Ok(count)
    }

    /// Invalidate all representation variants for a logical page.
    pub fn invalidate(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
    ) -> Result<bool, SnapshotCacheError> {
        let mut cache = self
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)?;
        Ok(cache.invalidate(space_id, page_id))
    }
}

/// Single-flight owner that commits only while its cache revision is current.
#[derive(Debug)]
pub struct SnapshotRebuildGuard {
    cache: ConcurrentSnapshotCache,
    token: SnapshotRebuildToken,
    finished: bool,
}

impl SnapshotRebuildGuard {
    /// Return the exact logical key owned by this guard.
    pub fn key(&self) -> &SnapshotCacheKey {
        self.token.key()
    }

    /// Commit one validated snapshot and release the single-flight slot.
    pub fn commit(mut self, envelope: SnapshotEnvelope) -> Result<(), SnapshotCacheError> {
        let result = self
            .cache
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)
            .and_then(|mut cache| cache.complete_rebuild(self.token.clone(), envelope));
        self.finished = true;
        result
    }

    /// Explicitly abandon the rebuild without changing the retained value.
    pub fn abort(mut self) -> Result<bool, SnapshotCacheError> {
        let result = self
            .cache
            .inner
            .lock()
            .map_err(|_| SnapshotCacheError::Poisoned)
            .map(|mut cache| cache.abort_rebuild(&self.token));
        self.finished = true;
        result
    }
}

impl Drop for SnapshotRebuildGuard {
    fn drop(&mut self) {
        if !self.finished
            && let Ok(mut cache) = self.cache.inner.lock()
        {
            cache.abort_rebuild(&self.token);
        }
    }
}

/// Build a valid empty full snapshot for deterministic bridge implementations.
pub fn empty_snapshot(space_id: SpaceId, page_id: PageId) -> SnapshotEnvelope {
    let hash = ContentHash::from_bytes(b"[]");
    SnapshotEnvelope {
        schema_version: 1,
        space_id,
        page_id,
        snapshot_version: SnapshotVersion::new(1),
        snapshot_hash: hash.clone(),
        base_snapshot_version: None,
        base_hash: None,
        result_hash: hash,
        delta_sequence: None,
        topology_version: TopologyVersion::new(1),
        navigation_generation: Generation::new(1),
        document_generation: Generation::new(1),
        frame_versions: BTreeMap::<agentyc_core::FrameId, FrameVersion>::new(),
        changed: Vec::new(),
        delta_or_elements: SnapshotBody::Elements {
            elements: Vec::new(),
        },
        mode: SnapshotMode::Full,
        operation_count: 0,
        coherent: true,
        coverage: SnapshotCoverage::Complete,
        dirty_reasons: Vec::<CoreDirtyReason>::new(),
        cache_state: CacheState::Fresh,
        resync_reason: None,
        transport_bytes: 0,
        utf8_bytes: 2,
        serialized_tokens: 0,
        model_context_tokens: 0,
        tokenizer: None,
        budget: None,
        omitted: Vec::new(),
        truncated: false,
        resync_required: false,
        refs_epoch: RefEpoch::new(1),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_reads_do_not_change_cache_state_or_require_a_scan() {
        let space = SpaceId::from_suffix("one").expect("space");
        let page = PageId::from_suffix("one").expect("page");
        let mut cache = SnapshotCache::new(2, 1024 * 1024);
        cache
            .insert(empty_snapshot(space.clone(), page.clone()))
            .expect("insert");
        let cached = cache.get_clean(&space, &page).expect("clean cache");
        assert_eq!(cached.cache_state, CacheState::Cached);
        assert!(cache.get(&space, &page).expect("entry").clean);
        cache.mark_dirty(&space, &page);
        assert!(cache.get_clean(&space, &page).is_none());
    }

    #[test]
    fn keyed_variants_are_partitioned_by_representation_dimensions() {
        let space = SpaceId::from_suffix("keyed").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let envelope = empty_snapshot(space, page);
        let key = SnapshotCacheKey::from_envelope(&envelope);

        let mut compact = key.clone();
        compact.mode = SnapshotMode::Compact;
        assert_ne!(key, compact);

        let mut focused = key.clone().with_focus(Some(
            ElementKey::from_suffix("focused").expect("element key"),
        ));
        focused.budget = Some(TokenBudget {
            serialized_limit: Some(100),
            model_context_limit: Some(200),
        });
        focused.tokenizer = Some("test-tokenizer".to_owned());
        focused.topology_version = TopologyVersion::new(2);
        focused.generation.document_generation = Generation::new(2);
        focused.frame_versions.insert(
            FrameId::from_suffix("main").expect("frame"),
            FrameVersion::new(3),
        );
        assert_ne!(key, focused);
    }

    #[test]
    fn dirty_reasons_transition_stale_and_invalidated_without_a_scan() {
        let space = SpaceId::from_suffix("dirty").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let envelope = empty_snapshot(space, page);
        let key = SnapshotCacheKey::from_envelope(&envelope);
        let mut cache = SnapshotCache::new(2, 1024 * 1024);
        cache
            .insert_with_key(key.clone(), envelope)
            .expect("insert");

        assert!(cache.mark_dirty_for_key(&key, DirtyReason::GeometryChanged));
        assert_eq!(cache.state(&key), Some(CacheState::Stale));
        assert_eq!(
            cache.dirty_reasons(&key),
            Some([DirtyReason::GeometryChanged].as_slice())
        );
        assert!(cache.mark_dirty_for_key(&key, DirtyReason::RawEvaluation));
        assert_eq!(cache.state(&key), Some(CacheState::Invalidated));
        assert_eq!(
            cache.dirty_reasons(&key),
            Some([DirtyReason::GeometryChanged, DirtyReason::RawEvaluation].as_slice())
        );
        assert!(cache.mark_dirty_for_key(&key, DirtyReason::ScrollChanged));
        assert_eq!(cache.state(&key), Some(CacheState::Invalidated));
        assert!(cache.get_clean_with_key(&key).is_none());
    }

    #[test]
    fn expiry_requires_resync_and_retains_expired_reason() {
        let space = SpaceId::from_suffix("expiry").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let envelope = empty_snapshot(space, page);
        let key = SnapshotCacheKey::from_envelope(&envelope);
        let mut cache = SnapshotCache::new(2, 1024 * 1024);
        cache
            .insert_with_expiry(key.clone(), envelope, Some(Timestamp::new(5)))
            .expect("insert");

        let lookup = cache.lookup_at(&key, Timestamp::new(5)).expect("lookup");
        assert_eq!(lookup.state, CacheState::Invalidated);
        assert!(!lookup.clean);
        assert_eq!(lookup.dirty_reasons, vec![DirtyReason::Expired]);
        assert!(cache.get_clean_at(&key, Timestamp::new(6)).is_none());
    }

    #[test]
    fn single_flight_and_stale_writer_protection_are_deterministic() {
        let space = SpaceId::from_suffix("flight").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let envelope = empty_snapshot(space, page);
        let key = SnapshotCacheKey::from_envelope(&envelope);
        let mut cache = SnapshotCache::new(2, 1024 * 1024);
        cache
            .insert_with_key(key.clone(), envelope.clone())
            .expect("insert");

        let token = cache.begin_rebuild(key.clone()).expect("leader");
        assert!(matches!(
            cache.begin_rebuild(key.clone()),
            Err(SnapshotCacheError::RebuildInFlight)
        ));
        cache.mark_dirty_for_key(&key, DirtyReason::EventGap);
        assert!(matches!(
            cache.complete_rebuild(token, envelope.clone()),
            Err(SnapshotCacheError::StaleWriter)
        ));

        let concurrent = ConcurrentSnapshotCache::new(cache);
        let leader = match concurrent
            .try_begin_rebuild(key.clone())
            .expect("admission")
        {
            RebuildAdmission::Leader(guard) => guard,
            RebuildAdmission::InFlight => panic!("expected leader"),
        };
        assert!(matches!(
            concurrent.try_begin_rebuild(key).expect("admission"),
            RebuildAdmission::InFlight
        ));
        leader.abort().expect("abort");
    }
}
