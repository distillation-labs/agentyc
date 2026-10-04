//! Bounded context construction over validated host snapshots.
//!
//! Context construction does not scan the browser. It consumes a broker
//! [`SnapshotRead`], preserves clean-cache zero-scan metadata, and chooses a
//! representation only after deterministic coverage and serialized-cost checks.

use std::{
    cmp::Ordering,
    collections::{BTreeMap, BTreeSet},
};

use agentyc_core::{
    CacheState, ContentHash, DeltaLimits, DeltaOperation, DeltaSequence, ElementKey, ElementKind,
    FrameId, FrameVersion, Generation, PageId, RefEpoch, ResyncReason, SnapshotBody,
    SnapshotCoverage, SnapshotDelta, SnapshotDocument, SnapshotEnvelope, SnapshotMode,
    SnapshotVersion, SpaceId, TokenBudget, TopologyVersion,
};
use serde::{Deserialize, Serialize};

use crate::snapshots::{PageGeneration, SnapshotRead};

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
    /// Return the complete bounded subtree selected by [`ContextRequest::focus`].
    Focus,
    /// Return a bounded patch against the supplied base.
    Delta,
}

/// Alias used by callers that name the request mode explicitly.
pub type ContextRequestMode = ContextMode;

/// Error returned by a tokenizer implementation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenizerError {
    /// The tokenizer could not represent its count.
    CountOverflow,
    /// The tokenizer rejected the serialized input.
    InvalidInput(String),
}

impl std::fmt::Display for TokenizerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::CountOverflow => formatter.write_str("token count overflow"),
            Self::InvalidInput(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for TokenizerError {}

/// Tokenizer boundary used for all context token metrics and budgets.
///
/// A tokenizer is never inferred from a name. Callers that select a non-default
/// tokenizer must pass its implementation through [`ContextBuilder::build_with_tokenizer`].
pub trait Tokenizer: Send + Sync {
    /// Stable logical tokenizer identifier.
    fn name(&self) -> &str;
    /// Count tokens in serialized context text.
    fn count_tokens(&self, input: &str) -> Result<u64, TokenizerError>;
    /// Count tokens after the model-context boundary.
    fn count_model_context_tokens(&self, input: &str) -> Result<u64, TokenizerError> {
        self.count_tokens(input)
    }
}

/// Deterministic fallback tokenizer that counts Unicode scalar values.
///
/// This is an actual tokenizer implementation, not a byte-ratio estimate. A
/// deployed model should provide its tokenizer through [`Tokenizer`].
#[derive(Debug, Clone, Copy, Default)]
pub struct UnicodeScalarTokenizer;

impl Tokenizer for UnicodeScalarTokenizer {
    fn name(&self) -> &str {
        "unicode_scalars"
    }

    fn count_tokens(&self, input: &str) -> Result<u64, TokenizerError> {
        u64::try_from(input.chars().count()).map_err(|_| TokenizerError::CountOverflow)
    }
}

/// Logical focus dimensions used to partition context/cache results.
#[derive(Debug, Clone, Default, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ContextFocus {
    /// Exact logical frame containing focus, when supplied.
    pub frame_id: Option<FrameId>,
    /// Logical element receiving focus, when supplied.
    pub element_key: Option<ElementKey>,
}

impl ContextFocus {
    /// Return whether no logical focus target was supplied.
    pub const fn is_empty(&self) -> bool {
        self.frame_id.is_none() && self.element_key.is_none()
    }
}

/// Compatibility alias for callers that use a short focus-key name.
pub type FocusKey = ContextFocus;

/// Cache key for a context representation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextCacheKey {
    /// Logical owning space.
    pub space_id: SpaceId,
    /// Logical owning page.
    pub page_id: PageId,
    /// Exact snapshot version represented by the context read.
    #[serde(default)]
    pub snapshot_version: SnapshotVersion,
    /// Requested context mode.
    pub mode: ContextMode,
    /// Logical focus partition.
    pub focus: ContextFocus,
    /// Serialized/model token budget.
    pub budget: Option<TokenBudget>,
    /// Actual tokenizer identifier.
    pub tokenizer: Option<String>,
    /// Logical frame topology version.
    pub topology_version: TopologyVersion,
    /// Page generation partition.
    pub generation: PageGeneration,
    /// Exact frame-version vector partition.
    #[serde(default)]
    pub frame_versions: BTreeMap<FrameId, FrameVersion>,
    /// Delta base version, when the request is base-scoped.
    pub base_snapshot_version: Option<SnapshotVersion>,
    /// Delta base hash, when the request is base-scoped.
    pub base_hash: Option<ContentHash>,
}

impl ContextCacheKey {
    /// Construct a cache key from a broker read, request, and logical focus.
    pub fn from_read(read: &SnapshotRead, request: &ContextRequest, focus: ContextFocus) -> Self {
        let focus = if request.focus.is_empty() {
            focus
        } else {
            request.focus.clone()
        };
        Self::from_read_with_generation(
            read,
            request,
            focus,
            PageGeneration {
                target_generation: Generation::new(0),
                navigation_generation: read.envelope.navigation_generation,
                document_generation: read.envelope.document_generation,
            },
        )
    }

    /// Construct a cache key with an exact page generation proof.
    pub fn from_read_with_generation(
        read: &SnapshotRead,
        request: &ContextRequest,
        focus: ContextFocus,
        generation: PageGeneration,
    ) -> Self {
        let focus = if request.focus.is_empty() {
            focus
        } else {
            request.focus.clone()
        };
        Self {
            space_id: read.envelope.space_id.clone(),
            page_id: read.envelope.page_id.clone(),
            snapshot_version: read.envelope.snapshot_version,
            mode: request.mode,
            focus,
            budget: request.token_budget,
            tokenizer: request
                .tokenizer
                .clone()
                .or_else(|| Some(UnicodeScalarTokenizer.name().to_owned())),
            topology_version: read.envelope.topology_version,
            generation,
            frame_versions: read.envelope.frame_versions.clone(),
            base_snapshot_version: request.base.as_ref().map(|base| base.snapshot_version),
            base_hash: request.base.as_ref().map(|base| base.snapshot_hash.clone()),
        }
    }
}

impl Ord for ContextCacheKey {
    fn cmp(&self, other: &Self) -> Ordering {
        self.space_id
            .cmp(&other.space_id)
            .then_with(|| self.page_id.cmp(&other.page_id))
            .then_with(|| self.snapshot_version.cmp(&other.snapshot_version))
            .then_with(|| context_mode_rank(self.mode).cmp(&context_mode_rank(other.mode)))
            .then_with(|| self.focus.cmp(&other.focus))
            .then_with(|| budget_key(self.budget).cmp(&budget_key(other.budget)))
            .then_with(|| self.tokenizer.cmp(&other.tokenizer))
            .then_with(|| self.topology_version.cmp(&other.topology_version))
            .then_with(|| self.generation.cmp(&other.generation))
            .then_with(|| self.frame_versions.cmp(&other.frame_versions))
            .then_with(|| self.base_snapshot_version.cmp(&other.base_snapshot_version))
            .then_with(|| self.base_hash.cmp(&other.base_hash))
    }
}

impl PartialOrd for ContextCacheKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn context_mode_rank(mode: ContextMode) -> u8 {
    match mode {
        ContextMode::Auto => 0,
        ContextMode::Full => 1,
        ContextMode::Compact => 2,
        ContextMode::Focus => 3,
        ContextMode::Delta => 4,
    }
}

fn budget_key(budget: Option<TokenBudget>) -> (Option<u64>, Option<u64>) {
    budget.map_or((None, None), |budget| {
        (budget.serialized_limit, budget.model_context_limit)
    })
}

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
    /// Complete focused subtree.
    Focus,
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
    /// Logical frame and/or element focus for a focused context request.
    pub focus: ContextFocus,
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

    /// Construct a focused subtree representation request.
    pub fn focus() -> Self {
        Self {
            mode: ContextMode::Focus,
            ..Self::default()
        }
    }

    /// Set the logical frame and/or element focus.
    #[must_use]
    pub fn with_focus(mut self, focus: ContextFocus) -> Self {
        self.focus = focus;
        self
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

    /// Select a tokenizer identity; the matching implementation must be supplied
    /// to [`ContextBuilder::build_with_tokenizer`].
    #[must_use]
    pub fn with_tokenizer(mut self, tokenizer: impl Into<String>) -> Self {
        self.tokenizer = Some(tokenizer.into());
        self
    }

    /// Set bounded delta chain and operation limits.
    #[must_use]
    pub const fn with_delta_limits(mut self, limits: DeltaLimits) -> Self {
        self.delta_limits = limits;
        self
    }

    /// Build the compatibility cache key for this request and broker read.
    pub fn cache_key(&self, read: &SnapshotRead, focus: ContextFocus) -> ContextCacheKey {
        let focus = if self.focus.is_empty() {
            focus
        } else {
            self.focus.clone()
        };
        ContextCacheKey::from_read(read, self, focus)
    }

    /// Build a cache key with an exact page generation proof.
    pub fn cache_key_with_generation(
        &self,
        read: &SnapshotRead,
        focus: ContextFocus,
        generation: PageGeneration,
    ) -> ContextCacheKey {
        let focus = if self.focus.is_empty() {
            focus
        } else {
            self.focus.clone()
        };
        ContextCacheKey::from_read_with_generation(read, self, focus, generation)
    }
}

impl Default for ContextRequest {
    fn default() -> Self {
        Self {
            mode: ContextMode::Auto,
            base: None,
            focus: ContextFocus::default(),
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
    /// Bytes in the transport representation.
    pub transport_bytes: u64,
    /// UTF-8 bytes in serialized metadata/body.
    pub utf8_bytes: u64,
    /// Deterministic serialized token count returned by the tokenizer.
    pub serialized_tokens: u64,
    /// Model-context token count returned by the tokenizer.
    pub model_context_tokens: u64,
    /// Tokenizer identifier used for the counts.
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
        let tokenizer = UnicodeScalarTokenizer;
        self.build_with_tokenizer(read, request, &tokenizer)
    }

    /// Build context with the actual tokenizer implementation used for metrics.
    pub fn build_with_tokenizer(
        &self,
        read: &SnapshotRead,
        request: &ContextRequest,
        tokenizer: &dyn Tokenizer,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        self.build_envelope_with_tokenizer(
            &read.envelope,
            read.cache_state,
            read.scan_performed,
            request,
            tokenizer,
        )
    }

    /// Build context from an envelope using fresh-read semantics.
    pub fn build_snapshot(
        &self,
        envelope: &SnapshotEnvelope,
        request: &ContextRequest,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        let tokenizer = UnicodeScalarTokenizer;
        self.build_snapshot_with_tokenizer(envelope, request, &tokenizer)
    }

    /// Build a snapshot context with an actual tokenizer implementation.
    pub fn build_snapshot_with_tokenizer(
        &self,
        envelope: &SnapshotEnvelope,
        request: &ContextRequest,
        tokenizer: &dyn Tokenizer,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        self.build_envelope_with_tokenizer(envelope, CacheState::Fresh, true, request, tokenizer)
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
        let tokenizer = UnicodeScalarTokenizer;
        self.build_envelope_with_tokenizer(
            envelope,
            cache_state,
            scan_performed,
            request,
            &tokenizer,
        )
    }

    fn build_envelope_with_tokenizer(
        &self,
        envelope: &SnapshotEnvelope,
        cache_state: CacheState,
        scan_performed: bool,
        request: &ContextRequest,
        tokenizer: &dyn Tokenizer,
    ) -> Result<ContextOutput, agentyc_core::CoreError> {
        envelope
            .validate()
            .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
        validate_tokenizer(request, tokenizer)?;
        let focus_requested = request.mode == ContextMode::Focus || !request.focus.is_empty();
        validate_focus_request_shape(request, focus_requested)?;
        if focus_requested {
            validate_focus_provenance(envelope, cache_state)?;
        }
        if cache_state == CacheState::Cached
            && !scan_performed
            && request.clean_cache_metadata_only
            && request.mode != ContextMode::Delta
        {
            if !focus_requested {
                return self.metadata_only(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    tokenizer,
                );
            }
            if let SnapshotBody::Elements { elements } = &envelope.delta_or_elements {
                let (elements, _) = redact_elements(elements, &request.redaction);
                let _ = focus_elements(&elements, &request.focus, &envelope.frame_versions)?;
                return self.metadata_only(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    tokenizer,
                );
            }
            return Err(agentyc_core::CoreError::stale_ref(
                "focused context requires a complete retained snapshot body",
            ));
        }

        if request.mode == ContextMode::Delta {
            let Some(base) = request.base.as_ref() else {
                return if request.allow_resync {
                    self.resync_output(
                        envelope,
                        cache_state,
                        scan_performed,
                        request,
                        ResyncReason::BaseMissing,
                        tokenizer,
                    )
                } else {
                    Err(agentyc_core::CoreError::new(
                        agentyc_core::ErrorCode::EventLagged,
                        "delta context requires a retained base",
                    ))
                };
            };
            if let Some(reason) = base_resync_reason(envelope, base, request.delta_limits) {
                return if request.allow_resync {
                    self.resync_output(
                        envelope,
                        cache_state,
                        scan_performed,
                        request,
                        reason,
                        tokenizer,
                    )
                } else {
                    Err(agentyc_core::CoreError::new(
                        agentyc_core::ErrorCode::EventLagged,
                        format!("snapshot base requires resync: {reason:?}"),
                    ))
                };
            }
        }

        let current_document = match current_document(
            envelope,
            request.base.as_ref(),
            &request.redaction,
            request.delta_limits,
        ) {
            Ok(document) => document,
            Err(error) => {
                let reason = envelope.resync_reason.unwrap_or(ResyncReason::BaseMissing);
                return if request.allow_resync {
                    self.resync_output(
                        envelope,
                        cache_state,
                        scan_performed,
                        request,
                        reason,
                        tokenizer,
                    )
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
        let current_elements = if focus_requested {
            focus_elements(&current_elements, &request.focus, &envelope.frame_versions)?
        } else {
            current_elements
        };
        let compact_elements = compact_elements(&current_elements, &request.redaction);
        let full_body = SnapshotBody::Elements {
            elements: current_elements.clone(),
        };
        let compact_body = SnapshotBody::Elements {
            elements: compact_elements,
        };
        let mut delta_failure_reason = None;
        let base_document = request.base.as_ref().and_then(|base| {
            match redacted_base_document(base, &request.redaction) {
                Ok(document) => Some(document),
                Err(_) => {
                    delta_failure_reason = Some(ResyncReason::BaseMissing);
                    None
                }
            }
        });
        let delta_body = base_document.as_ref().and_then(|base| {
            if !base_matches(envelope, request.base.as_ref()?, base) {
                delta_failure_reason = Some(ResyncReason::Incoherent);
                return None;
            }
            match build_delta_body(
                envelope,
                request.base.as_ref()?,
                base,
                &current_document,
                request.delta_limits,
            ) {
                Ok(body) => Some(body),
                Err(error) => {
                    delta_failure_reason = Some(delta_failure_reason_for(&error));
                    None
                }
            }
        });

        if request.mode == ContextMode::Delta && delta_body.is_none() {
            let reason = delta_failure_reason.unwrap_or(ResyncReason::BaseMissing);
            return if request.allow_resync {
                self.resync_output(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    reason,
                    tokenizer,
                )
            } else {
                Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::EventLagged,
                    format!("delta construction requires resync: {reason:?}"),
                ))
            };
        }

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
                self.resync_output(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    reason,
                    tokenizer,
                )
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
        if !fits_budget(&metadata, body.as_ref(), request, tokenizer)?
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
        if !fits_budget(&metadata, body.as_ref(), request, tokenizer)? {
            if request.allow_resync {
                return self.resync_output(
                    envelope,
                    cache_state,
                    scan_performed,
                    request,
                    ResyncReason::CostExceeded,
                    tokenizer,
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
        let metrics = measure(&metadata, body.as_ref(), request, tokenizer)?;
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
        tokenizer: &dyn Tokenizer,
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
        if !fits_budget(&metadata, None, request, tokenizer)? {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "clean-cache context metadata exceeds its serialized budget",
            ));
        }
        let metrics = measure(&metadata, None, request, tokenizer)?;
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
        tokenizer: &dyn Tokenizer,
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
        if !fits_budget(&metadata, body.as_ref(), request, tokenizer)? {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "resynchronization context marker exceeds its serialized budget",
            ));
        }
        let metrics = measure(&metadata, body.as_ref(), request, tokenizer)?;
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

fn validate_tokenizer(
    request: &ContextRequest,
    tokenizer: &dyn Tokenizer,
) -> Result<(), agentyc_core::CoreError> {
    if request
        .tokenizer
        .as_deref()
        .is_some_and(|requested| requested != tokenizer.name())
    {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "requested tokenizer {:?} does not match supplied tokenizer {:?}",
            request.tokenizer,
            tokenizer.name()
        )));
    }
    Ok(())
}

fn validate_focus_request_shape(
    request: &ContextRequest,
    focus_requested: bool,
) -> Result<(), agentyc_core::CoreError> {
    if request.mode == ContextMode::Focus && request.focus.is_empty() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "focus context requires a frame_id or element_key target",
        ));
    }
    if focus_requested && request.focus.is_empty() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "focused context requires a non-empty logical target",
        ));
    }
    Ok(())
}

fn validate_focus_provenance(
    envelope: &SnapshotEnvelope,
    cache_state: CacheState,
) -> Result<(), agentyc_core::CoreError> {
    if matches!(cache_state, CacheState::Stale | CacheState::Invalidated)
        || matches!(
            envelope.cache_state,
            CacheState::Stale | CacheState::Invalidated
        )
    {
        return Err(agentyc_core::CoreError::stale_ref(
            "focused context requires a current snapshot cache entry",
        ));
    }
    if !envelope.coherent
        || envelope.truncated
        || envelope.resync_required
        || envelope.coverage != SnapshotCoverage::Complete
    {
        return Err(agentyc_core::CoreError::stale_ref(
            "focused context requires a complete coherent snapshot",
        ));
    }
    Ok(())
}

fn focus_elements(
    elements: &[agentyc_core::SnapshotElement],
    focus: &ContextFocus,
    frame_versions: &BTreeMap<FrameId, FrameVersion>,
) -> Result<Vec<agentyc_core::SnapshotElement>, agentyc_core::CoreError> {
    if focus.is_empty() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "focused context requires a logical target",
        ));
    }
    let by_key: BTreeMap<&ElementKey, &agentyc_core::SnapshotElement> = elements
        .iter()
        .map(|element| (&element.key, element))
        .collect();
    if let Some(frame_id) = &focus.frame_id
        && !frame_versions.contains_key(frame_id)
    {
        return Err(agentyc_core::CoreError::stale_ref(format!(
            "focused logical frame {frame_id} is not present in the current snapshot"
        )));
    }
    let frame_scope = focus
        .frame_id
        .as_ref()
        .map(|frame_id| frame_scope_keys(elements, &by_key, frame_id, frame_versions.len()))
        .transpose()?;

    let keys = if let Some(element_key) = &focus.element_key {
        if !by_key.contains_key(element_key) {
            return Err(agentyc_core::CoreError::stale_ref(format!(
                "focused element {element_key} is not present in the current snapshot"
            )));
        }
        if let Some(frame_scope) = &frame_scope
            && !frame_scope.contains(element_key)
        {
            return Err(agentyc_core::CoreError::stale_ref(
                "focused element is not in the requested logical frame",
            ));
        }
        subtree_keys(elements, &by_key, element_key)?
    } else {
        frame_scope.ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument(
                "focused context requires a frame_id or element_key target",
            )
        })?
    };

    Ok(elements
        .iter()
        .filter(|element| keys.contains(&element.key))
        .cloned()
        .collect())
}

fn frame_scope_keys<'a>(
    elements: &'a [agentyc_core::SnapshotElement],
    by_key: &BTreeMap<&'a ElementKey, &'a agentyc_core::SnapshotElement>,
    frame_id: &FrameId,
    frame_count: usize,
) -> Result<BTreeSet<ElementKey>, agentyc_core::CoreError> {
    if frame_count == 0 {
        return Err(agentyc_core::CoreError::stale_ref(
            "focused logical frame is absent from the snapshot provenance",
        ));
    }
    let has_frame_marker = elements.iter().any(|element| {
        matches!(element.kind, ElementKind::Frame)
            && (element.attributes.contains_key("frame_id")
                || element.attributes.contains_key("logical_frame_id"))
    });
    let markers: Vec<&agentyc_core::SnapshotElement> = elements
        .iter()
        .filter(|element| {
            matches!(element.kind, ElementKind::Frame)
                && ["frame_id", "logical_frame_id"].iter().any(|name| {
                    element
                        .attributes
                        .get(*name)
                        .is_some_and(|value| value == frame_id.as_str())
                })
        })
        .collect();
    match markers.as_slice() {
        [marker] => subtree_keys(elements, by_key, &marker.key),
        [] if has_frame_marker => Err(agentyc_core::CoreError::stale_ref(
            "focused logical frame has no current snapshot mapping",
        )),
        [] if frame_count == 1 => {
            for element in elements {
                ancestor_keys(by_key, &element.key)?;
            }
            Ok(elements.iter().map(|element| element.key.clone()).collect())
        }
        [] => Err(agentyc_core::CoreError::stale_ref(
            "focused logical frame cannot be mapped to snapshot elements",
        )),
        _ => Err(agentyc_core::CoreError::stale_ref(
            "focused logical frame mapping is ambiguous",
        )),
    }
}

fn subtree_keys(
    elements: &[agentyc_core::SnapshotElement],
    by_key: &BTreeMap<&ElementKey, &agentyc_core::SnapshotElement>,
    target: &ElementKey,
) -> Result<BTreeSet<ElementKey>, agentyc_core::CoreError> {
    let mut keys = ancestor_keys(by_key, target)?;
    for element in elements {
        if is_descendant_of(by_key, &element.key, target)? {
            keys.insert(element.key.clone());
        }
    }
    Ok(keys)
}

fn ancestor_keys(
    by_key: &BTreeMap<&ElementKey, &agentyc_core::SnapshotElement>,
    target: &ElementKey,
) -> Result<BTreeSet<ElementKey>, agentyc_core::CoreError> {
    let mut keys = BTreeSet::new();
    let mut current = Some(target);
    while let Some(key) = current {
        if !keys.insert(key.clone()) {
            return Err(agentyc_core::CoreError::stale_ref(
                "focused snapshot contains a cyclic ancestor chain",
            ));
        }
        let element = by_key.get(key).ok_or_else(|| {
            agentyc_core::CoreError::stale_ref("focused snapshot contains a missing ancestor")
        })?;
        current = element.parent.as_ref();
    }
    Ok(keys)
}

fn is_descendant_of(
    by_key: &BTreeMap<&ElementKey, &agentyc_core::SnapshotElement>,
    element_key: &ElementKey,
    target: &ElementKey,
) -> Result<bool, agentyc_core::CoreError> {
    if element_key == target {
        return Ok(true);
    }
    let Some(element) = by_key.get(element_key) else {
        return Ok(false);
    };
    let mut visited = BTreeSet::new();
    let mut current = element.parent.as_ref();
    while let Some(key) = current {
        if key == target {
            return Ok(true);
        }
        if !visited.insert(key.clone()) {
            return Err(agentyc_core::CoreError::stale_ref(
                "focused snapshot contains a cyclic parent chain",
            ));
        }
        let Some(parent) = by_key.get(key) else {
            return Ok(false);
        };
        current = parent.parent.as_ref();
    }
    Ok(false)
}

fn base_resync_reason(
    current: &SnapshotEnvelope,
    base: &SnapshotEnvelope,
    limits: DeltaLimits,
) -> Option<ResyncReason> {
    if current.space_id != base.space_id || current.page_id != base.page_id {
        return Some(ResyncReason::BaseMissing);
    }
    if base.validate().is_err()
        || matches!(
            base.cache_state,
            CacheState::Stale | CacheState::Invalidated
        )
        || base.resync_required
        || base.truncated
        || !base.coherent
        || base.coverage != SnapshotCoverage::Complete
    {
        return Some(base.resync_reason.unwrap_or(ResyncReason::Incoherent));
    }
    if !matches!(base.mode, SnapshotMode::Full) {
        return Some(if matches!(base.mode, SnapshotMode::Delta) {
            ResyncReason::UnsupportedDelta
        } else {
            ResyncReason::Incoherent
        });
    }
    if current.navigation_generation != base.navigation_generation
        || current.document_generation != base.document_generation
    {
        return Some(ResyncReason::NavigationChanged);
    }
    if current.topology_version != base.topology_version
        || current.frame_versions != base.frame_versions
    {
        return Some(ResyncReason::Incoherent);
    }
    if current.resync_required || current.truncated || !current.coherent {
        return Some(current.resync_reason.unwrap_or(ResyncReason::Incoherent));
    }
    if current.snapshot_version <= base.snapshot_version {
        return Some(ResyncReason::BaseMissing);
    }
    let version_gap = current
        .snapshot_version
        .get()
        .saturating_sub(base.snapshot_version.get());
    if version_gap > u64::from(limits.max_chain_depth) {
        return Some(ResyncReason::ChainTooLong);
    }
    if limits.max_chain_depth == 0
        || matches!(&current.delta_or_elements, SnapshotBody::Delta { delta } if delta.chain_depth >= limits.max_chain_depth)
    {
        return Some(ResyncReason::ChainTooLong);
    }
    None
}

fn delta_failure_reason_for(error: &agentyc_core::CoreError) -> ResyncReason {
    match error.code {
        agentyc_core::ErrorCode::MessageTooLarge => ResyncReason::ChainTooLong,
        agentyc_core::ErrorCode::StaleRef => ResyncReason::BaseHashMismatch,
        _ => ResyncReason::Incoherent,
    }
}

fn current_document(
    envelope: &SnapshotEnvelope,
    base: Option<&SnapshotEnvelope>,
    redaction: &RedactionPolicy,
    limits: DeltaLimits,
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
                .apply(&base_document, limits)
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
        && base_envelope.document_generation == current.document_generation
        && base_envelope.topology_version == current.topology_version
        && base_envelope.frame_versions == current.frame_versions
        && base_envelope.snapshot_version < current.snapshot_version
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
        ContextMode::Focus => candidates
            .iter()
            .find(|(representation, _, _)| *representation == ContextRepresentation::Full)
            .map(|(_, body, _)| {
                (
                    ContextRepresentation::Focus,
                    body.clone(),
                    envelope.coverage,
                    false,
                )
            }),
        ContextMode::Delta => {
            if !delta_available {
                None
            } else {
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
        ContextRepresentation::Focus => 2,
        ContextRepresentation::Full => 3,
        ContextRepresentation::Metadata | ContextRepresentation::Resync => 4,
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

fn serialized_context(
    metadata: &ContextMetadata,
    body: Option<&SnapshotBody>,
) -> Result<(u64, String), agentyc_core::CoreError> {
    let metadata = serde_json::to_string(metadata)
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let body = body
        .map(serde_json::to_string)
        .transpose()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let utf8_bytes = metadata
        .len()
        .saturating_add(body.as_ref().map_or(0, String::len)) as u64;
    let mut serialized = metadata;
    if let Some(body) = body {
        serialized.push_str(&body);
    }
    Ok((utf8_bytes, serialized))
}

fn tokenizer_count(result: Result<u64, TokenizerError>) -> Result<u64, agentyc_core::CoreError> {
    result.map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))
}

fn fits_budget(
    metadata: &ContextMetadata,
    body: Option<&SnapshotBody>,
    request: &ContextRequest,
    tokenizer: &dyn Tokenizer,
) -> Result<bool, agentyc_core::CoreError> {
    let metrics = measure(metadata, body, request, tokenizer)?;
    if request
        .max_serialized_bytes
        .is_some_and(|limit| metrics.utf8_bytes > limit as u64)
    {
        return Ok(false);
    }
    if request.token_budget.is_some_and(|budget| {
        budget
            .serialized_limit
            .is_some_and(|limit| metrics.serialized_tokens > limit)
    }) || request.token_budget.is_some_and(|budget| {
        budget
            .model_context_limit
            .is_some_and(|limit| metrics.model_context_tokens > limit)
    }) {
        return Ok(false);
    }
    Ok(true)
}

fn measure(
    metadata: &ContextMetadata,
    body: Option<&SnapshotBody>,
    request: &ContextRequest,
    tokenizer: &dyn Tokenizer,
) -> Result<TokenMetrics, agentyc_core::CoreError> {
    let (utf8_bytes, serialized) = serialized_context(metadata, body)?;
    let serialized_tokens = tokenizer_count(tokenizer.count_tokens(&serialized))?;
    let model_context_tokens = tokenizer_count(tokenizer.count_model_context_tokens(&serialized))?;
    Ok(TokenMetrics {
        transport_bytes: utf8_bytes,
        utf8_bytes,
        serialized_tokens,
        model_context_tokens,
        tokenizer: Some(tokenizer.name().to_owned()),
        budget: request.token_budget,
    })
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

    fn focus_tree_envelope() -> SnapshotEnvelope {
        envelope_with_elements(
            vec![
                agentyc_core::SnapshotElement {
                    key: ElementKey::from_suffix("root").expect("root"),
                    parent: None,
                    kind: ElementKind::Root,
                    text: None,
                    attributes: BTreeMap::new(),
                    order: 0,
                },
                agentyc_core::SnapshotElement {
                    key: ElementKey::from_suffix("section").expect("section"),
                    parent: Some(ElementKey::from_suffix("root").expect("root")),
                    kind: ElementKind::Element,
                    text: Some("Section".to_owned()),
                    attributes: BTreeMap::from([("role".to_owned(), "region".to_owned())]),
                    order: 1,
                },
                agentyc_core::SnapshotElement {
                    key: ElementKey::from_suffix("target").expect("target"),
                    parent: Some(ElementKey::from_suffix("section").expect("section")),
                    kind: ElementKind::Element,
                    text: Some("Focused target".to_owned()),
                    attributes: BTreeMap::from([("aria-label".to_owned(), "target".to_owned())]),
                    order: 2,
                },
                agentyc_core::SnapshotElement {
                    key: ElementKey::from_suffix("target-child").expect("target child"),
                    parent: Some(ElementKey::from_suffix("target").expect("target")),
                    kind: ElementKind::Control,
                    text: Some("Focused control".to_owned()),
                    attributes: BTreeMap::from([("name".to_owned(), "child".to_owned())]),
                    order: 3,
                },
                agentyc_core::SnapshotElement {
                    key: ElementKey::from_suffix("sibling").expect("sibling"),
                    parent: Some(ElementKey::from_suffix("section").expect("section")),
                    kind: ElementKind::Control,
                    text: Some("Sibling".to_owned()),
                    attributes: BTreeMap::new(),
                    order: 4,
                },
            ],
            1,
            1,
        )
    }

    fn body_keys(output: &ContextOutput) -> Vec<String> {
        match output.body.as_ref() {
            Some(SnapshotBody::Elements { elements }) => elements
                .iter()
                .map(|element| element.key.as_str().to_owned())
                .collect(),
            _ => Vec::new(),
        }
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
        let current = envelope_with_elements(current_elements, 2, 1);
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
    fn document_generation_change_requires_resync() {
        let base = envelope_with_elements(many_controls(), 1, 1);
        let current = envelope_with_elements(many_controls(), 2, 2);
        let read = SnapshotRead {
            envelope: current,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let request = ContextRequest {
            mode: ContextMode::Delta,
            base: Some(base),
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

    #[derive(Debug)]
    struct FixedTokenizer;

    impl Tokenizer for FixedTokenizer {
        fn name(&self) -> &str {
            "fixed"
        }

        fn count_tokens(&self, _input: &str) -> Result<u64, TokenizerError> {
            Ok(7)
        }

        fn count_model_context_tokens(&self, _input: &str) -> Result<u64, TokenizerError> {
            Ok(11)
        }
    }

    #[test]
    fn tokenizer_interface_controls_metrics_and_rejects_mismatched_names() {
        let envelope = snapshot();
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let tokenizer = FixedTokenizer;
        let request = ContextRequest::full().with_tokenizer("fixed");
        let output = ContextBuilder::new()
            .build_with_tokenizer(&read, &request, &tokenizer)
            .expect("context");
        assert_eq!(output.metrics.serialized_tokens, 7);
        assert_eq!(output.metrics.model_context_tokens, 11);
        assert_eq!(output.metrics.tokenizer.as_deref(), Some("fixed"));

        let mismatch = ContextBuilder::new().build_with_tokenizer(
            &read,
            &ContextRequest::full().with_tokenizer("other"),
            &tokenizer,
        );
        assert_eq!(
            mismatch.expect_err("mismatched tokenizer").code,
            agentyc_core::ErrorCode::InvalidArgument
        );
    }

    #[test]
    fn delta_mode_resyncs_for_missing_stale_or_lagged_bases() {
        let base = envelope_with_elements(many_controls(), 1, 1);
        let current = envelope_with_elements(many_controls(), 100, 1);
        let read = SnapshotRead {
            envelope: current.clone(),
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };

        let missing = ContextBuilder::new()
            .build(&read, &ContextRequest::delta(base.clone()).with_base(None))
            .expect("missing-base resync");
        assert_eq!(
            missing.metadata.representation,
            ContextRepresentation::Resync
        );
        assert_eq!(
            missing.metadata.resync_reason,
            Some(ResyncReason::BaseMissing)
        );

        let mut stale_base = base.clone();
        stale_base.cache_state = CacheState::Invalidated;
        let stale = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::delta(stale_base).with_delta_limits(DeltaLimits {
                    max_operations: 128,
                    max_chain_depth: 128,
                }),
            )
            .expect("stale-base resync");
        assert_eq!(stale.metadata.representation, ContextRepresentation::Resync);

        let lagged = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::delta(base).with_delta_limits(DeltaLimits {
                    max_operations: 128,
                    max_chain_depth: 8,
                }),
            )
            .expect("lagged-base resync");
        assert_eq!(
            lagged.metadata.representation,
            ContextRepresentation::Resync
        );
        assert_eq!(
            lagged.metadata.resync_reason,
            Some(ResyncReason::ChainTooLong)
        );
    }

    #[test]
    fn delta_mode_resyncs_when_frame_topology_or_operation_bounds_fail() {
        let base = envelope_with_elements(many_controls(), 1, 1);
        let mut topology_changed = envelope_with_elements(many_controls(), 2, 1);
        topology_changed.topology_version = TopologyVersion::new(2);
        let topology_read = SnapshotRead {
            envelope: topology_changed,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let topology = ContextBuilder::new()
            .build(&topology_read, &ContextRequest::delta(base.clone()))
            .expect("topology resync");
        assert_eq!(
            topology.metadata.representation,
            ContextRepresentation::Resync
        );
        assert_eq!(
            topology.metadata.resync_reason,
            Some(ResyncReason::Incoherent)
        );

        let mut changed_elements = many_controls();
        changed_elements[0]
            .attributes
            .insert("aria-label".to_owned(), "changed".to_owned());
        let current = envelope_with_elements(changed_elements, 2, 1);
        let operation_limited = ContextBuilder::new()
            .build(
                &SnapshotRead {
                    envelope: current,
                    cache_state: CacheState::Fresh,
                    scan_performed: true,
                },
                &ContextRequest::delta(base).with_delta_limits(DeltaLimits {
                    max_operations: 0,
                    max_chain_depth: 8,
                }),
            )
            .expect("operation-bound resync");
        assert_eq!(
            operation_limited.metadata.representation,
            ContextRepresentation::Resync
        );
        assert!(operation_limited.metadata.resync_required);
    }

    #[test]
    fn focus_returns_a_full_subtree_distinct_from_compact() {
        let envelope = focus_tree_envelope();
        let read = SnapshotRead {
            envelope: envelope.clone(),
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let compact = ContextBuilder::new()
            .build(&read, &ContextRequest::compact())
            .expect("compact context");
        let focused = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: None,
                    element_key: Some(ElementKey::from_suffix("target").expect("target")),
                }),
            )
            .expect("focused context");

        assert_eq!(
            focused.metadata.representation,
            ContextRepresentation::Focus
        );
        assert_eq!(
            body_keys(&focused),
            vec![
                "element_root",
                "element_section",
                "element_target",
                "element_target-child",
            ]
        );
        assert!(
            !body_keys(&focused)
                .iter()
                .any(|key| key == "element_sibling")
        );
        assert_ne!(focused.body, compact.body);
        let Some(SnapshotBody::Elements { elements }) = focused.body.as_ref() else {
            panic!("focused context must contain an element body");
        };
        let target = elements
            .iter()
            .find(|element| element.key.as_str() == "element_target")
            .expect("focused target");
        assert_eq!(target.text.as_deref(), Some("Focused target"));
        assert_eq!(
            target.parent.as_ref().map(ElementKey::as_str),
            Some("element_section")
        );
        envelope
            .validate()
            .expect("original envelope remains valid");
        assert_eq!(focused.metadata.snapshot_hash, envelope.snapshot_hash);
    }

    #[test]
    fn focus_accepts_a_proven_frame_and_rejects_unmapped_multi_frame_content() {
        let envelope = focus_tree_envelope();
        let read = SnapshotRead {
            envelope: envelope.clone(),
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let focused = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: Some(FrameId::from_suffix("main").expect("main frame")),
                    element_key: None,
                }),
            )
            .expect("single-frame focus");
        assert_eq!(body_keys(&focused).len(), 5);

        let mut multi_frame = envelope;
        multi_frame.frame_versions.insert(
            FrameId::from_suffix("child").expect("child frame"),
            FrameVersion::new(1),
        );
        let error = ContextBuilder::new()
            .build(
                &SnapshotRead {
                    envelope: multi_frame,
                    cache_state: CacheState::Fresh,
                    scan_performed: true,
                },
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: Some(FrameId::from_suffix("child").expect("child frame")),
                    element_key: None,
                }),
            )
            .expect_err("unmapped frame focus must fail closed");
        assert_eq!(error.code, agentyc_core::ErrorCode::StaleRef);
    }

    #[test]
    fn focus_rejects_missing_unknown_and_stale_targets() {
        let envelope = focus_tree_envelope();
        let read = SnapshotRead {
            envelope: envelope.clone(),
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let missing = ContextBuilder::new()
            .build(&read, &ContextRequest::focus())
            .expect_err("missing focus must fail");
        assert_eq!(missing.code, agentyc_core::ErrorCode::InvalidArgument);

        let unknown = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: None,
                    element_key: Some(ElementKey::from_suffix("missing").expect("missing")),
                }),
            )
            .expect_err("unknown element must fail");
        assert_eq!(unknown.code, agentyc_core::ErrorCode::StaleRef);

        let mut partial = envelope.clone();
        partial.coherent = false;
        partial.coverage = SnapshotCoverage::Partial;
        let partial_error = ContextBuilder::new()
            .build(
                &SnapshotRead {
                    envelope: partial,
                    cache_state: CacheState::Fresh,
                    scan_performed: true,
                },
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: None,
                    element_key: Some(ElementKey::from_suffix("target").expect("target")),
                }),
            )
            .expect_err("partial focused context must fail closed");
        assert_eq!(partial_error.code, agentyc_core::ErrorCode::StaleRef);

        let stale_error = ContextBuilder::new()
            .build(
                &SnapshotRead {
                    envelope,
                    cache_state: CacheState::Stale,
                    scan_performed: false,
                },
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: None,
                    element_key: Some(ElementKey::from_suffix("target").expect("target")),
                }),
            )
            .expect_err("stale focused context must fail closed");
        assert_eq!(stale_error.code, agentyc_core::ErrorCode::StaleRef);
    }

    #[test]
    fn focus_cache_keys_partition_mode_and_logical_target() {
        let envelope = focus_tree_envelope();
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Fresh,
            scan_performed: true,
        };
        let target = ContextFocus {
            frame_id: Some(FrameId::from_suffix("main").expect("main frame")),
            element_key: Some(ElementKey::from_suffix("target").expect("target")),
        };
        let focused = ContextRequest::focus().with_focus(target.clone());
        let focused_key = focused.cache_key(&read, ContextFocus::default());
        assert_eq!(focused_key.focus, target);
        assert_eq!(focused_key.mode, ContextMode::Focus);

        let compact_key = ContextRequest::compact().cache_key(&read, ContextFocus::default());
        assert_ne!(focused_key, compact_key);

        let other_key = ContextRequest::focus()
            .with_focus(ContextFocus {
                frame_id: target.frame_id.clone(),
                element_key: Some(ElementKey::from_suffix("section").expect("section")),
            })
            .cache_key(&read, ContextFocus::default());
        assert_ne!(focused_key, other_key);

        let fallback_key = ContextRequest::focus().cache_key(&read, target.clone());
        assert_eq!(fallback_key.focus, target);
    }

    #[test]
    fn clean_focused_cache_returns_metadata_without_a_body() {
        let mut envelope = focus_tree_envelope();
        envelope.cache_state = CacheState::Cached;
        let read = SnapshotRead {
            envelope,
            cache_state: CacheState::Cached,
            scan_performed: false,
        };
        let output = ContextBuilder::new()
            .build(
                &read,
                &ContextRequest::focus().with_focus(ContextFocus {
                    frame_id: None,
                    element_key: Some(ElementKey::from_suffix("target").expect("target")),
                }),
            )
            .expect("clean focused context");
        assert!(output.is_clean_cache_metadata_only());
        assert_eq!(
            output.metadata.representation,
            ContextRepresentation::Metadata
        );
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
