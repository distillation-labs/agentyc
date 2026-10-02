//! Bounded logical element-reference registry with exact snapshot provenance.
//!
//! The registry is intentionally independent of browser handles. A reference is
//! valid only when its logical scope, exact frame, snapshot provenance, and
//! lifetime all match the caller's proof.

use std::collections::{BTreeMap, VecDeque};

use agentyc_core::{
    CoreError, ElementRef, FrameId, FrameVersion, Generation, PageId, RefEpoch, RefId,
    SnapshotEnvelope, SnapshotProvenance, SpaceId, Timestamp,
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
    /// The owning logical page or space was invalidated.
    ScopeInvalidated,
    /// All references were invalidated during a reset.
    RegistryReset,
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
        self.active.insert(
            element_ref.ref_id.clone(),
            RefRecord {
                element_ref,
                provenance,
                issued_at: now,
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
                "ref identity or provenance was altered",
            ));
        }
        if &record.element_ref.frame_id != frame_id {
            return Err(CoreError::stale_ref(
                "ref frame provenance does not match the requested frame",
            ));
        }
        if current.snapshot_hash != record.provenance.snapshot_hash || current != &record.provenance
        {
            return Err(CoreError::stale_ref(
                "current snapshot provenance does not match the ref",
            ));
        }
        element_ref.validate_against(current)?;
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
        let record = self.resolve_ref(
            element_ref,
            &element_ref.frame_id,
            &envelope.provenance(),
            now,
        )?;
        if record.frame_version.is_some() && record.frame_version != frame_version {
            return Err(CoreError::stale_ref(
                "frame identity was reused with a different frame version",
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
            "ref resolution requires an exact logical frame; frame guessing is forbidden",
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
            CoreError::stale_ref(format!("ref was invalidated: {:?}", tombstone.reason))
        } else {
            CoreError::stale_ref("ref is not present in the bounded registry")
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
}
