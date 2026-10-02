//! Bounded context construction over validated host snapshots.
//!
//! Context construction does not scan the browser. It consumes a broker
//! [`SnapshotRead`], preserves clean-cache zero-scan metadata, and chooses a
//! representation only after deterministic coverage and serialized-cost checks.

use std::collections::{BTreeMap, BTreeSet};

use agentyc_core::{
    CacheState, ContentHash, DeltaLimits, DeltaOperation, DeltaSequence, ElementKey, ElementKind,
    Generation, PageId, RefEpoch, ResyncReason, SnapshotBody, SnapshotCoverage, SnapshotDelta,
    SnapshotDocument, SnapshotEnvelope, SnapshotVersion, SpaceId, TokenBudget,
};
use serde::{Deserialize, Serialize};

use crate::snapshots::SnapshotRead;

/// Requested context representation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    /// Choose the least costly valid representation.
    Auto,
    /// Return every retained logical element.
    Full,
    /// Return all logical keys with optional fields compacted.
    Compact,
    /// Return a bounded patch against the supplied base.
    Delta,
}

/// Alias used by callers that name the request mode explicitly.
pub type ContextRequestMode = ContextMode;

/// Representation actually returned to the caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextRepresentation {
    /// Only provenance/cache metadata was returned.
    Metadata,
    /// Complete element body.
    Full,
    /// All logical keys with compact optional fields.
    Compact,
    /// Validated delta body.
    Delta,
    /// A resynchronization marker is required.
    Resync,
}

/// Deterministic field-name redaction policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RedactionPolicy {
    /// Case-insensitive attribute names or name fragments to redact.
    pub sensitive_fields: BTreeSet<String>,
    /// Replacement inserted instead of a sensitive value.
    pub replacement: String,
}

impl Default for RedactionPolicy {
    fn default() -> Self {
        Self {
            sensitive_fields: BTreeSet::from([
                "authorization".to_owned(),
                "api_key".to_owned(),
                "apikey".to_owned(),
                "bearer".to_owned(),
                "card_number".to_owned(),
                "cookie".to_owned(),
                "credit_card".to_owned(),
                "cvv".to_owned(),
                "password".to_owned(),
                "secret".to_owned(),
                "ssn".to_owned(),
                "token".to_owned(),
                "value".to_owned(),
            ]),
            replacement: "[REDACTED]".to_owned(),
        }
    }
}

impl RedactionPolicy {
    /// Return whether an output field must be redacted.
    pub fn is_sensitive(&self, name: &str) -> bool {
        let normalized = name.to_ascii_lowercase().replace('-', "_");
        self.sensitive_fields.iter().any(|field| {
            let field = field.to_ascii_lowercase().replace('-', "_");
            normalized == field || normalized.contains(&field)
        })
    }

    /// Add a case-insensitive sensitive field fragment.
    pub fn add_sensitive_field(&mut self, field: impl Into<String>) {
        self.sensitive_fields
            .insert(field.into().to_ascii_lowercase());
    }
}

/// Request/options used by [`ContextBuilder`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContextRequest {
    /// Desired representation policy.
    pub mode: ContextMode,
    /// Optional validated base envelope for delta construction.
    pub base: Option<SnapshotEnvelope>,
    /// Maximum serialized context bytes, including metadata/body estimates.
    pub max_serialized_bytes: Option<usize>,
    /// Optional token limits applied after deterministic byte measurement.
    pub token_budget: Option<TokenBudget>,
    /// Tokenizer identifier attached to output metrics.
    pub tokenizer: Option<String>,
    /// Bounded delta validation limits.
    pub delta_limits: DeltaLimits,
    /// Minimum logical coverage accepted for a body representation.
    pub minimum_coverage: SnapshotCoverage,
    /// Whether a clean cache should return metadata without its cached body.
    pub clean_cache_metadata_only: bool,
    /// Whether a budget overflow may return a resync marker.
    pub allow_resync: bool,
    /// Output redaction policy.
    pub redaction: RedactionPolicy,
}

impl ContextRequest {
    /// Construct an automatic representation request.
    pub fn auto() -> Self {
        Self::default()
    }

    /// Construct a full representation request.
    pub fn full() -> Self {
        Self {
            mode: ContextMode::Full,
            ..Self::default()
        }
    }

    /// Construct a compact representation request.
    pub fn compact() -> Self {
        Self {
            mode: ContextMode::Compact,
            ..Self::default()
        }
    }

    /// Construct a delta request against a base envelope.
    pub fn delta(base: SnapshotEnvelope) -> Self {
        Self {
            mode: ContextMode::Delta,
            base: Some(base),
            ..Self::default()
        }
    }

    /// Replace the optional delta base.
    #[must_use]
    pub fn with_base(mut self, base: Option<SnapshotEnvelope>) -> Self {
        self.base = base;
        self
    }

    /// Set a serialized-byte budget.
    #[must_use]
    pub const fn with_max_serialized_bytes(mut self, limit: Option<usize>) -> Self {
        self.max_serialized_bytes = limit;
        self
    }
}

impl Default for ContextRequest {
    fn default() -> Self {
        Self {
            mode: ContextMode::Auto,
            base: None,
            max_serialized_bytes: None,
            token_budget: None,
            tokenizer: None,
            delta_limits: DeltaLimits::default(),
            minimum_coverage: SnapshotCoverage::Complete,
            clean_cache_metadata_only: true,
            allow_resync: true,
            redaction: RedactionPolicy::default(),
        }
    }
}

/// Alias for callers that use an options name.
pub type ContextOptions = ContextRequest;

/// Cache/provenance metadata kept when a clean read intentionally omits DOM data.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextMetadata {
    /// Logical owning space.
    pub space_id: SpaceId,
    /// Logical owning page.
    pub page_id: PageId,
    /// Snapshot version represented by the metadata.
    pub snapshot_version: SnapshotVersion,
    /// Result snapshot hash.
    pub snapshot_hash: ContentHash,
    /// Navigation generation.
    pub navigation_generation: Generation,
    /// Document generation.
    pub document_generation: Generation,
    /// Ref epoch associated with the snapshot.
    pub refs_epoch: RefEpoch,
    /// Cache freshness.
    pub cache_state: CacheState,
    /// Whether the broker performed a bridge scan for this result.
    pub scan_performed: bool,
    /// Whether this is the clean-cache metadata-only form.
    pub clean_cache: bool,
    /// Logical coverage of the body/underlying snapshot.
    pub coverage: SnapshotCoverage,
    /// Representation mode selected for the output.
    pub representation: ContextRepresentation,
    /// Whether sensitive output fields were redacted.
    pub redacted: bool,
    /// Whether the body is incomplete.
    pub truncated: bool,
    /// Whether the consumer must resynchronize before using the body as a base.
    pub resync_required: bool,
    /// Optional resynchronization cause.
    pub resync_reason: Option<ResyncReason>,
}

/// Token and byte measurements attached to every context result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenMetrics {
    /// Bytes in the transport representation estimate.
    pub transport_bytes: u64,
    /// UTF-8 bytes in serialized metadata/body.
    pub utf8_bytes: u64,
    /// Deterministic serialized token estimate.
    pub serialized_tokens: u64,
    /// Deterministic model-context token estimate.
    pub model_context_tokens: u64,
    /// Tokenizer identifier used for the estimate.
    pub tokenizer: Option<String>,
    /// Applied token budget, if any.
    pub budget: Option<TokenBudget>,
}

/// Body returned by the context builder. `SnapshotBody::Elements` is always
/// redacted before it is placed in this type.
pub type ContextBody = SnapshotBody;

/// Compact context result with metadata separated from optional DOM content.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextOutput {
    /// Cache/provenance metadata.
    pub metadata: ContextMetadata,
    /// Optional redacted body; absent for clean-cache metadata-only results.
    pub body: Option<ContextBody>,
    /// Token/byte metrics for this output.
    pub metrics: TokenMetrics,
}

impl ContextOutput {
    /// Return whether no DOM body was emitted because the cache was clean.
    pub const fn is_clean_cache_metadata_only(&self) -> bool {
        self.metadata.clean_cache && self.body.is_none()
    }

    /// Return whether this result cannot safely serve as a complete base.
    pub const fn needs_resync(&self) -> bool {
        self.metadata.resync_required
    }

    /// Serialize the redacted output deterministically.
    pub fn to_json(&self) -> Result<Vec<u8>, serde_json::Error> {
        serde_json::to_vec(self)
    }
}

#[derive(Debug, Clone, Copy)]
struct MetadataState {
    cache_state: CacheState,
    scan_performed: bool,
    clean_cache: bool,
    coverage: SnapshotCoverage,
    representation: ContextRepresentation,
    redacted: bool,
    truncated: bool,
    resync_required: bool,
    resync_reason: Option<ResyncReason>,
}

/// Stateless context builder. Options belong to each request so callers can
/// safely reuse one builder across logical pages.
#[derive(Debug, Default, Clone, Copy)]
pub struct ContextBuilder;

impl ContextBuilder {
    /// Construct a context builder.
    pub const fn new() -> Self {
        Self
    }

    /// Build context from a broker snapshot read.
    pub fn build(
        &self,
        read: &SnapshotRead,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        self.build_envelope(
            &read.envelope,
            read.cache_state,
            read.scan_performed,
            request,
        )
    }

    /// Build context from an envelope using fresh-read semantics.
    pub fn build_snapshot(
        &self,
        envelope: &SnapshotEnvelope,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        self.build_envelope(envelope, CacheState::Fresh, true, request)
    }

    /// Alias for [`Self::build`].
    pub fn build_read(
        &self,
        read: &SnapshotRead,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        self.build(read, request)
    }

    /// Build context from a snapshot envelope when no broker read metadata is available.
    pub fn build_envelope(
        &self,
        envelope: &SnapshotEnvelope,
        cache_state: CacheState,
        scan_performed: bool,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        envelope
            .validate()
            .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
        if cache_state == CacheState::Cached && !scan_performed {
            return self.metadata_only(envelope, cache_state, scan_performed, request);
        }

        let current_document =
            match current_document(envelope, request.base.as_ref(), &request.redaction) {
                Ok(document) => document,
                Err(error) => {
                    let reason = envelope.resync_reason.unwrap_or(ResyncReason::BaseMissing);
                    return if request.allow_resync {
                        self.resync_output(envelope, cache_state, scan_performed, request, reason)
                    } else {
                        Err(error)
                    };
                }
            };
        let (current_elements, redacted_output) = match &envelope.delta_or_elements {
            SnapshotBody::Elements { .. } => {
                let result = redact_elements_from_envelope(envelope, &request.redaction)?;
                (result.elements, result.redacted)
            }
            SnapshotBody::Delta { .. } => {
                redact_elements(&current_document.elements, &request.redaction)
            }
            SnapshotBody::Resync { .. } => (Vec::new(), false),
        };
        let compact_elements = compact_elements(&current_elements, &request.redaction);
        let full_body = SnapshotBody::Elements {
            elements: current_elements.clone(),
        };
        let compact_body = SnapshotBody::Elements {
            elements: compact_elements,
        };
        let base_document = request
            .base
            .as_ref()
            .and_then(|base| redacted_base_document(base, &request.redaction).ok());
        let delta_body = base_document.as_ref().and_then(|base| {
            if !base_matches(envelope, request.base.as_ref()?, base) {
                return None;
            }
            build_delta_body(
                envelope,
                request.base.as_ref()?,
                base,
                &current_document,
                request.delta_limits,
            )
            .ok()
        });

        let full_cost = body_cost(&full_body)?;
        let compact_cost = body_cost(&compact_body)?;
        let delta_cost = delta_body.as_ref().map(body_cost).transpose()?;
        let selected = select_body(
            request,
            envelope,
            BodyCandidates {
                full: full_body,
                compact: compact_body,
                delta: delta_body,
                full_cost,
                compact_cost,
                delta_cost,
            },
        )?;

        let Some(selected) = selected else {
            let reason = if !envelope.coherent || envelope.truncated || envelope.resync_required {
                ResyncReason::Incoherent
            } else {
                ResyncReason::CostExceeded
            };
            return if request.allow_resync {
                self.resync_output(envelope, cache_state, scan_performed, request, reason)
            } else {
                Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::MessageTooLarge,
                    "no context representation fits the requested budget",
                ))
            };
        };

        let (representation, mut body, mut coverage, _) = selected;
        let mut truncated = false;
        let mut resync_required = envelope.resync_required;
        let mut resync_reason = envelope.resync_reason;
        if representation == ContextRepresentation::Delta && body.is_none() {
            resync_required = true;
            resync_reason = Some(ResyncReason::BaseMissing);
        }
        if representation == ContextRepresentation::Delta
            && envelope.coverage != SnapshotCoverage::Complete
        {
            coverage = SnapshotCoverage::Partial;
            resync_required = true;
            resync_reason = Some(ResyncReason::Incoherent);
        }

        let mut metadata = make_metadata(
            envelope,
            MetadataState {
                cache_state,
                scan_performed,
                clean_cache: false,
                coverage,
                representation,
                redacted: redacted_output,
                truncated: false,
                resync_required,
                resync_reason,
            },
        );
        if !fits_budget(&metadata, body.as_ref(), request)?
            && let Some(ContextBody::Elements { elements }) = body.as_mut()
        {
            let original_len = elements.len();
            truncate_elements(elements, request)?;
            truncated = elements.len() < original_len;
            if truncated {
                coverage = SnapshotCoverage::Partial;
                resync_required = true;
                resync_reason = Some(ResyncReason::CostExceeded);
                metadata.coverage = coverage;
                metadata.truncated = true;
                metadata.resync_required = true;
                metadata.resync_reason = resync_reason;
            }
        }
        if !fits_budget(&metadata, body.as_ref(), request)? {
            if request.allow_resync {
                return self.resync_output(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    ResyncReason::CostExceeded,
                );
            }
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "context output exceeds its serialized budget",
            ));
        }
        metadata.truncated = truncated;
        metadata.resync_required = resync_required;
        metadata.resync_reason = resync_reason;
        let metrics = measure(&metadata, body.as_ref(), request);
        Ok(ContextOutput {
            metadata,
            body,
            metrics,
        })
    }

    /// Build metadata-only output for a clean cache without reading its body.
    fn metadata_only(
        &self,
        envelope: &SnapshotEnvelope,
        cache_state: CacheState,
        scan_performed: bool,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        let metadata = make_metadata(
            envelope,
            MetadataState {
                cache_state,
                scan_performed,
                clean_cache: true,
                coverage: envelope.coverage,
                representation: ContextRepresentation::Metadata,
                redacted: false,
                truncated: false,
                resync_required: envelope.resync_required,
                resync_reason: envelope.resync_reason,
            },
        );
        if !fits_budget(&metadata, None, request)? {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "clean-cache context metadata exceeds its serialized budget",
            ));
        }
        let metrics = measure(&metadata, None, request);
        Ok(ContextOutput {
            metadata,
            body: None,
            metrics,
        })
    }

    fn resync_output(
        &self,
        envelope: &SnapshotEnvelope,
        cache_state: CacheState,
        scan_performed: bool,
        request: &ContextRequest,
        reason: ResyncReason,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        let metadata = make_metadata(
            envelope,
            MetadataState {
                cache_state,
                scan_performed,
                clean_cache: false,
                coverage: SnapshotCoverage::Partial,
                representation: ContextRepresentation::Resync,
                redacted: false,
                truncated: false,
                resync_required: true,
                resync_reason: Some(reason),
            },
        );
        let body = Some(SnapshotBody::Resync { reason });
        if !fits_budget(&metadata, body.as_ref(), request)? {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "resynchronization context marker exceeds its serialized budget",
            ));
        }
        let metrics = measure(&metadata, body.as_ref(), request);
        Ok(ContextOutput {
            metadata,
            body,
            metrics,
        })
    }
}

fn make_metadata(envelope: &SnapshotEnvelope, state: MetadataState) -> ContextMetadata {
    ContextMetadata {
        space_id: envelope.space_id.clone(),
        page_id: envelope.page_id.clone(),
        snapshot_version: envelope.snapshot_version,
        snapshot_hash: envelope.snapshot_hash.clone(),
        navigation_generation: envelope.navigation_generation,
        document_generation: envelope.document_generation,
        refs_epoch: envelope.refs_epoch,
        cache_state: state.cache_state,
        scan_performed: state.scan_performed,
        clean_cache: state.clean_cache,
        coverage: state.coverage,
        representation: state.representation,
        redacted: state.redacted,
        truncated: state.truncated,
        resync_required: state.resync_required,
        resync_reason: state.resync_reason,
    }
}

fn current_document(
    envelope: &SnapshotEnvelope,
    base: Option<&SnapshotEnvelope>,
    redaction: &RedactionPolicy,
) -> Result<SnapshotDocument, agentyc_core::CoreError> {
    match &envelope.delta_or_elements {
        SnapshotBody::Elements { elements } => SnapshotDocument::new(
            envelope.snapshot_version,
            redact_elements(elements, redaction).0,
        )
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string())),
        SnapshotBody::Delta { delta } => {
            let Some(base) = base else {
                return Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::EventLagged,
                    "delta context requires a retained base",
                ));
            };
            let base_document = redacted_base_document(base, redaction)?;
            delta
                .apply(&base_document, DeltaLimits::default())
                .map_err(|error| error.core_error())
        }
        SnapshotBody::Resync { reason } => Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::EventLagged,
            format!("snapshot requires resync: {reason:?}"),
        )),
    }
}

fn redacted_base_document(
    base: &SnapshotEnvelope,
    redaction: &RedactionPolicy,
) -> Result<SnapshotDocument, agentyc_core::CoreError> {
    base.validate()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    match &base.delta_or_elements {
        SnapshotBody::Elements { elements } => SnapshotDocument::new(
            base.snapshot_version,
            redact_elements(elements, redaction).0,
        )
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string())),
        SnapshotBody::Delta { .. } | SnapshotBody::Resync { .. } => {
            Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::EventLagged,
                "delta base must be a complete element snapshot",
            ))
        }
    }
}

fn redact_elements(
    elements: &[agentyc_core::SnapshotElement],
    policy: &RedactionPolicy,
) -> (Vec<agentyc_core::SnapshotElement>, bool) {
    let mut redacted = false;
    let result = elements
        .iter()
        .map(|element| {
            let mut element = element.clone();
            for (key, value) in &mut element.attributes {
                if policy.is_sensitive(key) && value != &policy.replacement {
                    *value = policy.replacement.clone();
                    redacted = true;
                }
            }
            element
        })
        .collect();
    (result, redacted)
}

fn redact_elements_from_envelope(
    envelope: &SnapshotEnvelope,
    policy: &RedactionPolicy,
) -> Result<RedactionResult, agentyc_core::CoreError> {
    match &envelope.delta_or_elements {
        SnapshotBody::Elements { elements } => {
            let (elements, redacted) = redact_elements(elements, policy);
            Ok(RedactionResult { elements, redacted })
        }
        SnapshotBody::Delta { .. } | SnapshotBody::Resync { .. } => {
            Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::EventLagged,
                "context body cannot be inspected without a complete base",
            ))
        }
    }
}

struct RedactionResult {
    elements: Vec<agentyc_core::SnapshotElement>,
    redacted: bool,
}

fn compact_elements(
    elements: &[agentyc_core::SnapshotElement],
    policy: &RedactionPolicy,
) -> Vec<agentyc_core::SnapshotElement> {
    elements
        .iter()
        .map(|element| {
            let mut compact = element.clone();
            if !matches!(element.kind, ElementKind::Control | ElementKind::Frame) {
                compact.text = None;
                compact.attributes.clear();
            } else {
                compact.attributes.retain(|key, _| {
                    matches!(
                        key.to_ascii_lowercase().as_str(),
                        "id" | "name" | "role" | "type" | "aria-label" | "placeholder" | "href"
                    ) || policy.is_sensitive(key)
                });
            }
            compact
        })
        .collect()
}

fn base_matches(
    current: &SnapshotEnvelope,
    base_envelope: &SnapshotEnvelope,
    base: &SnapshotDocument,
) -> bool {
    base_envelope.space_id == current.space_id
        && base_envelope.page_id == current.page_id
        && base_envelope.snapshot_hash == base.snapshot_hash
        && base_envelope.navigation_generation == current.navigation_generation
        && base_envelope.snapshot_version != current.snapshot_version
}

fn build_delta_body(
    current: &SnapshotEnvelope,
    base_envelope: &SnapshotEnvelope,
    base: &SnapshotDocument,
    result: &SnapshotDocument,
    limits: DeltaLimits,
) -> Result<SnapshotBody, agentyc_core::CoreError> {
    let base_by_key: BTreeMap<&ElementKey, &agentyc_core::SnapshotElement> = base
        .elements
        .iter()
        .map(|element| (&element.key, element))
        .collect();
    let result_by_key: BTreeMap<&ElementKey, &agentyc_core::SnapshotElement> = result
        .elements
        .iter()
        .map(|element| (&element.key, element))
        .collect();
    let mut operations = Vec::new();
    for key in base_by_key.keys() {
        if !result_by_key.contains_key(key) {
            operations.push(DeltaOperation::Remove {
                key: (*key).clone(),
            });
        }
    }
    for (key, element) in &result_by_key {
        match base_by_key.get(key) {
            None => operations.push(DeltaOperation::Upsert {
                key: (*key).clone(),
                element: (*element).clone(),
            }),
            Some(previous) if *previous != *element => {
                let mut previous_without_order = (*previous).clone();
                let result_without_order = (*element).clone();
                previous_without_order.order = result_without_order.order;
                if previous_without_order == result_without_order {
                    operations.push(DeltaOperation::Move {
                        key: (*key).clone(),
                        order: element.order,
                        before: None,
                    });
                } else {
                    operations.push(DeltaOperation::Replace {
                        key: (*key).clone(),
                        element: (*element).clone(),
                    });
                }
            }
            Some(_) => {}
        }
    }
    for (frame_id, frame_version) in &current.frame_versions {
        if base_envelope.frame_versions.get(frame_id).copied() != Some(*frame_version) {
            operations.push(DeltaOperation::FrameReset {
                frame_id: frame_id.clone(),
                frame_version: *frame_version,
            });
        }
    }
    operations.sort_by_key(delta_sort_key);
    let delta_sequence = current
        .delta_sequence
        .unwrap_or_else(|| DeltaSequence::new(1));
    let chain_depth = current_chain_depth(current)
        .or_else(|| Some(base_chain_depth(base)))
        .unwrap_or(0)
        .saturating_add(1);
    let delta = SnapshotDelta {
        base_snapshot_version: base.snapshot_version,
        base_hash: base.snapshot_hash.clone(),
        result_snapshot_version: result.snapshot_version,
        result_hash: result.snapshot_hash.clone(),
        delta_sequence,
        chain_depth,
        operations,
    };
    delta.validate(limits).map_err(|error| error.core_error())?;
    Ok(SnapshotBody::Delta { delta })
}

fn current_chain_depth(envelope: &SnapshotEnvelope) -> Option<u16> {
    match &envelope.delta_or_elements {
        SnapshotBody::Delta { delta } => Some(delta.chain_depth),
        SnapshotBody::Elements { .. } | SnapshotBody::Resync { .. } => None,
    }
}

fn base_chain_depth(_base: &SnapshotDocument) -> u16 {
    0
}

fn delta_sort_key(operation: &DeltaOperation) -> (u8, String) {
    match operation {
        DeltaOperation::FrameReset { frame_id, .. } => (0, frame_id.as_str().to_owned()),
        DeltaOperation::Remove { key } => (1, key.as_str().to_owned()),
        DeltaOperation::Replace { key, .. } => (2, key.as_str().to_owned()),
        DeltaOperation::Upsert { key, .. } => (3, key.as_str().to_owned()),
        DeltaOperation::Move { key, .. } => (4, key.as_str().to_owned()),
    }
}

fn body_cost(body: &SnapshotBody) -> Result<usize, agentyc_core::CoreError> {
    serde_json::to_vec(body)
        .map(|bytes| bytes.len())
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))
}

type SelectedBody = (
    ContextRepresentation,
    Option<SnapshotBody>,
    SnapshotCoverage,
    bool,
);

struct BodyCandidates {
    full: SnapshotBody,
    compact: SnapshotBody,
    delta: Option<SnapshotBody>,
    full_cost: usize,
    compact_cost: usize,
    delta_cost: Option<usize>,
}

fn select_body(
    request: &ContextRequest,
    envelope: &SnapshotEnvelope,
    candidates: BodyCandidates,
) -> Result<Option<SelectedBody>, agentyc_core::CoreError> {
    let BodyCandidates {
        full,
        compact,
        delta,
        full_cost,
        compact_cost,
        delta_cost,
    } = candidates;
    let complete = envelope.coherent
        && !envelope.truncated
        && !envelope.resync_required
        && envelope.coverage == SnapshotCoverage::Complete;
    let body_safe = envelope.coherent && !envelope.truncated && !envelope.resync_required;
    let allowed = |coverage: SnapshotCoverage| coverage_allowed(coverage, request.minimum_coverage);
    let mut candidates: Vec<(ContextRepresentation, SnapshotBody, usize)> = Vec::new();
    if body_safe
        && allowed(if complete {
            SnapshotCoverage::Complete
        } else {
            SnapshotCoverage::Partial
        })
    {
        candidates.push((ContextRepresentation::Full, full, full_cost));
        candidates.push((ContextRepresentation::Compact, compact, compact_cost));
        if let (Some(delta), Some(cost)) = (delta, delta_cost)
            && complete
        {
            candidates.push((ContextRepresentation::Delta, delta, cost));
        }
    }
    let delta_available = delta_cost.is_some();
    let selected = match request.mode {
        ContextMode::Full => candidates
            .iter()
            .find(|(representation, _, _)| *representation == ContextRepresentation::Full)
            .map(|(representation, body, _)| {
                (*representation, body.clone(), envelope.coverage, false)
            }),
        ContextMode::Compact => candidates
            .iter()
            .find(|(representation, _, _)| *representation == ContextRepresentation::Compact)
            .map(|(representation, body, _)| {
                (*representation, body.clone(), envelope.coverage, false)
            }),
        ContextMode::Delta => {
            if !delta_available {
                None
            } else if delta_cost.is_some_and(|cost| cost < full_cost && cost < compact_cost) {
                candidates
                    .iter()
                    .find(|(representation, _, _)| *representation == ContextRepresentation::Delta)
                    .map(|(representation, body, _)| {
                        (
                            *representation,
                            body.clone(),
                            SnapshotCoverage::Complete,
                            false,
                        )
                    })
            } else {
                candidates
                    .iter()
                    .filter(|(representation, _, _)| {
                        matches!(
                            representation,
                            ContextRepresentation::Compact | ContextRepresentation::Full
                        )
                    })
                    .min_by(|left, right| left.2.cmp(&right.2))
                    .map(|(representation, body, _)| {
                        (*representation, body.clone(), envelope.coverage, false)
                    })
            }
        }
        ContextMode::Auto => candidates
            .iter()
            .min_by(|left, right| {
                left.2
                    .cmp(&right.2)
                    .then_with(|| representation_rank(left.0).cmp(&representation_rank(right.0)))
            })
            .map(|(representation, body, _)| {
                (*representation, body.clone(), envelope.coverage, false)
            }),
    };
    Ok(selected.map(|(representation, body, coverage, redacted)| {
        (representation, Some(body), coverage, redacted)
    }))
}

fn representation_rank(representation: ContextRepresentation) -> u8 {
    match representation {
        ContextRepresentation::Delta => 0,
        ContextRepresentation::Compact => 1,
        ContextRepresentation::Full => 2,
        ContextRepresentation::Metadata | ContextRepresentation::Resync => 3,
    }
}

fn coverage_allowed(actual: SnapshotCoverage, minimum: SnapshotCoverage) -> bool {
    matches!(minimum, SnapshotCoverage::Partial) || actual == SnapshotCoverage::Complete
}

fn truncate_elements(
    elements: &mut Vec<agentyc_core::SnapshotElement>,
    request: &ContextRequest,
) -> Result<(), agentyc_core::CoreError> {
    let limit = request.max_serialized_bytes.unwrap_or(0);
    if limit == 0 {
        elements.clear();
        return Ok(());
    }
    while !elements.is_empty() {
        let body = SnapshotBody::Elements {
            elements: elements.clone(),
        };
        let bytes = body_cost(&body)?;
        if bytes <= limit {
            break;
        }
        elements.pop();
    }
    Ok(())
}

fn fits_budget(
    metadata: &ContextMetadata,
    body: Option<&SnapshotBody>,
    request: &ContextRequest,
) -> Result<bool, agentyc_core::CoreError> {
    let metadata_bytes = serde_json::to_vec(metadata)
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?
        .len();
    let body_bytes = body.map(body_cost).transpose()?.unwrap_or(0);
    let bytes = metadata_bytes.saturating_add(body_bytes);
    if request
        .max_serialized_bytes
        .is_some_and(|limit| bytes > limit)
    {
        return Ok(false);
    }
    let serialized_tokens = estimate_tokens(bytes);
    let model_context_tokens = estimate_tokens(bytes);
    if request.token_budget.is_some_and(|budget| {
        budget
            .serialized_limit
            .is_some_and(|limit| serialized_tokens > limit)
    }) || request.token_budget.is_some_and(|budget| {
        budget
            .model_context_limit
            .is_some_and(|limit| model_context_tokens > limit)
    }) {
        return Ok(false);
    }
    Ok(true)
}

fn measure(
    metadata: &ContextMetadata,
    body: Option<&SnapshotBody>,
    request: &ContextRequest,
) -> TokenMetrics {
    let metadata_bytes = serde_json::to_vec(metadata).map_or(0, |bytes| bytes.len());
    let body_bytes = body
        .and_then(|body| serde_json::to_vec(body).ok())
        .map_or(0, |bytes| bytes.len());
    let utf8_bytes = metadata_bytes.saturating_add(body_bytes) as u64;
    let transport_bytes = utf8_bytes;
    TokenMetrics {
        transport_bytes,
        utf8_bytes,
        serialized_tokens: estimate_tokens(utf8_bytes as usize),
        model_context_tokens: estimate_tokens(utf8_bytes as usize),
        tokenizer: request.tokenizer.clone(),
        budget: request.token_budget,
    }
}

fn estimate_tokens(bytes: usize) -> u64 {
    bytes.div_ceil(4) as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snapshots::empty_snapshot;
    use agentyc_core::{FrameId, FrameVersion, SpaceId};

    fn snapshot() -> SnapshotEnvelope {
        let space = SpaceId::from_suffix("space").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let frame = FrameId::from_suffix("main").expect("frame");
        let mut envelope = empty_snapshot(space, page);
        envelope.frame_versions.insert(frame, FrameVersion::new(1));
        envelope
    }

    fn envelope_with_elements(
        elements: Vec<agentyc_core::SnapshotElement>,
        version: u64,
        document_generation: u64,
    ) -> SnapshotEnvelope {
        let mut envelope = snapshot();
        let document = SnapshotDocument::new(SnapshotVersion::new(version), elements)
            .expect("normalized document");
        envelope.snapshot_version = document.snapshot_version;
        envelope.document_generation = Generation::new(document_generation);
        envelope.snapshot_hash = document.snapshot_hash.clone();
        envelope.result_hash = document.snapshot_hash.clone();
        envelope.delta_or_elements = SnapshotBody::Elements {
            elements: document.elements,
        };
        envelope.validate().expect("valid envelope");
        envelope
    }

    fn many_controls() -> Vec<agentyc_core::SnapshotElement> {
        (0..64)
            .map(|index| agentyc_core::SnapshotElement {
                key: ElementKey::from_suffix(format!("control-{index}")).expect("element key"),
                parent: None,
                kind: ElementKind::Control,
                text: Some(format!("Control {index}")),
                attributes: BTreeMap::from([
                    ("aria-label".to_owned(), format!("Control {index}")),
                    ("data-long".to_owned(), "x".repeat(160)),
                ]),
                order: index,
            })
            .collect()
    }

    #[test]
    fn clean_cache_returns_metadata_without_a_body_or_scan() {
        let envelope = snapshot();
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Cached,
            scan_performed: false,
        };
        let output = ContextBuilder::new()
            .build(&read, &ContextRequest::default())
            .expect("context");
        assert!(output.is_clean_cache_metadata_only());
        assert!(!output.metadata.scan_performed);
        assert!(output.metrics.utf8_bytes > 0);
    }

    #[test]
    fn delta_is_used_only_when_it_is_valid_and_cheaper() {
        let base = envelope_with_elements(many_controls(), 1, 1);
        let current_elements = match &base.delta_or_elements {
            SnapshotBody::Elements { elements } => {
                let mut elements = elements.clone();
                elements[0]
                    .attributes
                    .insert("aria-label".to_owned(), "Changed control".to_owned());
                elements
            }
            SnapshotBody::Delta { .. } | SnapshotBody::Resync { .. } => unreachable!(),
        };
        let current = envelope_with_elements(current_elements, 2, 2);
        let read = SnapshotRead {
            envelope: current,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let request = ContextRequest {
            base: Some(base),
            ..ContextRequest::default()
        };
        let output = ContextBuilder::new()
            .build(&read, &request)
            .expect("context");
        assert_eq!(output.metadata.representation, ContextRepresentation::Delta);
        assert!(matches!(output.body, Some(SnapshotBody::Delta { .. })));
    }

    #[test]
    fn invalid_delta_base_falls_back_to_resync() {
        let base = envelope_with_elements(many_controls(), 1, 1);
        let current = envelope_with_elements(many_controls(), 2, 2);
        let mut wrong_page = base.clone();
        wrong_page.page_id = PageId::from_suffix("other").expect("page");
        let read = SnapshotRead {
            envelope: current,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let request = ContextRequest {
            mode: ContextMode::Delta,
            base: Some(wrong_page),
            ..ContextRequest::default()
        };
        let output = ContextBuilder::new()
            .build(&read, &request)
            .expect("resync context");
        assert_eq!(
            output.metadata.representation,
            ContextRepresentation::Resync
        );
        assert!(output.metadata.resync_required);
    }

    #[test]
    fn oversized_context_reports_truncation_and_resync() {
        let envelope = envelope_with_elements(many_controls(), 1, 1);
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let request = ContextRequest::full().with_max_serialized_bytes(Some(1_200));
        let output = ContextBuilder::new()
            .build(&read, &request)
            .expect("bounded context");
        assert!(
            output.metadata.truncated
                || output.metadata.representation == ContextRepresentation::Resync
        );
        assert!(output.metadata.resync_required);
        assert!(output.metrics.serialized_tokens > 0);
    }

    #[test]
    fn redaction_is_applied_to_attributes() {
        let mut envelope = snapshot();
        let element = agentyc_core::SnapshotElement {
            key: ElementKey::from_suffix("control").expect("key"),
            parent: None,
            kind: ElementKind::Control,
            text: None,
            attributes: BTreeMap::from([
                ("password".to_owned(), "secret-value".to_owned()),
                ("name".to_owned(), "user".to_owned()),
            ]),
            order: 0,
        };
        let document =
            SnapshotDocument::new(SnapshotVersion::new(1), vec![element]).expect("document");
        envelope.snapshot_hash = document.snapshot_hash.clone();
        envelope.result_hash = document.snapshot_hash.clone();
        envelope.delta_or_elements = SnapshotBody::Elements {
            elements: document.elements,
        };
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let output = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest {
                    mode: ContextMode::Full,
                    ..ContextRequest::default()
                },
            )
            .expect("context");
        let json = String::from_utf8(output.to_json().expect("json")).expect("utf8");
        assert!(!json.contains("secret-value"));
        assert!(json.contains("[REDACTED]"));
    }
}
