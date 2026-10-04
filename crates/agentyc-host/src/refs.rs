//! Bounded logical element-reference registry with exact snapshot provenance.
//!
//! The registry is intentionally independent of browser handles. A reference is
//! valid only when its logical scope, exact frame, snapshot provenance, and
//! lifetime all match the caller's proof.

use std::collections::{BTreeMap, VecDeque};

use agentyc_core::{
    CoreError, ElementRef, FrameId, FrameVersion, Generation, PageId, RefEpoch, RefId,
    SnapshotEnvelope, SnapshotProvenance, SnapshotVersion, SpaceId, Timestamp,
};

use serde::{Deserialize, Serialize};

/// Bounds for the in-memory reference registry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefRegistryLimits {
    /// Maximum number of live references.
    pub max_active_refs: usize,
    /// Maximum number of retained invalidation tombstones.
    pub max_tombstones: usize,
    /// Optional lifetime in logical clock ticks for newly issued refs.
    pub default_ttl: Option<u64>,
}

impl RefRegistryLimits {
    /// Construct limits without an expiry policy.
    pub const fn new(max_active_refs: usize, max_tombstones: usize) -> Self {
        Self {
            max_active_refs,
            max_tombstones,
            default_ttl: None,
        }
    }

    /// Set the default logical lifetime for newly issued refs.
    #[must_use]
    pub const fn with_default_ttl(mut self, ttl: Option<u64>) -> Self {
        self.default_ttl = ttl;
        self
    }
}

impl Default for RefRegistryLimits {
    fn default() -> Self {
        Self {
            max_active_refs: 1_024,
            max_tombstones: 2_048,
            default_ttl: Some(300),
        }
    }
}

/// Why a reference was removed from the live registry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RefInvalidationReason {
    /// A caller explicitly invalidated one reference.
    Explicit,
    /// The reference lifetime elapsed.
    Expired,
    /// The active-reference bound required deterministic eviction.
    Capacity,
    /// The document generation changed.
    DocumentChanged,
    /// The navigation generation changed.
    NavigationChanged,
    /// The ref epoch changed.
    RefEpochChanged,
    /// The logical frame was replaced or removed.
    FrameReplaced,
    /// A rerender invalidated element locations without changing navigation.
    Rerendered,
    /// Raw evaluation may have changed page state outside the snapshot model.
    RawEvaluation,
    /// A takeover changed the authority generation.
    Takeover,
    /// A reconnect changed the bridge provenance.
    Reconnect,
    /// The owning logical page or space was invalidated.
    ScopeInvalidated,
    /// All references were invalidated during a reset.
    RegistryReset,
}

/// Stable next-step information for a stale logical ref.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RefStaleHint {
    /// Request or retain a fresh complete snapshot.
    RefreshSnapshot,
    /// Rebuild the frame-scoped locator in the current frame.
    RefreshFrame,
    /// Re-read the current document after a rerender or replacement.
    RefreshDocument,
    /// Request a snapshot after navigation settles.
    RefreshNavigation,
    /// The ref lifetime elapsed and it must be issued again.
    Expired,
    /// Authority or bridge provenance changed.
    Reconnect,
    /// The registry no longer retains enough information to reuse the ref.
    #[default]
    Reissue,
}

impl RefStaleHint {
    /// Return the stable wire hint used in stale-ref guidance.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RefreshSnapshot => "refresh_snapshot",
            Self::RefreshFrame => "refresh_frame",
            Self::RefreshDocument => "refresh_document",
            Self::RefreshNavigation => "refresh_navigation",
            Self::Expired => "expired",
            Self::Reconnect => "reconnect",
            Self::Reissue => "reissue",
        }
    }
}

impl std::fmt::Display for RefStaleHint {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl RefInvalidationReason {
    /// Return deterministic caller guidance for this invalidation cause.
    pub const fn stale_hint(self) -> RefStaleHint {
        match self {
            Self::Expired => RefStaleHint::Expired,
            Self::FrameReplaced => RefStaleHint::RefreshFrame,
            Self::DocumentChanged | Self::Rerendered => RefStaleHint::RefreshDocument,
            Self::NavigationChanged => RefStaleHint::RefreshNavigation,
            Self::RawEvaluation => RefStaleHint::RefreshSnapshot,
            Self::Takeover | Self::Reconnect => RefStaleHint::Reconnect,
            Self::RefEpochChanged | Self::ScopeInvalidated => RefStaleHint::RefreshSnapshot,
            Self::Explicit | Self::Capacity | Self::RegistryReset => RefStaleHint::Reissue,
        }
    }
}

/// Last successful logical validation of a reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefValidation {
    /// Document generation proven by the validation.
    pub generation: Generation,
    /// Exact document generation proven by the validation.
    pub document_generation: Generation,
    /// Exact navigation generation proven by the validation.
    pub navigation_generation: Generation,
    /// Exact snapshot version proven by the validation.
    pub snapshot_version: SnapshotVersion,
    /// Exact frame version when one was supplied by the snapshot.
    pub frame_version: Option<FrameVersion>,
    /// Logical time of the validation.
    pub validated_at: Timestamp,
    /// Absolute expiry retained by the registry.
    pub expires_at: Option<Timestamp>,
}

/// A live reference and the complete proof that created it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefRecord {
    /// Core logical reference returned to a caller.
    pub element_ref: ElementRef,
    /// Snapshot provenance retained independently of the ref fields.
    pub provenance: SnapshotProvenance,
    /// Logical time at which the ref was issued.
    pub issued_at: Timestamp,
    /// Logical expiry, when the registry has an expiry policy.
    pub expires_at: Option<Timestamp>,
    /// Frame version captured when the source envelope carried one.
    pub frame_version: Option<FrameVersion>,
    /// Last document generation successfully validated.
    #[serde(default)]
    pub last_validated_generation: Generation,
    /// Logical time of the last successful validation.
    #[serde(default)]
    pub last_validated_at: Timestamp,
    /// Snapshot version used by the last successful validation.
    #[serde(default)]
    pub last_validated_snapshot_version: SnapshotVersion,
    /// Navigation generation used by the last successful validation.
    #[serde(default)]
    pub last_validated_navigation_generation: Generation,
    /// Document generation used by the last successful validation.
    #[serde(default)]
    pub last_validated_document_generation: Generation,
    /// Frame version used by the last successful validation, when known.
    #[serde(default)]
    pub last_validated_frame_version: Option<FrameVersion>,
}

impl RefRecord {
    /// Return the complete last-validation record for this ref.
    pub const fn last_validation(&self) -> RefValidation {
        RefValidation {
            generation: self.last_validated_generation,
            document_generation: self.last_validated_document_generation,
            navigation_generation: self.last_validated_navigation_generation,
            snapshot_version: self.last_validated_snapshot_version,
            frame_version: self.last_validated_frame_version,
            validated_at: self.last_validated_at,
            expires_at: self.expires_at,
        }
    }
}

/// A bounded record explaining why a previously issued ref can no longer be used.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefTombstone {
    /// Ref identity that was invalidated.
    pub ref_id: RefId,
    /// The exact ref proof that was invalidated.
    pub element_ref: ElementRef,
    /// Stable invalidation cause.
    pub reason: RefInvalidationReason,
    /// Caller-facing logical recovery hint.
    #[serde(default)]
    pub hint: RefStaleHint,
    /// Logical time at which the tombstone was created.
    pub invalidated_at: Timestamp,
}

/// Bounded registry for logical element refs.
#[derive(Debug, Clone)]
pub struct RefRegistry {
    active: BTreeMap<RefId, RefRecord>,
    tombstones: BTreeMap<RefId, RefTombstone>,
    tombstone_order: VecDeque<RefId>,
    limits: RefRegistryLimits,
    next_ref_number: u64,
}

impl Default for RefRegistry {
    fn default() -> Self {
        Self::new(RefRegistryLimits::default())
    }
}

impl RefRegistry {
    /// Construct an empty registry with explicit bounds.
    pub fn new(limits: RefRegistryLimits) -> Self {
        Self {
            active: BTreeMap::new(),
            tombstones: BTreeMap::new(),
            tombstone_order: VecDeque::new(),
            limits,
            next_ref_number: 1,
        }
    }

    /// Construct a registry from active/tombstone counts without an expiry policy.
    pub fn with_limits(max_active_refs: usize, max_tombstones: usize) -> Self {
        Self::new(RefRegistryLimits::new(max_active_refs, max_tombstones))
    }

    /// Return the configured registry limits.
    pub const fn limits(&self) -> RefRegistryLimits {
        self.limits
    }

    /// Issue a ref from a validated, ref-capable snapshot using an exact frame.
    pub fn issue(
        &mut self,
        envelope: &SnapshotEnvelope,
        frame_id: FrameId,
        now: Timestamp,
    ) -> Result<ElementRef, CoreError> {
        self.issue_ref(envelope, frame_id, now)
    }

    /// Alias for [`Self::issue`].
    pub fn issue_from_envelope(
        &mut self,
        envelope: &SnapshotEnvelope,
        frame_id: FrameId,
        now: Timestamp,
    ) -> Result<ElementRef, CoreError> {
        self.issue(envelope, frame_id, now)
    }

    /// Alias for [`Self::issue`].
    pub fn issue_ref(
        &mut self,
        envelope: &SnapshotEnvelope,
        frame_id: FrameId,
        now: Timestamp,
    ) -> Result<ElementRef, CoreError> {
        envelope
            .validate()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        let ref_id = self.allocate_ref_id()?;
        self.issue_with_id(ref_id, envelope, frame_id, now)
    }

    /// Issue a ref with a caller-selected logical identity.
    pub(crate) fn issue_with_id(
        &mut self,
        ref_id: RefId,
        envelope: &SnapshotEnvelope,
        frame_id: FrameId,
        now: Timestamp,
    ) -> Result<ElementRef, CoreError> {
        envelope
            .validate()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        if !envelope.frame_versions.contains_key(&frame_id) {
            return Err(CoreError::stale_ref(
                "snapshot frame vector does not contain the requested frame",
            ));
        }
        let element_ref = envelope.make_ref(ref_id, frame_id)?;
        self.register(element_ref.clone(), envelope.provenance(), now)?;
        if let Some(record) = self.active.get_mut(&element_ref.ref_id) {
            record.frame_version = envelope.frame_versions.get(&element_ref.frame_id).copied();
            record.last_validated_frame_version = record.frame_version;
        }
        Ok(element_ref)
    }

    /// Register an already-created ref and its exact provenance.
    pub(crate) fn register(
        &mut self,
        element_ref: ElementRef,
        provenance: SnapshotProvenance,
        now: Timestamp,
    ) -> Result<(), CoreError> {
        if !provenance.can_issue_refs() {
            return Err(CoreError::stale_ref(
                "refs require complete coherent snapshot provenance",
            ));
        }
        if element_ref.space_id != provenance.space_id
            || element_ref.page_id != provenance.page_id
            || element_ref.snapshot_version != provenance.snapshot_version
            || element_ref.document_generation != provenance.document_generation
            || element_ref.navigation_generation != provenance.navigation_generation
            || element_ref.refs_epoch != provenance.refs_epoch
        {
            return Err(CoreError::stale_ref(
                "ref does not match the supplied snapshot provenance",
            ));
        }
        if self.limits.max_active_refs == 0 {
            return Err(CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "reference registry has no active capacity",
            ));
        }

        self.purge_expired(now);
        if self.active.contains_key(&element_ref.ref_id) {
            self.invalidate_ref(&element_ref.ref_id, RefInvalidationReason::Explicit, now);
        }
        while self.active.len() >= self.limits.max_active_refs {
            let oldest = self
                .active
                .values()
                .min_by(|left, right| {
                    left.issued_at
                        .cmp(&right.issued_at)
                        .then_with(|| left.element_ref.ref_id.cmp(&right.element_ref.ref_id))
                })
                .map(|record| record.element_ref.ref_id.clone());
            let Some(oldest) = oldest else {
                break;
            };
            self.invalidate_ref(&oldest, RefInvalidationReason::Capacity, now);
        }

        let expires_at = self
            .limits
            .default_ttl
            .map(|ttl| Timestamp::new(now.get().saturating_add(ttl)));
        let document_generation = provenance.document_generation;
        let navigation_generation = provenance.navigation_generation;
        let snapshot_version = provenance.snapshot_version;
        self.active.insert(
            element_ref.ref_id.clone(),
            RefRecord {
                element_ref,
                provenance,
                issued_at: now,
                last_validated_generation: document_generation,
                last_validated_at: now,
                last_validated_snapshot_version: snapshot_version,
                last_validated_navigation_generation: navigation_generation,
                last_validated_document_generation: document_generation,
                last_validated_frame_version: None,
                expires_at,
                frame_version: None,
            },
        );
        Ok(())
    }

    /// Resolve a ref only with an exact frame and exact current provenance.
    pub fn resolve<'a>(
        &'a mut self,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        current: &SnapshotProvenance,
        now: Timestamp,
    ) -> Result<&'a RefRecord, CoreError> {
        self.resolve_ref(element_ref, frame_id, current, now)
    }

    /// Resolve a ref only with an exact frame and exact current provenance.
    pub fn resolve_ref<'a>(
        &'a mut self,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        current: &SnapshotProvenance,
        now: Timestamp,
    ) -> Result<&'a RefRecord, CoreError> {
        self.purge_expired(now);
        let Some(record) = self.active.get(&element_ref.ref_id) else {
            return Err(self.stale_error(&element_ref.ref_id));
        };
        if &record.element_ref != element_ref {
            return Err(CoreError::stale_ref(
                "ref identity or provenance was altered; hint=reissue",
            ));
        }
        if &record.element_ref.frame_id != frame_id {
            return Err(CoreError::stale_ref(
                "ref frame provenance does not match the requested frame; hint=refresh_frame",
            ));
        }
        if current != &record.provenance {
            let reason = if record.element_ref.refs_epoch != current.refs_epoch {
                RefInvalidationReason::RefEpochChanged
            } else if record.element_ref.navigation_generation != current.navigation_generation {
                RefInvalidationReason::NavigationChanged
            } else if record.element_ref.document_generation != current.document_generation {
                RefInvalidationReason::DocumentChanged
            } else {
                RefInvalidationReason::Rerendered
            };
            self.invalidate_ref(&element_ref.ref_id, reason, now);
            return Err(self.stale_error(&element_ref.ref_id));
        }
        element_ref.validate_against(current)?;
        let frame_version = record.frame_version;
        let record = self
            .active
            .get_mut(&element_ref.ref_id)
            .expect("ref was present during validation");
        record.last_validated_generation = current.document_generation;
        record.last_validated_at = now;
        record.last_validated_snapshot_version = current.snapshot_version;
        record.last_validated_navigation_generation = current.navigation_generation;
        record.last_validated_document_generation = current.document_generation;
        record.last_validated_frame_version = frame_version;
        Ok(record)
    }

    /// Resolve against a complete snapshot envelope, including exact frame version.
    pub fn resolve_against_snapshot<'a>(
        &'a mut self,
        element_ref: &ElementRef,
        envelope: &SnapshotEnvelope,
        now: Timestamp,
    ) -> Result<&'a RefRecord, CoreError> {
        envelope
            .validate()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        if !envelope.can_issue_refs() {
            return Err(CoreError::stale_ref(
                "snapshot cannot validate element refs",
            ));
        }
        let frame_version = envelope.frame_versions.get(&element_ref.frame_id).copied();
        if self.active.get(&element_ref.ref_id).is_some_and(|record| {
            record.element_ref == *element_ref
                && record.frame_version.is_some()
                && record.frame_version != frame_version
        }) {
            self.invalidate_ref(
                &element_ref.ref_id,
                RefInvalidationReason::FrameReplaced,
                now,
            );
            return Err(self.stale_error(&element_ref.ref_id));
        }
        let record = self.resolve_ref(
            element_ref,
            &element_ref.frame_id,
            &envelope.provenance(),
            now,
        )?;
        if record.frame_version.is_some() && record.frame_version != frame_version {
            return Err(CoreError::stale_ref(
                "frame identity was reused with a different frame version; hint=refresh_frame",
            ));
        }
        Ok(record)
    }

    /// Validate a ref without returning mutable browser-facing state.
    pub fn validate(
        &mut self,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        current: &SnapshotProvenance,
        now: Timestamp,
    ) -> Result<(), CoreError> {
        self.resolve_ref(element_ref, frame_id, current, now)
            .map(|_| ())
    }

    /// Resolve by opaque ref identity while still requiring exact frame/provenance.
    pub fn resolve_id<'a>(
        &'a mut self,
        ref_id: &RefId,
        frame_id: &FrameId,
        current: &SnapshotProvenance,
        now: Timestamp,
    ) -> Result<&'a RefRecord, CoreError> {
        let element_ref = self
            .active
            .get(ref_id)
            .map(|record| record.element_ref.clone())
            .or_else(|| {
                self.tombstones
                    .get(ref_id)
                    .map(|tombstone| tombstone.element_ref.clone())
            })
            .ok_or_else(|| self.stale_error(ref_id))?;
        self.resolve_ref(&element_ref, frame_id, current, now)
    }

    /// Explicitly reject a resolution attempt that has no exact frame identity.
    pub fn resolve_without_frame(
        &self,
        _element_ref: &ElementRef,
        _current: &SnapshotProvenance,
        _now: Timestamp,
    ) -> Result<(), CoreError> {
        Err(CoreError::stale_ref(
            "ref resolution requires an exact logical frame; frame guessing is forbidden; hint=refresh_frame",
        ))
    }

    /// Invalidate one live ref and retain a bounded tombstone.
    pub fn invalidate_ref(
        &mut self,
        ref_id: &RefId,
        reason: RefInvalidationReason,
        now: Timestamp,
    ) -> bool {
        let Some(record) = self.active.remove(ref_id) else {
            return false;
        };
        self.add_tombstone(RefTombstone {
            ref_id: ref_id.clone(),
            element_ref: record.element_ref,
            hint: reason.stale_hint(),
            reason,
            invalidated_at: now,
        });
        true
    }

    /// Alias for [`Self::invalidate_scope`].
    pub fn invalidate_page(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_scope(space_id, page_id, now)
    }

    /// Alias for [`Self::invalidate_ref`].
    pub fn invalidate(
        &mut self,
        ref_id: &RefId,
        reason: RefInvalidationReason,
        now: Timestamp,
    ) -> bool {
        self.invalidate_ref(ref_id, reason, now)
    }

    /// Invalidate every ref for one exact logical frame.
    pub fn invalidate_frame(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        frame_id: &FrameId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id
                    && record.element_ref.page_id == *page_id
                    && record.element_ref.frame_id == *frame_id
            },
            RefInvalidationReason::FrameReplaced,
            now,
        )
    }

    /// Invalidate refs affected by a rerender while retaining page scope.
    pub fn invalidate_rerender(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id && record.element_ref.page_id == *page_id
            },
            RefInvalidationReason::Rerendered,
            now,
        )
    }

    /// Invalidate refs after an untracked raw evaluation.
    pub fn invalidate_raw_evaluation(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id && record.element_ref.page_id == *page_id
            },
            RefInvalidationReason::RawEvaluation,
            now,
        )
    }

    /// Invalidate refs after a logical takeover.
    pub fn invalidate_takeover(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id && record.element_ref.page_id == *page_id
            },
            RefInvalidationReason::Takeover,
            now,
        )
    }

    /// Invalidate refs after a bridge reconnect or mapping reset.
    pub fn invalidate_reconnect(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id && record.element_ref.page_id == *page_id
            },
            RefInvalidationReason::Reconnect,
            now,
        )
    }

    /// Alias naming the frame-replacement invalidation explicitly.
    pub fn invalidate_frame_replacement(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        frame_id: &FrameId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_frame(space_id, page_id, frame_id, now)
    }

    /// Invalidate every ref in one logical page scope.
    pub fn invalidate_scope(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id && record.element_ref.page_id == *page_id
            },
            RefInvalidationReason::ScopeInvalidated,
            now,
        )
    }

    /// Invalidate refs whose document generation is not the supplied generation.
    pub fn invalidate_document_generation(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        document_generation: Generation,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id
                    && record.element_ref.page_id == *page_id
                    && record.element_ref.document_generation != document_generation
            },
            RefInvalidationReason::DocumentChanged,
            now,
        )
    }

    /// Invalidate refs whose navigation generation is not the supplied generation.
    pub fn invalidate_navigation_generation(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        navigation_generation: Generation,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id
                    && record.element_ref.page_id == *page_id
                    && record.element_ref.navigation_generation != navigation_generation
            },
            RefInvalidationReason::NavigationChanged,
            now,
        )
    }

    /// Invalidate refs from every epoch except the current one.
    pub fn invalidate_ref_epoch(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        refs_epoch: RefEpoch,
        now: Timestamp,
    ) -> usize {
        self.invalidate_matching(
            |record| {
                record.element_ref.space_id == *space_id
                    && record.element_ref.page_id == *page_id
                    && record.element_ref.refs_epoch != refs_epoch
            },
            RefInvalidationReason::RefEpochChanged,
            now,
        )
    }

    /// Invalidate refs that do not exactly match current page provenance.
    pub fn invalidate_provenance(&mut self, current: &SnapshotProvenance, now: Timestamp) -> usize {
        let ids: Vec<_> = self
            .active
            .values()
            .filter(|record| {
                record.provenance.space_id == current.space_id
                    && record.provenance.page_id == current.page_id
                    && record.provenance != *current
            })
            .map(|record| record.element_ref.ref_id.clone())
            .collect();
        let count = ids.len();
        for ref_id in ids {
            let reason =
                self.active
                    .get(&ref_id)
                    .map_or(RefInvalidationReason::DocumentChanged, |record| {
                        if record.element_ref.refs_epoch != current.refs_epoch {
                            RefInvalidationReason::RefEpochChanged
                        } else if record.element_ref.navigation_generation
                            != current.navigation_generation
                        {
                            RefInvalidationReason::NavigationChanged
                        } else if record.element_ref.document_generation
                            != current.document_generation
                        {
                            RefInvalidationReason::DocumentChanged
                        } else if record.element_ref.snapshot_version != current.snapshot_version
                            || record.provenance.snapshot_hash != current.snapshot_hash
                        {
                            RefInvalidationReason::Rerendered
                        } else {
                            RefInvalidationReason::Explicit
                        }
                    });
            self.invalidate_ref(&ref_id, reason, now);
        }
        count
    }

    /// Invalidate every live ref and retain bounded tombstones.
    pub fn invalidate_all(&mut self, now: Timestamp) -> usize {
        let ids: Vec<_> = self.active.keys().cloned().collect();
        let count = ids.len();
        for ref_id in ids {
            self.invalidate_ref(&ref_id, RefInvalidationReason::RegistryReset, now);
        }
        count
    }

    /// Remove and tombstone refs whose expiry is at or before `now`.
    pub fn purge_expired(&mut self, now: Timestamp) -> usize {
        let ids: Vec<_> = self
            .active
            .values()
            .filter(|record| {
                record
                    .expires_at
                    .is_some_and(|expiry| expiry.get() <= now.get())
            })
            .map(|record| record.element_ref.ref_id.clone())
            .collect();
        let count = ids.len();
        for ref_id in ids {
            self.invalidate_ref(&ref_id, RefInvalidationReason::Expired, now);
        }
        count
    }

    /// Number of currently active refs.
    pub fn active_len(&self) -> usize {
        self.active.len()
    }

    /// Return the last successful validation metadata for a live ref.
    pub fn last_validation(&self, ref_id: &RefId) -> Option<RefValidation> {
        self.active.get(ref_id).map(RefRecord::last_validation)
    }

    /// Return the stale recovery hint retained for an invalidated ref.
    pub fn stale_hint(&self, ref_id: &RefId) -> Option<RefStaleHint> {
        self.tombstones.get(ref_id).map(|tombstone| tombstone.hint)
    }

    /// Alias for [`Self::invalidate_document_generation`].
    pub fn invalidate_document(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        document_generation: Generation,
        now: Timestamp,
    ) -> usize {
        self.invalidate_document_generation(space_id, page_id, document_generation, now)
    }

    /// Alias for [`Self::invalidate_navigation_generation`].
    pub fn invalidate_navigation(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        navigation_generation: Generation,
        now: Timestamp,
    ) -> usize {
        self.invalidate_navigation_generation(space_id, page_id, navigation_generation, now)
    }

    /// Alias for [`Self::invalidate_ref_epoch`].
    pub fn invalidate_epoch(
        &mut self,
        space_id: &SpaceId,
        page_id: &PageId,
        refs_epoch: RefEpoch,
        now: Timestamp,
    ) -> usize {
        self.invalidate_ref_epoch(space_id, page_id, refs_epoch, now)
    }

    /// Number of retained tombstones.
    pub fn tombstone_len(&self) -> usize {
        self.tombstones.len()
    }

    /// Return a live record by its opaque identity without validating it.
    pub fn active(&self, ref_id: &RefId) -> Option<&RefRecord> {
        self.active.get(ref_id)
    }

    /// Return the retained invalidation tombstone for a ref, if any.
    pub fn tombstone(&self, ref_id: &RefId) -> Option<&RefTombstone> {
        self.tombstones.get(ref_id)
    }

    fn allocate_ref_id(&mut self) -> Result<RefId, CoreError> {
        loop {
            let number = self.next_ref_number;
            self.next_ref_number = self.next_ref_number.checked_add(1).ok_or_else(|| {
                CoreError::invalid_argument("reference identity counter overflow")
            })?;
            let ref_id = RefId::from_suffix(format!("host-{number}"))
                .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
            if !self.active.contains_key(&ref_id) && !self.tombstones.contains_key(&ref_id) {
                return Ok(ref_id);
            }
        }
    }

    fn invalidate_matching<F>(
        &mut self,
        predicate: F,
        reason: RefInvalidationReason,
        now: Timestamp,
    ) -> usize
    where
        F: Fn(&RefRecord) -> bool,
    {
        let ids: Vec<_> = self
            .active
            .values()
            .filter(|record| predicate(record))
            .map(|record| record.element_ref.ref_id.clone())
            .collect();
        let count = ids.len();
        for ref_id in ids {
            self.invalidate_ref(&ref_id, reason, now);
        }
        count
    }

    fn add_tombstone(&mut self, tombstone: RefTombstone) {
        if self.limits.max_tombstones == 0 {
            return;
        }
        let ref_id = tombstone.ref_id.clone();
        self.tombstones.insert(ref_id.clone(), tombstone);
        self.tombstone_order.retain(|item| item != &ref_id);
        self.tombstone_order.push_back(ref_id);
        while self.tombstones.len() > self.limits.max_tombstones {
            let Some(oldest) = self.tombstone_order.pop_front() else {
                break;
            };
            self.tombstones.remove(&oldest);
        }
    }

    fn stale_error(&self, ref_id: &RefId) -> CoreError {
        if let Some(tombstone) = self.tombstones.get(ref_id) {
            CoreError::stale_ref(format!(
                "ref was invalidated: {:?}; hint={}",
                tombstone.reason, tombstone.hint
            ))
        } else {
            CoreError::stale_ref("ref is not present in the bounded registry; hint=reissue")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snapshots::empty_snapshot;
    use agentyc_core::{FrameVersion, PageId, SpaceId};

    fn fixture() -> (SnapshotEnvelope, FrameId) {
        let space = SpaceId::from_suffix("space").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let frame = FrameId::from_suffix("oopif").expect("frame");
        let mut snapshot = empty_snapshot(space, page);
        snapshot
            .frame_versions
            .insert(frame.clone(), FrameVersion::new(1));
        (snapshot, frame)
    }

    #[test]
    fn exact_frame_and_provenance_are_required() {
        let (snapshot, frame) = fixture();
        let mut registry = RefRegistry::with_limits(8, 8);
        let element_ref = registry
            .issue(&snapshot, frame.clone(), Timestamp::new(1))
            .expect("issue");
        assert!(
            registry
                .resolve(
                    &element_ref,
                    &frame,
                    &snapshot.provenance(),
                    Timestamp::new(2)
                )
                .is_ok()
        );
        let other_frame = FrameId::from_suffix("other").expect("frame");
        assert!(
            registry
                .resolve(
                    &element_ref,
                    &other_frame,
                    &snapshot.provenance(),
                    Timestamp::new(2),
                )
                .is_err()
        );
        assert!(
            registry
                .resolve_without_frame(&element_ref, &snapshot.provenance(), Timestamp::new(2))
                .is_err()
        );
        let mut replaced = snapshot.clone();
        replaced
            .frame_versions
            .insert(frame.clone(), FrameVersion::new(2));
        assert!(
            registry
                .resolve_against_snapshot(&element_ref, &replaced, Timestamp::new(2))
                .is_err()
        );
    }

    #[test]
    fn invalidation_tombstones_are_bounded_and_stale() {
        let (snapshot, frame) = fixture();
        let mut registry = RefRegistry::new(RefRegistryLimits::new(1, 1).with_default_ttl(Some(2)));
        let first = registry
            .issue(&snapshot, frame.clone(), Timestamp::new(0))
            .expect("first");
        registry.invalidate_ref(
            &first.ref_id,
            RefInvalidationReason::Explicit,
            Timestamp::new(1),
        );
        assert_eq!(registry.tombstone_len(), 1);
        assert!(
            registry
                .resolve(&first, &frame, &snapshot.provenance(), Timestamp::new(1))
                .is_err()
        );
        let second = registry
            .issue(&snapshot, frame, Timestamp::new(2))
            .expect("second");
        assert!(
            registry
                .resolve(
                    &second,
                    &second.frame_id,
                    &snapshot.provenance(),
                    Timestamp::new(5)
                )
                .is_err()
        );
        assert!(registry.tombstone_len() <= 1);
    }

    #[test]
    fn successful_resolution_updates_last_validation_metadata() {
        let (snapshot, frame) = fixture();
        let mut registry = RefRegistry::with_limits(8, 8);
        let element_ref = registry
            .issue(&snapshot, frame, Timestamp::new(1))
            .expect("issue");
        let before = registry
            .last_validation(&element_ref.ref_id)
            .expect("initial validation");
        registry
            .resolve(
                &element_ref,
                &element_ref.frame_id,
                &snapshot.provenance(),
                Timestamp::new(9),
            )
            .expect("resolve");
        let after = registry
            .last_validation(&element_ref.ref_id)
            .expect("last validation");
        assert_eq!(after.validated_at, Timestamp::new(9));
        assert_eq!(after.generation, snapshot.document_generation);
        assert_eq!(after.document_generation, snapshot.document_generation);
        assert_eq!(after.navigation_generation, snapshot.navigation_generation);
        assert_eq!(after.snapshot_version, snapshot.snapshot_version);
        assert_eq!(after.frame_version, Some(FrameVersion::new(1)));
        assert_eq!(after.expires_at, before.expires_at);
    }

    #[test]
    fn expiry_and_provenance_changes_retain_stale_hints() {
        let (snapshot, frame) = fixture();
        let mut expiry_registry =
            RefRegistry::new(RefRegistryLimits::new(8, 8).with_default_ttl(Some(2)));
        let expiring = expiry_registry
            .issue(&snapshot, frame.clone(), Timestamp::new(0))
            .expect("issue");
        let expiry_error = expiry_registry
            .resolve(&expiring, &frame, &snapshot.provenance(), Timestamp::new(3))
            .expect_err("expired ref must be stale");
        assert!(expiry_error.message.contains("hint=expired"));
        assert_eq!(
            expiry_registry.stale_hint(&expiring.ref_id),
            Some(RefStaleHint::Expired)
        );
        assert!(
            expiry_registry
                .tombstone(&expiring.ref_id)
                .expect("expiry tombstone")
                .reason
                == RefInvalidationReason::Expired
        );

        let mut rerendered = snapshot.clone();
        rerendered.snapshot_version = SnapshotVersion::new(2);
        rerendered.snapshot_hash = agentyc_core::ContentHash::from_bytes(b"rerendered");
        rerendered.result_hash = rerendered.snapshot_hash.clone();
        let mut rerender_registry = RefRegistry::with_limits(8, 8);
        let rerender_ref = rerender_registry
            .issue(&snapshot, frame.clone(), Timestamp::new(1))
            .expect("issue");
        assert!(
            rerender_registry
                .resolve(
                    &rerender_ref,
                    &frame,
                    &rerendered.provenance(),
                    Timestamp::new(2),
                )
                .is_err()
        );
        assert_eq!(
            rerender_registry
                .tombstone(&rerender_ref.ref_id)
                .map(|t| t.reason),
            Some(RefInvalidationReason::Rerendered)
        );

        let mut navigation_registry = RefRegistry::with_limits(8, 8);
        let navigation_ref = navigation_registry
            .issue(&snapshot, frame.clone(), Timestamp::new(1))
            .expect("issue");
        let mut navigated = snapshot.clone();
        navigated.navigation_generation = Generation::new(2);
        navigated.document_generation = Generation::new(2);
        assert!(
            navigation_registry
                .resolve(
                    &navigation_ref,
                    &frame,
                    &navigated.provenance(),
                    Timestamp::new(2),
                )
                .is_err()
        );
        assert_eq!(
            navigation_registry
                .tombstone(&navigation_ref.ref_id)
                .map(|t| t.reason),
            Some(RefInvalidationReason::NavigationChanged)
        );
    }

    #[test]
    fn raw_evaluation_and_frame_replacement_have_specific_hints() {
        let (snapshot, frame) = fixture();
        let mut raw_registry = RefRegistry::with_limits(8, 8);
        let raw_ref = raw_registry
            .issue(&snapshot, frame.clone(), Timestamp::new(1))
            .expect("issue");
        assert_eq!(
            raw_registry.invalidate_raw_evaluation(
                &snapshot.space_id,
                &snapshot.page_id,
                Timestamp::new(2),
            ),
            1
        );
        assert_eq!(
            raw_registry.stale_hint(&raw_ref.ref_id),
            Some(RefStaleHint::RefreshSnapshot)
        );

        let mut frame_registry = RefRegistry::with_limits(8, 8);
        let frame_ref = frame_registry
            .issue(&snapshot, frame, Timestamp::new(1))
            .expect("issue");
        assert_eq!(
            frame_registry.invalidate_frame_replacement(
                &snapshot.space_id,
                &snapshot.page_id,
                &frame_ref.frame_id,
                Timestamp::new(2),
            ),
            1
        );
        assert_eq!(
            frame_registry.stale_hint(&frame_ref.ref_id),
            Some(RefStaleHint::RefreshFrame)
        );
    }
}
