//! Host-owned logical snapshot cache with explicit dirty state.

use std::collections::BTreeMap;

use agentyc_core::{
    CacheState, ContentHash, FrameVersion, Generation, PageDescriptor, PageId, RefEpoch,
    SnapshotBody, SnapshotCoverage, SnapshotEnvelope, SnapshotMode, SnapshotVersion, SpaceId,
    TopologyVersion,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use agentyc_core::states::DirtyReason;

/// The logical generations a page proof is bound to.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
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
}

/// A deterministic bounded cache useful to the broker and adapter tests.
#[derive(Debug, Clone)]
pub struct SnapshotCache {
    entries: BTreeMap<SpaceId, BTreeMap<PageId, SnapshotCacheRecord>>,
    max_entries: usize,
    max_bytes: usize,
}

impl SnapshotCache {
    /// Construct an empty cache with explicit entry and serialized-byte bounds.
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            entries: BTreeMap::new(),
            max_entries,
            max_bytes,
        }
    }

    /// Insert a clean snapshot after validating scope, shape, and byte size.
    pub fn insert(&mut self, envelope: SnapshotEnvelope) -> Result<(), SnapshotCacheError> {
        envelope
            .validate()
            .map_err(|error| SnapshotCacheError::Invalid(error.to_string()))?;
        let bytes = serde_json::to_vec(&envelope)
            .map_err(|error| SnapshotCacheError::Invalid(error.to_string()))?;
        if bytes.len() > self.max_bytes {
            return Err(SnapshotCacheError::TooLarge);
        }
        let exists = self
            .entries
            .get(&envelope.space_id)
            .and_then(|pages| pages.get(&envelope.page_id))
            .is_some();
        if !exists && self.len() >= self.max_entries {
            return Err(SnapshotCacheError::Full);
        }
        self.entries
            .entry(envelope.space_id.clone())
            .or_default()
            .insert(
                envelope.page_id.clone(),
                SnapshotCacheRecord {
                    generation: PageGeneration {
                        target_generation: Generation::new(0),
                        navigation_generation: envelope.navigation_generation,
                        document_generation: envelope.document_generation,
                    },
                    envelope,
                    dirty: false,
                },
            );
        Ok(())
    }

    /// Return a cached snapshot without performing any bridge/browser scan.
    pub fn get(&self, space_id: &SpaceId, page_id: &PageId) -> Option<CachedSnapshot> {
        self.entries.get(space_id).and_then(|pages| {
            pages.get(page_id).map(|record| CachedSnapshot {
                envelope: record.envelope.clone(),
                clean: !record.dirty,
            })
        })
    }

    /// Return a clean snapshot only; dirty entries deliberately miss.
    pub fn get_clean(&self, space_id: &SpaceId, page_id: &PageId) -> Option<SnapshotEnvelope> {
        self.entries.get(space_id).and_then(|pages| {
            pages.get(page_id).and_then(|record| {
                if record.dirty {
                    None
                } else {
                    let mut envelope = record.envelope.clone();
                    envelope.cache_state = CacheState::Cached;
                    Some(envelope)
                }
            })
        })
    }

    /// Mark one logical page dirty without scanning or contacting a bridge.
    pub fn mark_dirty(&mut self, space_id: &SpaceId, page_id: &PageId) -> bool {
        self.entries
            .get_mut(space_id)
            .and_then(|pages| pages.get_mut(page_id))
            .map(|record| {
                record.dirty = true;
                true
            })
            .unwrap_or(false)
    }

    /// Remove one cached entry.
    pub fn invalidate(&mut self, space_id: &SpaceId, page_id: &PageId) -> bool {
        let Some(pages) = self.entries.get_mut(space_id) else {
            return false;
        };
        let removed = pages.remove(page_id).is_some();
        if pages.is_empty() {
            self.entries.remove(space_id);
        }
        removed
    }

    /// Number of retained cache entries.
    pub fn len(&self) -> usize {
        self.entries.values().map(BTreeMap::len).sum()
    }

    /// Return whether the cache contains no entries.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
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
        dirty_reasons: Vec::<DirtyReason>::new(),
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
}
