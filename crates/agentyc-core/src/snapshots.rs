//! Snapshot provenance, compact/full/delta contracts, and deterministic patching.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{
    errors::{CoreError, ErrorCode},
    ids::{
        ContentHash, ElementKey, FrameId, FrameVersion, Generation, PageId, RefEpoch, RefId,
        SnapshotVersion, SpaceId, TopologyVersion,
    },
    states::{CacheState, DirtyReason, ResyncReason, SnapshotCoverage, SnapshotMode},
};

/// Maximum number of operations in one delta by default.
pub const DEFAULT_MAX_DELTA_OPERATIONS: usize = 1024;
/// Maximum number of chained deltas by default.
pub const DEFAULT_MAX_DELTA_CHAIN_DEPTH: u16 = 8;
/// Current snapshot envelope schema version.
pub const SNAPSHOT_SCHEMA_VERSION: u16 = 1;
/// Maximum elements admitted in one complete snapshot body.
pub const MAX_SNAPSHOT_ELEMENTS: usize = 16_384;
/// Maximum attributes admitted on one snapshot element.
pub const MAX_SNAPSHOT_ATTRIBUTES: usize = 64;
/// Maximum UTF-8 bytes in an attribute key.
pub const MAX_SNAPSHOT_ATTRIBUTE_KEY_BYTES: usize = 128;
/// Maximum UTF-8 bytes in an attribute value.
pub const MAX_SNAPSHOT_ATTRIBUTE_VALUE_BYTES: usize = 16 * 1024;
/// Maximum UTF-8 bytes in an element text value.
pub const MAX_SNAPSHOT_TEXT_BYTES: usize = 64 * 1024;
/// Maximum frame-version components in one snapshot provenance vector.
pub const MAX_SNAPSHOT_FRAME_VERSIONS: usize = 1024;
/// Maximum changed element keys carried by one envelope.
pub const MAX_SNAPSHOT_CHANGED_KEYS: usize = MAX_SNAPSHOT_ELEMENTS;
/// Maximum omitted field names carried by one bounded envelope.
pub const MAX_SNAPSHOT_OMITTED_FIELDS: usize = 256;
/// Maximum UTF-8 bytes in one omitted field name.
pub const MAX_SNAPSHOT_OMITTED_FIELD_BYTES: usize = 256;
/// Maximum UTF-8 bytes in a tokenizer identifier.
pub const MAX_SNAPSHOT_TOKENIZER_BYTES: usize = 128;

/// A transport-neutral snapshot element with deterministic field order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotElement {
    /// Stable logical element key within the snapshot.
    pub key: ElementKey,
    /// Logical parent key, when the element is nested.
    pub parent: Option<ElementKey>,
    /// Element kind exposed by the snapshot contract.
    pub kind: ElementKind,
    /// Optional text data.
    pub text: Option<String>,
    /// Sorted attribute map.
    pub attributes: BTreeMap<String, String>,
    /// Logical document order, not a browser node identity.
    pub order: u32,
}

impl SnapshotElement {
    /// Validate bounded element fields before hashing or admission.
    pub fn validate(&self) -> Result<(), SnapshotValidationError> {
        if self.parent.as_ref() == Some(&self.key) {
            return Err(SnapshotValidationError::InvalidParent(self.key.clone()));
        }
        if self
            .text
            .as_ref()
            .is_some_and(|text| text.len() > MAX_SNAPSHOT_TEXT_BYTES)
        {
            return Err(SnapshotValidationError::FieldTooLarge {
                field: "text",
                size: self.text.as_ref().map_or(0, String::len),
                max: MAX_SNAPSHOT_TEXT_BYTES,
            });
        }
        if self.attributes.len() > MAX_SNAPSHOT_ATTRIBUTES {
            return Err(SnapshotValidationError::TooManyAttributes {
                count: self.attributes.len(),
                max: MAX_SNAPSHOT_ATTRIBUTES,
            });
        }
        for (key, value) in &self.attributes {
            if key.is_empty() || key.len() > MAX_SNAPSHOT_ATTRIBUTE_KEY_BYTES {
                return Err(SnapshotValidationError::FieldTooLarge {
                    field: "attribute_key",
                    size: key.len(),
                    max: MAX_SNAPSHOT_ATTRIBUTE_KEY_BYTES,
                });
            }
            if value.len() > MAX_SNAPSHOT_ATTRIBUTE_VALUE_BYTES {
                return Err(SnapshotValidationError::FieldTooLarge {
                    field: "attribute_value",
                    size: value.len(),
                    max: MAX_SNAPSHOT_ATTRIBUTE_VALUE_BYTES,
                });
            }
        }
        Ok(())
    }

    /// Return this element's logical key.
    pub fn key(&self) -> &ElementKey {
        &self.key
    }

    fn canonical_json(&self, output: &mut String) {
        output.push('{');
        append_json_key(output, "key");
        append_json_string(output, self.key.as_str());
        output.push(',');
        append_json_key(output, "parent");
        match &self.parent {
            Some(parent) => append_json_string(output, parent.as_str()),
            None => output.push_str("null"),
        }
        output.push(',');
        append_json_key(output, "kind");
        append_json_string(output, self.kind.as_str());
        output.push(',');
        append_json_key(output, "text");
        match &self.text {
            Some(text) => append_json_string(output, text),
            None => output.push_str("null"),
        }
        output.push(',');
        append_json_key(output, "attributes");
        output.push('{');
        for (index, (key, value)) in self.attributes.iter().enumerate() {
            if index != 0 {
                output.push(',');
            }
            append_json_string(output, key);
            output.push(':');
            append_json_string(output, value);
        }
        output.push('}');
        output.push(',');
        append_json_key(output, "order");
        output.push_str(&self.order.to_string());
        output.push('}');
    }
}

/// Bounded element categories in a snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ElementKind {
    /// A document or root node.
    Root,
    /// A structural element.
    Element,
    /// A text node.
    Text,
    /// An interactive control.
    Control,
    /// A frame boundary.
    Frame,
}

impl ElementKind {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Root => "root",
            Self::Element => "element",
            Self::Text => "text",
            Self::Control => "control",
            Self::Frame => "frame",
        }
    }
}

/// A complete, normalized snapshot body used as a delta base.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotDocument {
    /// Logical snapshot version.
    pub snapshot_version: SnapshotVersion,
    /// Elements in deterministic document order.
    pub elements: Vec<SnapshotElement>,
    /// Hash of canonical UTF-8 JSON for `elements`.
    pub snapshot_hash: ContentHash,
}

impl SnapshotDocument {
    /// Normalize elements, compute their deterministic hash, and construct a document.
    pub fn new(
        snapshot_version: SnapshotVersion,
        mut elements: Vec<SnapshotElement>,
    ) -> Result<Self, SnapshotValidationError> {
        if elements.len() > MAX_SNAPSHOT_ELEMENTS {
            return Err(SnapshotValidationError::TooManyElements {
                count: elements.len(),
                max: MAX_SNAPSHOT_ELEMENTS,
            });
        }
        normalize_elements(&mut elements)?;
        let snapshot_hash = hash_elements(&elements)?;
        let document = Self {
            snapshot_version,
            elements,
            snapshot_hash,
        };
        document.validate()?;
        Ok(document)
    }

    /// Validate element bounds, canonical order, and the declared body hash.
    pub fn validate(&self) -> Result<(), SnapshotValidationError> {
        if self.elements.len() > MAX_SNAPSHOT_ELEMENTS {
            return Err(SnapshotValidationError::TooManyElements {
                count: self.elements.len(),
                max: MAX_SNAPSHOT_ELEMENTS,
            });
        }
        let mut previous: Option<(&ElementKey, u32)> = None;
        let mut keys = BTreeSet::new();
        for element in &self.elements {
            element.validate()?;
            if !keys.insert(element.key.clone()) {
                return Err(SnapshotValidationError::DuplicateKey(element.key.clone()));
            }
            if previous.is_some_and(|(key, order)| {
                order > element.order || (order == element.order && key >= &element.key)
            }) {
                return Err(SnapshotValidationError::NonCanonicalBody);
            }
            previous = Some((&element.key, element.order));
        }
        let actual = hash_elements(&self.elements)?;
        if actual != self.snapshot_hash {
            return Err(SnapshotValidationError::BodyHashMismatch {
                expected: self.snapshot_hash.clone(),
                actual,
            });
        }
        Ok(())
    }

    /// Construct an empty normalized document.
    pub fn empty(snapshot_version: SnapshotVersion) -> Self {
        Self {
            snapshot_version,
            elements: Vec::new(),
            snapshot_hash: ContentHash::from_bytes(b"[]"),
        }
    }
}

/// Provenance carried by every ref-capable snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotProvenance {
    /// Canonical owning space.
    pub space_id: SpaceId,
    /// Canonical owning page.
    pub page_id: PageId,
    /// Snapshot version from the logical cache.
    pub snapshot_version: SnapshotVersion,
    /// Content hash of the snapshot result.
    pub snapshot_hash: ContentHash,
    /// Document generation used to create the snapshot.
    pub document_generation: Generation,
    /// Navigation generation used to create the snapshot.
    pub navigation_generation: Generation,
    /// Ref epoch invalidated by navigation/rebind.
    pub refs_epoch: RefEpoch,
    /// Whether all frame versions were coherent.
    pub coherent: bool,
    /// Whether all requested content was covered.
    pub coverage: SnapshotCoverage,
}

impl SnapshotProvenance {
    /// Validate provenance metadata before it is used to authorize a ref.
    pub fn validate(&self) -> Result<(), SnapshotValidationError> {
        if !self.coherent && matches!(self.coverage, SnapshotCoverage::Complete) {
            return Err(SnapshotValidationError::InvalidProvenance(
                "an incoherent snapshot cannot claim complete coverage".to_owned(),
            ));
        }
        Ok(())
    }

    /// Return whether this provenance can issue refs.
    pub const fn can_issue_refs(&self) -> bool {
        self.coherent && matches!(self.coverage, SnapshotCoverage::Complete)
    }
}

/// A logical element ref whose validity is tied to snapshot provenance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ElementRef {
    /// Opaque ref identity.
    pub ref_id: RefId,
    /// Exact logical element key within the issuing snapshot.
    pub element_key: ElementKey,
    /// Owning space.
    pub space_id: SpaceId,
    /// Owning page.
    pub page_id: PageId,
    /// Logical frame identity.
    pub frame_id: FrameId,
    /// Snapshot version that created the ref.
    pub snapshot_version: SnapshotVersion,
    /// Document generation that created the ref.
    pub document_generation: Generation,
    /// Navigation generation that created the ref.
    pub navigation_generation: Generation,
    /// Ref epoch that fences prior refs.
    pub refs_epoch: RefEpoch,
}

impl ElementRef {
    /// Validate this ref against current snapshot provenance.
    pub fn validate_against(&self, provenance: &SnapshotProvenance) -> Result<(), CoreError> {
        provenance.validate().map_err(|error| error.core_error())?;
        if !provenance.can_issue_refs() {
            return Err(CoreError::stale_ref(
                "snapshot is partial or incoherent and cannot validate refs",
            ));
        }
        if self.space_id != provenance.space_id || self.page_id != provenance.page_id {
            return Err(CoreError::stale_ref(
                "ref scope does not match snapshot scope",
            ));
        }
        if self.snapshot_version != provenance.snapshot_version
            || self.document_generation != provenance.document_generation
            || self.navigation_generation != provenance.navigation_generation
            || self.refs_epoch != provenance.refs_epoch
        {
            return Err(CoreError::stale_ref("ref provenance is stale"));
        }
        Ok(())
    }
}

/// A bounded patch operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum DeltaOperation {
    /// Insert a new element or update an existing key.
    Upsert {
        /// Target logical key.
        key: ElementKey,
        /// Replacement element.
        element: SnapshotElement,
    },
    /// Remove one existing element.
    Remove {
        /// Target logical key.
        key: ElementKey,
    },
    /// Move one element before another logical key or to the end.
    Move {
        /// Target logical key.
        key: ElementKey,
        /// New logical order.
        order: u32,
        /// Insert before this key, or at the end when absent.
        before: Option<ElementKey>,
    },
    /// Replace one existing element without changing its key.
    Replace {
        /// Target logical key.
        key: ElementKey,
        /// Replacement element.
        element: SnapshotElement,
    },
    /// Reset one frame's version vector component.
    FrameReset {
        /// Logical frame identity.
        frame_id: FrameId,
        /// New frame version.
        frame_version: FrameVersion,
    },
}

impl DeltaOperation {
    fn sort_key(&self) -> (u8, &str) {
        match self {
            Self::FrameReset { frame_id, .. } => (0, frame_id.as_str()),
            Self::Remove { key } => (1, key.as_str()),
            Self::Replace { key, .. } => (2, key.as_str()),
            Self::Upsert { key, .. } => (3, key.as_str()),
            Self::Move { key, .. } => (4, key.as_str()),
        }
    }

    fn target_key(&self) -> Option<&ElementKey> {
        match self {
            Self::Upsert { key, .. }
            | Self::Remove { key }
            | Self::Move { key, .. }
            | Self::Replace { key, .. } => Some(key),
            Self::FrameReset { .. } => None,
        }
    }
}

/// A deterministic, bounded delta between two snapshots.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotDelta {
    /// Base snapshot version.
    pub base_snapshot_version: SnapshotVersion,
    /// Base content hash.
    pub base_hash: ContentHash,
    /// Result snapshot version.
    pub result_snapshot_version: SnapshotVersion,
    /// Expected result content hash.
    pub result_hash: ContentHash,
    /// Delta sequence within the snapshot chain.
    pub delta_sequence: crate::ids::DeltaSequence,
    /// Number of chained deltas including this one.
    pub chain_depth: u16,
    /// Operations in canonical order.
    pub operations: Vec<DeltaOperation>,
}

impl SnapshotDelta {
    /// Validate size, order, duplicate handling, and element-key invariants.
    pub fn validate(&self, limits: DeltaLimits) -> Result<(), DeltaError> {
        if self.operations.len() > limits.max_operations {
            return Err(DeltaError::TooManyOperations {
                count: self.operations.len(),
                max: limits.max_operations,
            });
        }
        if self.chain_depth > limits.max_chain_depth {
            return Err(DeltaError::ChainTooLong {
                depth: self.chain_depth,
                max: limits.max_chain_depth,
            });
        }
        if self.result_snapshot_version <= self.base_snapshot_version {
            return Err(DeltaError::VersionDidNotAdvance {
                base: self.base_snapshot_version,
                result: self.result_snapshot_version,
            });
        }

        let mut previous: Option<(u8, &str)> = None;
        let mut targets = BTreeSet::new();
        let mut frame_resets = BTreeSet::new();
        for operation in &self.operations {
            let current = operation.sort_key();
            if previous.is_some_and(|previous| previous > current) {
                return Err(DeltaError::NonCanonicalOrder);
            }
            previous = Some(current);
            match operation {
                DeltaOperation::Upsert { element, .. }
                | DeltaOperation::Replace { element, .. } => {
                    element
                        .validate()
                        .map_err(|error| DeltaError::InvalidElement(Box::new(error)))?;
                }
                DeltaOperation::Remove { .. }
                | DeltaOperation::Move { .. }
                | DeltaOperation::FrameReset { .. } => {}
            }
            if let Some(key) = operation.target_key() {
                if !targets.insert(key.clone()) {
                    return Err(DeltaError::DuplicateTarget(key.clone()));
                }
                match operation {
                    DeltaOperation::Upsert {
                        key: target,
                        element,
                    }
                    | DeltaOperation::Replace {
                        key: target,
                        element,
                    } if target != &element.key => {
                        return Err(DeltaError::KeyMismatch {
                            target: target.clone(),
                            element: element.key.clone(),
                        });
                    }
                    DeltaOperation::Upsert { .. }
                    | DeltaOperation::Remove { .. }
                    | DeltaOperation::Move { .. }
                    | DeltaOperation::Replace { .. }
                    | DeltaOperation::FrameReset { .. } => {}
                }
            } else if let DeltaOperation::FrameReset { frame_id, .. } = operation
                && !frame_resets.insert(frame_id.clone())
            {
                return Err(DeltaError::DuplicateFrameReset(frame_id.clone()));
            }
        }
        Ok(())
    }

    /// Apply this delta and verify the declared result hash.
    pub fn apply(
        &self,
        base: &SnapshotDocument,
        limits: DeltaLimits,
    ) -> Result<SnapshotDocument, DeltaError> {
        self.validate(limits)?;
        if base.snapshot_version != self.base_snapshot_version {
            return Err(DeltaError::ResyncRequired(ResyncReason::BaseMissing));
        }
        if base.snapshot_hash != self.base_hash {
            return Err(DeltaError::ResyncRequired(ResyncReason::BaseHashMismatch));
        }
        base.validate()
            .map_err(|error| DeltaError::InvalidSnapshot(Box::new(error)))?;
        if let Some(frame_id) = self
            .operations
            .iter()
            .find_map(|operation| match operation {
                DeltaOperation::FrameReset { frame_id, .. } => Some(frame_id.clone()),
                DeltaOperation::Upsert { .. }
                | DeltaOperation::Remove { .. }
                | DeltaOperation::Move { .. }
                | DeltaOperation::Replace { .. } => None,
            })
        {
            return Err(DeltaError::FrameResetRequiresVersionVector(frame_id));
        }

        let mut elements = base.elements.clone();
        for operation in &self.operations {
            match operation {
                DeltaOperation::Upsert { key, element } => {
                    if let Some(existing) = elements.iter_mut().find(|item| &item.key == key) {
                        *existing = element.clone();
                    } else {
                        elements.push(element.clone());
                    }
                }
                DeltaOperation::Remove { key } => {
                    let Some(index) = elements.iter().position(|item| &item.key == key) else {
                        return Err(DeltaError::MissingTarget(key.clone()));
                    };
                    elements.remove(index);
                }
                DeltaOperation::Replace { key, element } => {
                    let Some(existing) = elements.iter_mut().find(|item| &item.key == key) else {
                        return Err(DeltaError::MissingTarget(key.clone()));
                    };
                    *existing = element.clone();
                }
                DeltaOperation::Move { key, order, before } => {
                    let Some(index) = elements.iter().position(|item| &item.key == key) else {
                        return Err(DeltaError::MissingTarget(key.clone()));
                    };
                    let mut element = elements.remove(index);
                    element.order = *order;
                    let insert_at = before
                        .as_ref()
                        .and_then(|target| elements.iter().position(|item| &item.key == target))
                        .unwrap_or(elements.len());
                    elements.insert(insert_at, element);
                }
                DeltaOperation::FrameReset { frame_id, .. } => {
                    return Err(DeltaError::FrameResetRequiresVersionVector(
                        frame_id.clone(),
                    ));
                }
            }
        }
        normalize_elements(&mut elements)
            .map_err(|error| DeltaError::InvalidSnapshot(Box::new(error)))?;
        let actual_hash = hash_elements(&elements)
            .map_err(|error| DeltaError::InvalidSnapshot(Box::new(error)))?;
        if actual_hash != self.result_hash {
            return Err(DeltaError::ResultHashMismatch {
                expected: self.result_hash.clone(),
                actual: actual_hash,
            });
        }
        Ok(SnapshotDocument {
            snapshot_version: self.result_snapshot_version,
            elements,
            snapshot_hash: self.result_hash.clone(),
        })
    }
}

/// Bounds applied to every delta.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeltaLimits {
    /// Maximum patch operation count.
    pub max_operations: usize,
    /// Maximum chain depth.
    pub max_chain_depth: u16,
}

impl Default for DeltaLimits {
    fn default() -> Self {
        Self {
            max_operations: DEFAULT_MAX_DELTA_OPERATIONS,
            max_chain_depth: DEFAULT_MAX_DELTA_CHAIN_DEPTH,
        }
    }
}

/// Snapshot body selected by an envelope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum SnapshotBody {
    /// Full or compact element list.
    Elements {
        /// Normalized elements.
        elements: Vec<SnapshotElement>,
    },
    /// A delta patch.
    Delta {
        /// Bounded patch.
        delta: SnapshotDelta,
    },
    /// A resync marker without a stale base.
    Resync {
        /// Reason the consumer must request a fresh base.
        reason: ResyncReason,
    },
}

/// Snapshot envelope containing metrics, provenance, and representation details.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotEnvelope {
    /// Schema version for this record.
    pub schema_version: u16,
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Owning logical page.
    pub page_id: PageId,
    /// Result snapshot version.
    pub snapshot_version: SnapshotVersion,
    /// Result content hash.
    pub snapshot_hash: ContentHash,
    /// Delta base version when applicable.
    pub base_snapshot_version: Option<SnapshotVersion>,
    /// Delta base hash when applicable.
    pub base_hash: Option<ContentHash>,
    /// Result hash repeated for consumers that only inspect delta metadata.
    pub result_hash: ContentHash,
    /// Delta sequence when applicable.
    pub delta_sequence: Option<crate::ids::DeltaSequence>,
    /// Topology version of the logical frame tree.
    pub topology_version: TopologyVersion,
    /// Navigation generation.
    pub navigation_generation: Generation,
    /// Document generation.
    pub document_generation: Generation,
    /// Per-frame version vector.
    pub frame_versions: BTreeMap<FrameId, FrameVersion>,
    /// Logical keys changed by this result.
    pub changed: Vec<ElementKey>,
    /// Representation-specific body.
    pub delta_or_elements: SnapshotBody,
    /// Representation mode.
    pub mode: SnapshotMode,
    /// Operation count in the selected body.
    pub operation_count: u32,
    /// Whether all frame versions were coherent.
    pub coherent: bool,
    /// Coverage status.
    pub coverage: SnapshotCoverage,
    /// Dirty causes included in the capture.
    pub dirty_reasons: Vec<DirtyReason>,
    /// Cache freshness.
    pub cache_state: CacheState,
    /// Resync cause, when the mode requires it.
    pub resync_reason: Option<ResyncReason>,
    /// Bytes used by the outer transport.
    pub transport_bytes: u64,
    /// Bytes in the UTF-8 serialized payload.
    pub utf8_bytes: u64,
    /// Serialized token count measured by the producer.
    pub serialized_tokens: u64,
    /// Deployed model-context token count measured by the consumer boundary.
    pub model_context_tokens: u64,
    /// Tokenizer identifier, if measured.
    pub tokenizer: Option<String>,
    /// Optional token budget.
    pub budget: Option<TokenBudget>,
    /// Fields/elements omitted by a bounded result.
    pub omitted: Vec<String>,
    /// Whether the result was truncated.
    pub truncated: bool,
    /// Whether the consumer must resync before using the body.
    pub resync_required: bool,
    /// Ref epoch associated with this result.
    pub refs_epoch: RefEpoch,
}

impl SnapshotEnvelope {
    /// Return provenance needed to validate an element ref.
    pub fn provenance(&self) -> SnapshotProvenance {
        SnapshotProvenance {
            space_id: self.space_id.clone(),
            page_id: self.page_id.clone(),
            snapshot_version: self.snapshot_version,
            snapshot_hash: self.snapshot_hash.clone(),
            document_generation: self.document_generation,
            navigation_generation: self.navigation_generation,
            refs_epoch: self.refs_epoch,
            coherent: self.coherent,
            coverage: self.coverage,
        }
    }

    /// Return whether this envelope may issue refs.
    pub fn can_issue_refs(&self) -> bool {
        !self.truncated && !self.resync_required && self.provenance().can_issue_refs()
    }

    /// Validate bounds, provenance, canonical body order, hashes, and mode metadata.
    pub fn validate(&self) -> Result<(), SnapshotValidationError> {
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION {
            return Err(SnapshotValidationError::UnsupportedSchemaVersion(
                self.schema_version,
            ));
        }
        self.provenance().validate()?;
        if self.frame_versions.len() > MAX_SNAPSHOT_FRAME_VERSIONS {
            return Err(SnapshotValidationError::TooManyFrameVersions {
                count: self.frame_versions.len(),
                max: MAX_SNAPSHOT_FRAME_VERSIONS,
            });
        }
        if self.changed.len() > MAX_SNAPSHOT_CHANGED_KEYS {
            return Err(SnapshotValidationError::TooManyChangedKeys {
                count: self.changed.len(),
                max: MAX_SNAPSHOT_CHANGED_KEYS,
            });
        }
        if self.omitted.len() > MAX_SNAPSHOT_OMITTED_FIELDS {
            return Err(SnapshotValidationError::TooManyOmittedFields {
                count: self.omitted.len(),
                max: MAX_SNAPSHOT_OMITTED_FIELDS,
            });
        }
        if let Some(tokenizer) = &self.tokenizer
            && tokenizer.len() > MAX_SNAPSHOT_TOKENIZER_BYTES
        {
            return Err(SnapshotValidationError::TokenizerTooLarge {
                size: tokenizer.len(),
                max: MAX_SNAPSHOT_TOKENIZER_BYTES,
            });
        }
        if self.omitted.iter().any(|field| field.is_empty()) {
            return Err(SnapshotValidationError::InvalidProvenance(
                "omitted field names must not be empty".to_owned(),
            ));
        }
        if let Some(field) = self
            .omitted
            .iter()
            .find(|field| field.len() > MAX_SNAPSHOT_OMITTED_FIELD_BYTES)
        {
            return Err(SnapshotValidationError::OmittedFieldTooLarge {
                size: field.len(),
                max: MAX_SNAPSHOT_OMITTED_FIELD_BYTES,
            });
        }
        if self.truncated && matches!(self.coverage, SnapshotCoverage::Complete) {
            return Err(SnapshotValidationError::InvalidProvenance(
                "a truncated snapshot cannot claim complete coverage".to_owned(),
            ));
        }
        validate_canonical_keys(&self.changed)?;
        match (&self.mode, &self.delta_or_elements) {
            (SnapshotMode::Full | SnapshotMode::Compact, SnapshotBody::Elements { elements }) => {
                if elements.len() > MAX_SNAPSHOT_ELEMENTS {
                    return Err(SnapshotValidationError::TooManyElements {
                        count: elements.len(),
                        max: MAX_SNAPSHOT_ELEMENTS,
                    });
                }
                if self.base_snapshot_version.is_some()
                    || self.base_hash.is_some()
                    || self.delta_sequence.is_some()
                    || self.resync_reason.is_some()
                    || self.operation_count != 0
                    || self.resync_required
                {
                    return Err(SnapshotValidationError::UnexpectedBodyMetadata);
                }
                let document = SnapshotDocument::new(self.snapshot_version, elements.clone())?;
                if document.elements.as_slice() != elements.as_slice() {
                    return Err(SnapshotValidationError::NonCanonicalBody);
                }
                if self.snapshot_hash != document.snapshot_hash {
                    return Err(SnapshotValidationError::BodyHashMismatch {
                        expected: self.snapshot_hash.clone(),
                        actual: document.snapshot_hash,
                    });
                }
                if self.result_hash != self.snapshot_hash {
                    return Err(SnapshotValidationError::ResultHashMismatch {
                        expected: self.result_hash.clone(),
                        actual: self.snapshot_hash.clone(),
                    });
                }
            }
            (SnapshotMode::Delta, SnapshotBody::Delta { delta }) => {
                if self.base_snapshot_version != Some(delta.base_snapshot_version)
                    || self.base_hash.as_ref() != Some(&delta.base_hash)
                    || self.delta_sequence != Some(delta.delta_sequence)
                    || self.snapshot_version != delta.result_snapshot_version
                    || self.snapshot_hash != delta.result_hash
                    || self.resync_reason.is_some()
                    || self.resync_required
                {
                    return Err(SnapshotValidationError::DeltaMetadataMismatch);
                }
                if self.operation_count as usize != delta.operations.len() {
                    return Err(SnapshotValidationError::OperationCountMismatch);
                }
                delta
                    .validate(DeltaLimits::default())
                    .map_err(|error| SnapshotValidationError::InvalidDelta(Box::new(error)))?;
            }
            (SnapshotMode::Resync, SnapshotBody::Resync { reason }) => {
                if !self.resync_required
                    || self.resync_reason != Some(*reason)
                    || self.base_snapshot_version.is_some()
                    || self.base_hash.is_some()
                    || self.delta_sequence.is_some()
                    || self.operation_count != 0
                {
                    return Err(SnapshotValidationError::ResyncMetadataMismatch);
                }
            }
            _ => return Err(SnapshotValidationError::ModeBodyMismatch),
        }
        if !self.coherent && self.can_issue_refs() {
            return Err(SnapshotValidationError::PartialCanNotIssueRefs);
        }
        Ok(())
    }

    /// Build one ref only after the entire envelope and its provenance validate.
    pub fn make_ref(
        &self,
        ref_id: RefId,
        frame_id: FrameId,
        element_key: ElementKey,
    ) -> Result<ElementRef, CoreError> {
        self.validate().map_err(|error| error.core_error())?;
        if !self.can_issue_refs() {
            return Err(CoreError::stale_ref(
                "refs require a complete coherent non-truncated snapshot",
            ));
        }
        let SnapshotBody::Elements { elements } = &self.delta_or_elements else {
            return Err(CoreError::stale_ref(
                "element refs require a full snapshot body",
            ));
        };
        if !elements.iter().any(|element| element.key == element_key) {
            return Err(CoreError::stale_ref(
                "element key is not present in the issuing snapshot",
            ));
        }
        Ok(ElementRef {
            ref_id,
            element_key,
            space_id: self.space_id.clone(),
            page_id: self.page_id.clone(),
            frame_id,
            snapshot_version: self.snapshot_version,
            document_generation: self.document_generation,
            navigation_generation: self.navigation_generation,
            refs_epoch: self.refs_epoch,
        })
    }
}

/// Token measurement and budget metadata.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenBudget {
    /// Maximum serialized tokens requested.
    pub serialized_limit: Option<u64>,
    /// Maximum model-context tokens requested.
    pub model_context_limit: Option<u64>,
}

/// Deterministic decision when choosing compact, delta, full, or resync output.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum SnapshotDecision {
    /// Use a compact element representation.
    Compact,
    /// Use a valid bounded delta.
    Delta,
    /// Use a complete representation because it is cheaper or safer.
    Full,
    /// Reject the base and request a fresh coherent snapshot.
    Resync {
        /// Reason the base cannot be used.
        reason: ResyncReason,
    },
}

/// Choose a deterministic representation without inspecting browser state.
pub fn choose_snapshot_decision(
    base_available: bool,
    base_matches: bool,
    delta_valid: bool,
    delta_cost: usize,
    full_cost: usize,
    chain_depth: u16,
    limits: DeltaLimits,
) -> SnapshotDecision {
    if !base_available {
        return SnapshotDecision::Full;
    }
    if !base_matches {
        return SnapshotDecision::Resync {
            reason: ResyncReason::BaseHashMismatch,
        };
    }
    if !delta_valid || chain_depth > limits.max_chain_depth {
        return SnapshotDecision::Full;
    }
    if delta_cost < full_cost {
        SnapshotDecision::Delta
    } else {
        SnapshotDecision::Compact
    }
}

fn validate_canonical_keys(keys: &[ElementKey]) -> Result<(), SnapshotValidationError> {
    if keys.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(SnapshotValidationError::NonCanonicalChanged);
    }
    Ok(())
}

/// Errors raised while normalizing or validating snapshot contracts.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum SnapshotValidationError {
    /// The body contains more elements than the bounded contract permits.
    #[error("snapshot contains {count} elements, maximum is {max}")]
    TooManyElements {
        /// Actual element count.
        count: usize,
        /// Maximum admitted count.
        max: usize,
    },
    /// An element contains too many attributes.
    #[error("snapshot element contains {count} attributes, maximum is {max}")]
    TooManyAttributes {
        /// Actual attribute count.
        count: usize,
        /// Maximum admitted count.
        max: usize,
    },
    /// One bounded text field exceeds its byte limit.
    #[error("snapshot {field} is {size} bytes, maximum is {max}")]
    FieldTooLarge {
        /// Logical field name.
        field: &'static str,
        /// Actual UTF-8 byte length.
        size: usize,
        /// Maximum admitted byte length.
        max: usize,
    },
    /// An element points to itself as its parent.
    #[error("snapshot element {0} cannot be its own parent")]
    InvalidParent(ElementKey),
    /// Provenance fields cannot be used to authorize refs.
    #[error("invalid snapshot provenance: {0}")]
    InvalidProvenance(String),
    /// Two elements use the same key.
    #[error("duplicate snapshot element key {0}")]
    DuplicateKey(ElementKey),
    /// The same order value was assigned to two elements.
    #[error("duplicate snapshot element order {0}")]
    DuplicateOrder(u32),
    /// A delta body does not match envelope metadata.
    #[error("snapshot delta metadata does not match its envelope")]
    DeltaMetadataMismatch,
    /// A body does not match its mode.
    #[error("snapshot mode does not match its body")]
    ModeBodyMismatch,
    /// A non-delta body unexpectedly included a base.
    #[error("full or compact snapshot unexpectedly includes a delta base")]
    UnexpectedBase,
    /// A full/compact body contains metadata that belongs to another mode.
    #[error("snapshot body contains unexpected mode metadata")]
    UnexpectedBodyMetadata,
    /// A resync body does not match its resync metadata.
    #[error("snapshot resync metadata does not match its body")]
    ResyncMetadataMismatch,
    /// Body elements are not in canonical order.
    #[error("snapshot body is not in canonical order")]
    NonCanonicalBody,
    /// The changed-key list is not sorted and unique.
    #[error("snapshot changed keys are not in canonical order")]
    NonCanonicalChanged,
    /// Body normalization failed.
    #[error("invalid snapshot body: {0}")]
    InvalidBody(String),
    /// Body hash differs from the declared snapshot hash.
    #[error("snapshot body hash mismatch: expected {expected}, got {actual}")]
    BodyHashMismatch {
        /// Declared snapshot hash.
        expected: ContentHash,
        /// Computed body hash.
        actual: ContentHash,
    },
    /// Result and snapshot hashes differ.
    #[error("snapshot result hash mismatch: expected {expected}, got {actual}")]
    ResultHashMismatch {
        /// Declared result hash.
        expected: ContentHash,
        /// Declared snapshot hash.
        actual: ContentHash,
    },
    /// Delta validation failed before dispatch.
    #[error("invalid snapshot delta: {0}")]
    InvalidDelta(Box<DeltaError>),
    /// Envelope operation count differs from its delta.
    #[error("snapshot operation count does not match its delta")]
    OperationCountMismatch,
    /// Partial/incoherent results may not claim ref capability.
    #[error("partial or incoherent snapshot cannot issue refs")]
    PartialCanNotIssueRefs,
    /// A canonical element could not be represented.
    #[error("snapshot element contains invalid canonical data")]
    InvalidCanonicalData,
    /// The envelope schema is not supported by this core version.
    #[error("unsupported snapshot schema version {0}")]
    UnsupportedSchemaVersion(u16),
    /// The frame-version vector exceeds its bound.
    #[error("snapshot contains {count} frame versions, maximum is {max}")]
    TooManyFrameVersions {
        /// Actual frame-version count.
        count: usize,
        /// Maximum admitted count.
        max: usize,
    },
    /// The changed-key list exceeds its bound.
    #[error("snapshot contains {count} changed keys, maximum is {max}")]
    TooManyChangedKeys {
        /// Actual changed-key count.
        count: usize,
        /// Maximum admitted count.
        max: usize,
    },
    /// The omitted-field list exceeds its bound.
    #[error("snapshot contains {count} omitted fields, maximum is {max}")]
    TooManyOmittedFields {
        /// Actual omitted-field count.
        count: usize,
        /// Maximum admitted count.
        max: usize,
    },
    /// An omitted field name exceeds its byte bound.
    #[error("snapshot omitted field is {size} bytes, maximum is {max}")]
    OmittedFieldTooLarge {
        /// Actual UTF-8 byte length.
        size: usize,
        /// Maximum admitted byte length.
        max: usize,
    },
    /// The tokenizer identifier exceeds its byte bound.
    #[error("snapshot tokenizer is {size} bytes, maximum is {max}")]
    TokenizerTooLarge {
        /// Actual UTF-8 byte length.
        size: usize,
        /// Maximum admitted byte length.
        max: usize,
    },
}

impl SnapshotValidationError {
    /// Map validation failures to a stable core error for protocol callers.
    pub fn core_error(&self) -> CoreError {
        let code = match self {
            Self::TooManyElements { .. }
            | Self::TooManyAttributes { .. }
            | Self::FieldTooLarge { .. }
            | Self::TooManyFrameVersions { .. }
            | Self::TooManyChangedKeys { .. }
            | Self::TooManyOmittedFields { .. }
            | Self::OmittedFieldTooLarge { .. }
            | Self::TokenizerTooLarge { .. } => ErrorCode::MessageTooLarge,
            _ => ErrorCode::InvalidArgument,
        };
        CoreError::new(code, self.to_string())
    }
}

/// Errors raised while applying a bounded delta.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum DeltaError {
    /// The operation count exceeds the configured bound.
    #[error("delta has {count} operations, maximum is {max}")]
    TooManyOperations {
        /// Actual count.
        count: usize,
        /// Maximum count.
        max: usize,
    },
    /// The chain depth exceeds the configured bound.
    #[error("delta chain depth {depth} exceeds {max}")]
    ChainTooLong {
        /// Actual depth.
        depth: u16,
        /// Maximum depth.
        max: u16,
    },
    /// Operations are not in canonical order.
    #[error("delta operations are not in canonical order")]
    NonCanonicalOrder,
    /// A target key appears more than once.
    #[error("delta contains duplicate target {0}")]
    DuplicateTarget(ElementKey),
    /// A frame reset appears more than once.
    #[error("delta contains duplicate frame reset {0}")]
    DuplicateFrameReset(FrameId),
    /// An operation's key differs from its element key.
    #[error("delta key {target} does not match element key {element}")]
    KeyMismatch {
        /// Operation target.
        target: ElementKey,
        /// Embedded element key.
        element: ElementKey,
    },
    /// The result version did not advance beyond the base version.
    #[error("delta result version {result:?} does not advance base version {base:?}")]
    VersionDidNotAdvance {
        /// Base snapshot version.
        base: SnapshotVersion,
        /// Result snapshot version.
        result: SnapshotVersion,
    },
    /// An embedded replacement element violates snapshot bounds.
    #[error("delta contains an invalid element: {0}")]
    InvalidElement(Box<SnapshotValidationError>),
    /// A frame reset needs the frame-version vector that is not present on a document.
    #[error("frame reset for {0} requires a frame-version vector and was rejected")]
    FrameResetRequiresVersionVector(FrameId),
    /// An operation targets an absent element.
    #[error("delta target {0} is missing")]
    MissingTarget(ElementKey),
    /// The base version or hash cannot be applied.
    #[error("delta requires resync: {0:?}")]
    ResyncRequired(ResyncReason),
    /// The declared result hash does not match the applied result.
    #[error("delta result hash mismatch: expected {expected}, got {actual}")]
    ResultHashMismatch {
        /// Declared result hash.
        expected: ContentHash,
        /// Computed result hash.
        actual: ContentHash,
    },
    /// Normalization rejected the resulting document.
    #[error("invalid snapshot result: {0}")]
    InvalidSnapshot(Box<SnapshotValidationError>),
}

impl DeltaError {
    /// Map a delta failure to a stable core error.
    pub fn core_error(&self) -> CoreError {
        match self {
            Self::ResyncRequired(reason) => CoreError::new(
                ErrorCode::StaleRef,
                format!("snapshot delta requires resync: {reason:?}"),
            ),
            Self::TooManyOperations { .. } | Self::ChainTooLong { .. } => {
                CoreError::new(ErrorCode::MessageTooLarge, self.to_string())
            }
            Self::InvalidElement(error) => error.core_error(),
            _ => CoreError::new(ErrorCode::InvalidArgument, self.to_string()),
        }
    }
}

fn normalize_elements(elements: &mut Vec<SnapshotElement>) -> Result<(), SnapshotValidationError> {
    if elements.len() > MAX_SNAPSHOT_ELEMENTS {
        return Err(SnapshotValidationError::TooManyElements {
            count: elements.len(),
            max: MAX_SNAPSHOT_ELEMENTS,
        });
    }
    elements.sort_by(|left, right| {
        left.order
            .cmp(&right.order)
            .then_with(|| left.key.cmp(&right.key))
    });
    let mut keys = BTreeSet::new();
    let mut orders = BTreeSet::new();
    for element in elements {
        if !keys.insert(element.key.clone()) {
            return Err(SnapshotValidationError::DuplicateKey(element.key.clone()));
        }
        if !orders.insert(element.order) {
            return Err(SnapshotValidationError::DuplicateOrder(element.order));
        }
    }
    Ok(())
}

fn hash_elements(elements: &[SnapshotElement]) -> Result<ContentHash, SnapshotValidationError> {
    let mut canonical = String::with_capacity(elements.len().saturating_mul(96).saturating_add(2));
    canonical.push('[');
    for (index, element) in elements.iter().enumerate() {
        if index != 0 {
            canonical.push(',');
        }
        element.canonical_json(&mut canonical);
    }
    canonical.push(']');
    Ok(ContentHash::from_bytes(canonical.as_bytes()))
}

fn append_json_key(output: &mut String, key: &str) {
    append_json_string(output, key);
    output.push(':');
}

fn append_json_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => {
                use std::fmt::Write;
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{DeltaSequence, FrameId, FrameVersion, RefId};

    fn element(suffix: &str, order: u32) -> SnapshotElement {
        SnapshotElement {
            key: ElementKey::from_suffix(suffix).expect("key"),
            parent: None,
            kind: ElementKind::Element,
            text: Some(suffix.to_owned()),
            attributes: BTreeMap::new(),
            order,
        }
    }

    fn full_envelope(elements: Vec<SnapshotElement>) -> SnapshotEnvelope {
        let document = SnapshotDocument::new(SnapshotVersion::new(1), elements.clone())
            .expect("normalized document");
        SnapshotEnvelope {
            schema_version: 1,
            space_id: SpaceId::from_suffix("one").expect("space"),
            page_id: PageId::from_suffix("one").expect("page"),
            snapshot_version: document.snapshot_version,
            snapshot_hash: document.snapshot_hash.clone(),
            base_snapshot_version: None,
            base_hash: None,
            result_hash: document.snapshot_hash,
            delta_sequence: None,
            topology_version: TopologyVersion::new(1),
            navigation_generation: Generation::new(1),
            document_generation: Generation::new(1),
            frame_versions: BTreeMap::new(),
            changed: Vec::new(),
            delta_or_elements: SnapshotBody::Elements { elements },
            mode: SnapshotMode::Full,
            operation_count: 0,
            coherent: true,
            coverage: SnapshotCoverage::Complete,
            dirty_reasons: Vec::new(),
            cache_state: CacheState::Fresh,
            resync_reason: None,
            transport_bytes: 0,
            utf8_bytes: 0,
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

    #[test]
    fn snapshot_envelope_validates_canonical_body_and_hashes() {
        let canonical = vec![element("a", 0), element("b", 1)];
        let envelope = full_envelope(canonical.clone());
        envelope.validate().expect("canonical full envelope");

        let mut noncanonical = envelope.clone();
        noncanonical.delta_or_elements = SnapshotBody::Elements {
            elements: canonical.into_iter().rev().collect(),
        };
        assert!(matches!(
            noncanonical.validate(),
            Err(SnapshotValidationError::NonCanonicalBody)
        ));

        let mut wrong_body_hash = envelope.clone();
        wrong_body_hash.snapshot_hash = ContentHash::from_bytes(b"wrong-body");
        assert!(matches!(
            wrong_body_hash.validate(),
            Err(SnapshotValidationError::BodyHashMismatch { .. })
        ));

        let mut wrong_result_hash = envelope;
        wrong_result_hash.result_hash = ContentHash::from_bytes(b"wrong-result");
        assert!(matches!(
            wrong_result_hash.validate(),
            Err(SnapshotValidationError::ResultHashMismatch { .. })
        ));
    }

    #[test]
    fn snapshot_envelope_validates_delta_metadata_and_operation_order() {
        let base =
            SnapshotDocument::new(SnapshotVersion::new(1), vec![element("a", 0)]).expect("base");
        let result = SnapshotDocument::new(
            SnapshotVersion::new(2),
            vec![element("a", 0), element("b", 1)],
        )
        .expect("result");
        let delta = SnapshotDelta {
            base_snapshot_version: base.snapshot_version,
            base_hash: base.snapshot_hash.clone(),
            result_snapshot_version: result.snapshot_version,
            result_hash: result.snapshot_hash.clone(),
            delta_sequence: DeltaSequence::new(1),
            chain_depth: 1,
            operations: vec![DeltaOperation::Upsert {
                key: ElementKey::from_suffix("b").expect("key"),
                element: element("b", 1),
            }],
        };
        let mut envelope = full_envelope(Vec::new());
        envelope.snapshot_version = result.snapshot_version;
        envelope.snapshot_hash = result.snapshot_hash.clone();
        envelope.base_snapshot_version = Some(delta.base_snapshot_version);
        envelope.base_hash = Some(delta.base_hash.clone());
        envelope.result_hash = delta.result_hash.clone();
        envelope.delta_sequence = Some(delta.delta_sequence);
        envelope.delta_or_elements = SnapshotBody::Delta {
            delta: delta.clone(),
        };
        envelope.mode = SnapshotMode::Delta;
        envelope.operation_count = delta.operations.len() as u32;
        envelope.validate().expect("canonical delta envelope");

        let mut bad_metadata = envelope.clone();
        bad_metadata.base_hash = Some(ContentHash::from_bytes(b"wrong-base"));
        assert!(matches!(
            bad_metadata.validate(),
            Err(SnapshotValidationError::DeltaMetadataMismatch)
        ));

        let mut bad_order = envelope;
        if let SnapshotBody::Delta { delta } = &mut bad_order.delta_or_elements {
            delta.operations.push(DeltaOperation::Remove {
                key: ElementKey::from_suffix("a").expect("key"),
            });
            bad_order.operation_count = delta.operations.len() as u32;
        }
        assert!(matches!(
            bad_order.validate(),
            Err(SnapshotValidationError::InvalidDelta(error))
                if matches!(*error, DeltaError::NonCanonicalOrder)
        ));
    }

    #[test]
    fn delta_apply_is_hash_checked_and_ordered() {
        let base = SnapshotDocument::new(
            SnapshotVersion::new(1),
            vec![element("a", 0), element("b", 1)],
        )
        .expect("base");
        let replacement = element("c", 1);
        let expected = SnapshotDocument::new(
            SnapshotVersion::new(2),
            vec![element("a", 0), replacement.clone()],
        )
        .expect("expected");
        let delta = SnapshotDelta {
            base_snapshot_version: base.snapshot_version,
            base_hash: base.snapshot_hash.clone(),
            result_snapshot_version: expected.snapshot_version,
            result_hash: expected.snapshot_hash.clone(),
            delta_sequence: DeltaSequence::new(1),
            chain_depth: 1,
            operations: vec![
                DeltaOperation::Remove {
                    key: ElementKey::from_suffix("b").expect("key"),
                },
                DeltaOperation::Upsert {
                    key: ElementKey::from_suffix("c").expect("key"),
                    element: replacement,
                },
            ],
        };
        assert!(delta.validate(DeltaLimits::default()).is_ok());
        let applied = delta.apply(&base, DeltaLimits::default()).expect("apply");
        assert_eq!(applied, expected);
        assert!(matches!(
            delta.apply(
                &SnapshotDocument {
                    snapshot_hash: ContentHash::from_bytes(b"wrong"),
                    ..base.clone()
                },
                DeltaLimits::default()
            ),
            Err(DeltaError::ResyncRequired(ResyncReason::BaseHashMismatch))
        ));
    }

    #[test]
    fn stale_refs_are_rejected_after_generation_or_epoch_changes() {
        let space = SpaceId::from_suffix("one").expect("space");
        let page = PageId::from_suffix("one").expect("page");
        let provenance = SnapshotProvenance {
            space_id: space.clone(),
            page_id: page.clone(),
            snapshot_version: SnapshotVersion::new(3),
            snapshot_hash: ContentHash::from_bytes(b"snapshot"),
            document_generation: Generation::new(4),
            navigation_generation: Generation::new(5),
            refs_epoch: RefEpoch::new(2),
            coherent: true,
            coverage: SnapshotCoverage::Complete,
        };
        let reference = ElementRef {
            ref_id: RefId::from_suffix("one").expect("ref"),
            element_key: ElementKey::from_suffix("target").expect("element key"),
            space_id: space,
            page_id: page,
            frame_id: FrameId::from_suffix("main").expect("frame"),
            snapshot_version: SnapshotVersion::new(3),
            document_generation: Generation::new(4),
            navigation_generation: Generation::new(5),
            refs_epoch: RefEpoch::new(1),
        };
        let error = reference
            .validate_against(&provenance)
            .expect_err("stale ref");
        assert_eq!(error.code, ErrorCode::StaleRef);
    }

    #[test]
    fn partial_snapshots_cannot_issue_refs() {
        let provenance = SnapshotProvenance {
            space_id: SpaceId::from_suffix("one").expect("space"),
            page_id: PageId::from_suffix("one").expect("page"),
            snapshot_version: SnapshotVersion::new(1),
            snapshot_hash: ContentHash::from_bytes(b"snapshot"),
            document_generation: Generation::new(1),
            navigation_generation: Generation::new(1),
            refs_epoch: RefEpoch::new(1),
            coherent: false,
            coverage: SnapshotCoverage::Partial,
        };
        assert!(!provenance.can_issue_refs());
    }

    #[test]
    fn representation_decision_falls_back_deterministically() {
        assert_eq!(
            choose_snapshot_decision(false, false, false, 1, 100, 0, DeltaLimits::default()),
            SnapshotDecision::Full
        );
        assert_eq!(
            choose_snapshot_decision(true, false, true, 1, 100, 1, DeltaLimits::default()),
            SnapshotDecision::Resync {
                reason: ResyncReason::BaseHashMismatch
            }
        );
        assert_eq!(
            choose_snapshot_decision(true, true, true, 20, 100, 1, DeltaLimits::default()),
            SnapshotDecision::Delta
        );
    }

    #[test]
    fn snapshot_bounds_provenance_and_ref_issuance_fail_closed() {
        let mut oversized = element("large", 0);
        oversized.text = Some("x".repeat(MAX_SNAPSHOT_TEXT_BYTES + 1));
        assert!(matches!(
            oversized.validate(),
            Err(SnapshotValidationError::FieldTooLarge { field: "text", .. })
        ));

        let mut envelope = full_envelope(vec![element("one", 0)]);
        envelope.changed = vec![ElementKey::from_suffix("one").expect("key")];
        envelope.validate().expect("valid envelope");
        let reference = envelope
            .make_ref(
                RefId::from_suffix("one").expect("ref"),
                FrameId::from_suffix("main").expect("frame"),
                ElementKey::from_suffix("one").expect("element key"),
            )
            .expect("validated ref");
        reference
            .validate_against(&envelope.provenance())
            .expect("matching provenance");

        let missing_key = ElementKey::from_suffix("missing").expect("element key");
        let error = envelope
            .make_ref(
                RefId::from_suffix("missing").expect("ref"),
                FrameId::from_suffix("main").expect("frame"),
                missing_key,
            )
            .expect_err("an element key outside the snapshot cannot issue a ref");
        assert_eq!(error.code, ErrorCode::StaleRef);

        let mut missing_element_key = serde_json::to_value(&reference).expect("ref json");
        missing_element_key
            .as_object_mut()
            .expect("ref object")
            .remove("element_key");
        assert!(
            serde_json::from_value::<ElementRef>(missing_element_key).is_err(),
            "an element ref without an element key must be rejected"
        );

        let mut malformed = envelope.clone();
        malformed.omitted = vec!["x".repeat(MAX_SNAPSHOT_OMITTED_FIELD_BYTES + 1)];
        let error = malformed
            .make_ref(
                RefId::from_suffix("bad").expect("ref"),
                FrameId::from_suffix("main").expect("frame"),
                ElementKey::from_suffix("one").expect("element key"),
            )
            .expect_err("malformed envelope cannot issue refs");
        assert_eq!(error.code, ErrorCode::MessageTooLarge);

        let invalid_provenance = SnapshotProvenance {
            coherent: false,
            coverage: SnapshotCoverage::Complete,
            ..envelope.provenance()
        };
        assert!(invalid_provenance.validate().is_err());
        assert_eq!(
            reference
                .validate_against(&invalid_provenance)
                .expect_err("invalid provenance")
                .code,
            ErrorCode::InvalidArgument
        );
    }

    #[test]
    fn frame_reset_delta_is_validated_but_apply_fails_closed() {
        let base =
            SnapshotDocument::new(SnapshotVersion::new(1), vec![element("a", 0)]).expect("base");
        let delta = SnapshotDelta {
            base_snapshot_version: base.snapshot_version,
            base_hash: base.snapshot_hash.clone(),
            result_snapshot_version: SnapshotVersion::new(2),
            result_hash: base.snapshot_hash.clone(),
            delta_sequence: DeltaSequence::new(1),
            chain_depth: 1,
            operations: vec![DeltaOperation::FrameReset {
                frame_id: FrameId::from_suffix("child").expect("frame"),
                frame_version: FrameVersion::new(2),
            }],
        };
        delta
            .validate(DeltaLimits::default())
            .expect("frame reset shape");
        assert!(matches!(
            delta.apply(&base, DeltaLimits::default()),
            Err(DeltaError::FrameResetRequiresVersionVector(frame))
                if frame == FrameId::from_suffix("child").expect("frame")
        ));
    }
}
