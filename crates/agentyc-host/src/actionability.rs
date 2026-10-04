//! Shared fail-closed actionability and postcondition checks.
//!
//! The host can only approve an element operation when every required fact is
//! explicitly proven. Unknown connectedness, visibility, overlay, movement, or
//! ownership evidence is never treated as safe.

use std::collections::BTreeMap;

use agentyc_core::{
    ActionOperation, ActionReceipt, ActionRequest, ContentHash, CoreError, ElementRef, ErrorCode,
    FrameId, Generation, PageDescriptor, Postcondition, SnapshotProvenance,
};
use serde::{Deserialize, Serialize};

use crate::refs::RefRegistry;

/// Logical operation class used by shared actionability checks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionKind {
    /// Activate a control with a pointer-like operation.
    Click,
    /// Insert bounded text into a control.
    Input,
    /// Select or activate a control without a pointer hit test.
    Select,
    /// Move the page viewport.
    Scroll,
    /// Focus a control.
    Focus,
    /// Navigate without an element target.
    Navigate,
    /// Evaluate a policy-approved operation.
    Evaluate,
    /// Close a logical page.
    Close,
}

/// Return whether an operation necessarily targets a logical element.
///
/// Page-level operations remain valid without an element proof. Element
/// mutations must carry a complete ref/provenance/actionability envelope so
/// the host can revalidate the exact target immediately before dispatch.
pub const fn requires_element_actionability(operation: ActionOperation) -> bool {
    matches!(
        operation,
        ActionOperation::Click | ActionOperation::Input | ActionOperation::Upload
    )
}

impl From<ActionOperation> for ActionKind {
    fn from(operation: ActionOperation) -> Self {
        match operation {
            ActionOperation::Click => Self::Click,
            ActionOperation::Input => Self::Input,
            ActionOperation::Scroll => Self::Scroll,
            ActionOperation::Navigate => Self::Navigate,
            ActionOperation::Evaluate => Self::Evaluate,
            ActionOperation::Close => Self::Close,
            ActionOperation::Wait | ActionOperation::Screenshot => Self::Evaluate,
            ActionOperation::StorageWrite
            | ActionOperation::CookieWrite
            | ActionOperation::Upload => Self::Evaluate,
        }
    }
}

/// Explicit DOM/layout/ownership facts supplied by the bridge boundary.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionabilityEvidence {
    /// Whether the logical node is still connected to its frame document.
    pub connected: Option<bool>,
    /// Whether the node is visible under the bridge's visibility policy.
    pub visible: Option<bool>,
    /// Whether the node is disabled.
    pub disabled: Option<bool>,
    /// Whether the node is readonly.
    pub readonly: Option<bool>,
    /// Whether a different node covers the intended hit point.
    pub covered: Option<bool>,
    /// Whether an overlay is present over the intended hit point.
    pub overlay_present: Option<bool>,
    /// Whether the intended node is the actual hit target.
    pub hit_target: Option<bool>,
    /// Whether layout is moving or unstable.
    pub moving: Option<bool>,
    /// Whether the node is outside the usable viewport.
    pub offscreen: Option<bool>,
    /// Whether user control currently fences this operation.
    pub user_control: Option<bool>,
    /// Target generation proven by the bridge, when available.
    pub target_generation: Option<Generation>,
    /// Page navigation generation proven by the bridge, when available.
    pub navigation_generation: Option<Generation>,
    /// Page document generation proven by the bridge, when available.
    pub document_generation: Option<Generation>,
    /// Snapshot hash used to produce the evidence, when available.
    pub snapshot_hash: Option<ContentHash>,
}

impl ActionabilityEvidence {
    /// Return an intentionally incomplete/unknown evidence value.
    pub fn unknown() -> Self {
        Self::default()
    }

    /// Construct complete positive evidence for tests and trusted bridge seams.
    pub fn proven_interactive() -> Self {
        Self {
            connected: Some(true),
            visible: Some(true),
            disabled: Some(false),
            readonly: Some(false),
            covered: Some(false),
            overlay_present: Some(false),
            hit_target: Some(true),
            moving: Some(false),
            offscreen: Some(false),
            user_control: Some(false),
            ..Self::default()
        }
    }

    /// Set connectedness evidence.
    #[must_use]
    pub const fn with_connected(mut self, value: bool) -> Self {
        self.connected = Some(value);
        self
    }

    /// Set visibility evidence.
    #[must_use]
    pub const fn with_visible(mut self, value: bool) -> Self {
        self.visible = Some(value);
        self
    }

    /// Set disabled evidence.
    #[must_use]
    pub const fn with_disabled(mut self, value: bool) -> Self {
        self.disabled = Some(value);
        self
    }

    /// Set readonly evidence.
    #[must_use]
    pub const fn with_readonly(mut self, value: bool) -> Self {
        self.readonly = Some(value);
        self
    }

    /// Set overlay/coverage evidence.
    #[must_use]
    pub const fn with_covered(mut self, value: bool) -> Self {
        self.covered = Some(value);
        self
    }

    /// Set overlay evidence.
    #[must_use]
    pub const fn with_overlay_present(mut self, value: bool) -> Self {
        self.overlay_present = Some(value);
        self
    }

    /// Alias for [`Self::with_overlay_present`].
    #[must_use]
    pub const fn with_overlay(self, value: bool) -> Self {
        self.with_overlay_present(value)
    }

    /// Set hit-target evidence.
    #[must_use]
    pub const fn with_hit_target(mut self, value: bool) -> Self {
        self.hit_target = Some(value);
        self
    }

    /// Set layout movement evidence.
    #[must_use]
    pub const fn with_moving(mut self, value: bool) -> Self {
        self.moving = Some(value);
        self
    }

    /// Set viewport evidence.
    #[must_use]
    pub const fn with_offscreen(mut self, value: bool) -> Self {
        self.offscreen = Some(value);
        self
    }

    /// Set user-control ownership evidence.
    #[must_use]
    pub const fn with_user_control(mut self, value: bool) -> Self {
        self.user_control = Some(value);
        self
    }

    /// Set the managed target generation used to compute this evidence.
    #[must_use]
    pub const fn with_target_generation(mut self, generation: Generation) -> Self {
        self.target_generation = Some(generation);
        self
    }

    /// Attach the generations used to compute this evidence.
    #[must_use]
    pub fn with_generations(
        mut self,
        target_generation: Option<Generation>,
        navigation_generation: Generation,
        document_generation: Generation,
        snapshot_hash: Option<ContentHash>,
    ) -> Self {
        self.target_generation = target_generation;
        self.navigation_generation = Some(navigation_generation);
        self.document_generation = Some(document_generation);
        self.snapshot_hash = snapshot_hash;
        self
    }
}

/// Payload keys that carry the host-validated, typed actionability boundary.
///
/// Action requests currently use a bounded string map for transport compatibility.
/// These values are therefore JSON objects encoded as strings and are parsed once
/// at the host boundary; they are included in the canonical request hash.
pub const ELEMENT_REF_PAYLOAD_KEY: &str = "element_ref";
/// Alias accepted for callers that use the shorter ref spelling.
pub const REF_PAYLOAD_KEY: &str = "ref";
/// Snapshot provenance payload key.
pub const PROVENANCE_PAYLOAD_KEY: &str = "provenance";
/// Actionability evidence payload key.
pub const ACTIONABILITY_PAYLOAD_KEY: &str = "actionability_evidence";
/// Alias accepted for concise action payloads.
pub const EVIDENCE_PAYLOAD_KEY: &str = "evidence";
/// Logical frame scope payload key.
pub const FRAME_SCOPE_PAYLOAD_KEY: &str = "frame_scope";

/// Typed actionability input decoded from a bounded action payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionabilityInput {
    /// The exact ref issued from a complete snapshot.
    pub element_ref: ElementRef,
    /// The complete snapshot provenance used to issue the ref.
    pub provenance: SnapshotProvenance,
    /// The logical frame selected for dispatch.
    pub frame_id: FrameId,
    /// DOM/layout/ownership facts observed for this exact target.
    pub evidence: ActionabilityEvidence,
}

impl ActionabilityInput {
    /// Decode an optional typed actionability envelope from the string payload.
    ///
    /// An absent envelope preserves support for page-level actions and for legacy
    /// coordinate actions. Once any actionability field is supplied, all fields
    /// become mandatory and are validated fail-closed.
    pub fn from_payload(payload: &BTreeMap<String, String>) -> Result<Option<Self>, CoreError> {
        let has_input = [
            ELEMENT_REF_PAYLOAD_KEY,
            REF_PAYLOAD_KEY,
            PROVENANCE_PAYLOAD_KEY,
            ACTIONABILITY_PAYLOAD_KEY,
            EVIDENCE_PAYLOAD_KEY,
            FRAME_SCOPE_PAYLOAD_KEY,
        ]
        .into_iter()
        .any(|key| payload.contains_key(key));
        if !has_input {
            return Ok(None);
        }

        let element_ref = parse_payload_json::<ElementRef>(payload, ELEMENT_REF_PAYLOAD_KEY)
            .or_else(|_| parse_payload_json::<ElementRef>(payload, REF_PAYLOAD_KEY))?;
        let provenance = parse_payload_json::<SnapshotProvenance>(payload, PROVENANCE_PAYLOAD_KEY)?;
        let evidence =
            parse_payload_json::<ActionabilityEvidence>(payload, ACTIONABILITY_PAYLOAD_KEY)
                .or_else(|_| {
                    parse_payload_json::<ActionabilityEvidence>(payload, EVIDENCE_PAYLOAD_KEY)
                })?;
        let frame_id = payload
            .get(FRAME_SCOPE_PAYLOAD_KEY)
            .map(|value| value.parse::<FrameId>())
            .transpose()
            .map_err(|error| CoreError::stale_ref(format!("invalid logical frame scope: {error}")))?
            .unwrap_or_else(|| element_ref.frame_id.clone());
        if element_ref.frame_id != frame_id {
            return Err(CoreError::stale_ref(
                "action ref frame does not match the requested logical frame scope",
            ));
        }
        Ok(Some(Self {
            element_ref,
            provenance,
            frame_id,
            evidence,
        }))
    }

    /// Validate the typed envelope against the current durable page state.
    pub fn validate_for_page(
        &self,
        operation: ActionOperation,
        page: &PageDescriptor,
        current_snapshot_hash: Option<&ContentHash>,
    ) -> Result<ActionabilityProof, CoreError> {
        if self.element_ref.space_id != page.space_id || self.provenance.space_id != page.space_id {
            return Err(CoreError::stale_ref(
                "action ref provenance does not match the current logical space",
            ));
        }
        if self.element_ref.page_id != page.page_id || self.provenance.page_id != page.page_id {
            return Err(CoreError::stale_ref(
                "action ref provenance does not match the current logical page",
            ));
        }
        if self.provenance.navigation_generation != page.navigation_generation
            || self.provenance.document_generation != page.document_generation
            || self.element_ref.navigation_generation != page.navigation_generation
            || self.element_ref.document_generation != page.document_generation
        {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "action ref is bound to an older page generation",
            ));
        }
        let Some(current_snapshot_hash) = current_snapshot_hash else {
            return Err(CoreError::stale_ref(
                "current snapshot provenance is unavailable for action dispatch",
            ));
        };
        if current_snapshot_hash != &self.provenance.snapshot_hash {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "action ref is bound to an older snapshot",
            ));
        }
        if self.evidence.target_generation != Some(page.target_generation) {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "actionability evidence is not bound to the current target generation",
            ));
        }
        ActionabilityChecker::check_with_target_generation(
            operation.into(),
            &self.element_ref,
            &self.frame_id,
            &self.provenance,
            page.target_generation,
            &self.evidence,
        )
    }
}

/// Validate the typed actionability envelope in one action request.
///
/// Element mutations fail closed when the proof is absent. This function is
/// intentionally the shared boundary used by enqueue, dequeue, and the final
/// pre-dispatch gate; page-level operations may omit the envelope.
pub fn validate_request_actionability(
    request: &ActionRequest<BTreeMap<String, String>>,
    page: &PageDescriptor,
    current_snapshot_hash: Option<&ContentHash>,
) -> Result<(), CoreError> {
    let input = ActionabilityInput::from_payload(&request.payload)?;
    if requires_element_actionability(request.operation) && input.is_none() {
        return Err(CoreError::invalid_argument(
            "element mutation requires a complete ref, provenance, frame, and actionability proof",
        ));
    }
    let Some(input) = input else {
        return Ok(());
    };
    input
        .validate_for_page(request.operation, page, current_snapshot_hash)
        .map(|_| ())
}

fn parse_payload_json<T: for<'de> Deserialize<'de>>(
    payload: &BTreeMap<String, String>,
    key: &str,
) -> Result<T, CoreError> {
    let value = payload.get(key).ok_or_else(|| {
        CoreError::invalid_argument(format!("action payload field {key} is required"))
    })?;
    if value.len() > 65_536 || value.chars().any(char::is_control) {
        return Err(CoreError::invalid_argument(format!(
            "action payload field {key} is invalid or too large"
        )));
    }
    serde_json::from_str(value).map_err(|error| {
        CoreError::invalid_argument(format!(
            "action payload field {key} is not valid JSON: {error}"
        ))
    })
}

/// Validated action proof returned only after every required check passes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionabilityProof {
    /// Action class proven safe.
    pub action: ActionKind,
    /// Logical ref identity.
    pub ref_id: agentyc_core::RefId,
    /// Exact logical space.
    pub space_id: agentyc_core::SpaceId,
    /// Exact logical page.
    pub page_id: agentyc_core::PageId,
    /// Exact logical frame; no browser handle is exposed.
    pub frame_id: FrameId,
    /// Snapshot version used by the proof.
    pub snapshot_version: agentyc_core::SnapshotVersion,
    /// Document generation used by the proof.
    pub document_generation: Generation,
    /// Navigation generation used by the proof.
    pub navigation_generation: Generation,
    /// Ref epoch used by the proof.
    pub refs_epoch: agentyc_core::RefEpoch,
    /// Snapshot hash used by the proof.
    pub snapshot_hash: ContentHash,
    /// Managed target generation, when the caller supplied one.
    pub target_generation: Option<Generation>,
}

/// Fail-closed checker for element actions.
#[derive(Debug, Default, Clone, Copy)]
pub struct ActionabilityChecker;

impl ActionabilityChecker {
    /// Validate an element action against exact frame/provenance and evidence.
    pub fn check(
        action: ActionKind,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        provenance: &SnapshotProvenance,
        evidence: &ActionabilityEvidence,
    ) -> Result<ActionabilityProof, CoreError> {
        if element_ref.frame_id != *frame_id {
            return Err(CoreError::stale_ref(
                "action ref frame does not match the exact requested frame",
            ));
        }
        element_ref.validate_against(provenance)?;
        Self::check_provenance_evidence(provenance, evidence)?;
        if evidence.user_control != Some(false) {
            return Err(CoreError::new(
                if evidence.user_control == Some(true) {
                    ErrorCode::UserControlRequired
                } else {
                    ErrorCode::PermissionDenied
                },
                "user-control ownership is not proven safe",
            ));
        }
        require_bool(
            evidence.connected,
            "connected element evidence is missing or false",
        )?;
        require_bool(
            evidence.visible,
            "visible element evidence is missing or false",
        )?;
        require_bool(
            evidence.disabled.map(|value| !value),
            "enabled element evidence is missing or false",
        )?;
        if matches!(action, ActionKind::Input) {
            require_bool(
                evidence.readonly.map(|value| !value),
                "editable element evidence is missing or false",
            )?;
        }
        require_bool(
            evidence.moving.map(|value| !value),
            "stable layout evidence is missing or false",
        )?;
        require_bool(
            evidence.offscreen.map(|value| !value),
            "in-viewport evidence is missing or false",
        )?;
        if matches!(
            action,
            ActionKind::Click | ActionKind::Focus | ActionKind::Select
        ) {
            require_bool(
                evidence.covered.map(|value| !value),
                "uncovered hit point is not proven",
            )?;
            require_bool(
                evidence.overlay_present.map(|value| !value),
                "overlay-free hit point is not proven",
            )?;
            require_bool(
                evidence.hit_target,
                "hit-target evidence is missing or false",
            )?;
        }
        Ok(ActionabilityProof {
            action,
            ref_id: element_ref.ref_id.clone(),
            space_id: element_ref.space_id.clone(),
            page_id: element_ref.page_id.clone(),
            frame_id: element_ref.frame_id.clone(),
            snapshot_version: element_ref.snapshot_version,
            document_generation: element_ref.document_generation,
            navigation_generation: element_ref.navigation_generation,
            refs_epoch: element_ref.refs_epoch,
            snapshot_hash: provenance.snapshot_hash.clone(),
            target_generation: evidence.target_generation,
        })
    }

    /// Check an element action and require an exact managed target generation.
    pub fn check_with_target_generation(
        action: ActionKind,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        provenance: &SnapshotProvenance,
        expected_target_generation: Generation,
        evidence: &ActionabilityEvidence,
    ) -> Result<ActionabilityProof, CoreError> {
        let mut proof = Self::check(action, element_ref, frame_id, provenance, evidence)?;
        if evidence.target_generation != Some(expected_target_generation) {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "actionability evidence is not bound to the current target generation",
            ));
        }
        proof.target_generation = Some(expected_target_generation);
        Ok(proof)
    }

    /// Check a registry-managed ref before applying actionability evidence.
    pub fn check_with_registry(
        action: ActionKind,
        registry: &mut RefRegistry,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        provenance: &SnapshotProvenance,
        evidence: &ActionabilityEvidence,
        now: agentyc_core::Timestamp,
    ) -> Result<ActionabilityProof, CoreError> {
        registry.validate(element_ref, frame_id, provenance, now)?;
        Self::check(action, element_ref, frame_id, provenance, evidence)
    }

    /// Map a core action operation and check it as an element operation.
    pub fn check_operation(
        operation: ActionOperation,
        element_ref: &ElementRef,
        frame_id: &FrameId,
        provenance: &SnapshotProvenance,
        evidence: &ActionabilityEvidence,
    ) -> Result<ActionabilityProof, CoreError> {
        Self::check(
            operation.into(),
            element_ref,
            frame_id,
            provenance,
            evidence,
        )
    }

    fn check_provenance_evidence(
        provenance: &SnapshotProvenance,
        evidence: &ActionabilityEvidence,
    ) -> Result<(), CoreError> {
        if evidence.navigation_generation != Some(provenance.navigation_generation)
            || evidence.document_generation != Some(provenance.document_generation)
            || evidence.snapshot_hash.as_ref() != Some(&provenance.snapshot_hash)
        {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "actionability evidence is not bound to current snapshot provenance",
            ));
        }
        Ok(())
    }
}

/// Verify a core postcondition against the latest logical read.
#[derive(Debug, Default, Clone, Copy)]
pub struct PostconditionVerifier;

impl PostconditionVerifier {
    /// Verify one postcondition, failing closed if required evidence is absent.
    pub fn verify(
        postcondition: &Postcondition,
        document_generation: Generation,
        snapshot_hash: Option<&ContentHash>,
    ) -> Result<(), CoreError> {
        match postcondition {
            Postcondition::PageGeneration {
                document_generation: expected,
            } if *expected == document_generation => Ok(()),
            Postcondition::PageGeneration { .. } => Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "page document generation did not reach the action postcondition",
            )),
            Postcondition::SnapshotHash {
                snapshot_hash: expected,
            } => {
                if snapshot_hash == Some(expected) {
                    Ok(())
                } else {
                    Err(CoreError::new(
                        ErrorCode::TargetReplaced,
                        "snapshot hash postcondition is absent or does not match",
                    ))
                }
            }
        }
    }

    /// Verify the optional postcondition carried by an action receipt.
    pub fn verify_receipt(
        receipt: &ActionReceipt,
        document_generation: Generation,
        snapshot_hash: Option<&ContentHash>,
    ) -> Result<(), CoreError> {
        let Some(postcondition) = receipt.postcondition.as_ref() else {
            return Ok(());
        };
        Self::verify(postcondition, document_generation, snapshot_hash)
    }

    /// Boolean convenience method for callers that already handle fail-closed denial.
    pub fn matches(
        postcondition: &Postcondition,
        document_generation: Generation,
        snapshot_hash: Option<&ContentHash>,
    ) -> bool {
        Self::verify(postcondition, document_generation, snapshot_hash).is_ok()
    }
}

fn require_bool(value: Option<bool>, message: &str) -> Result<(), CoreError> {
    if value == Some(true) {
        Ok(())
    } else {
        Err(CoreError::new(ErrorCode::PermissionDenied, message))
    }
}

/// Alias for the shared actionability checker.
pub type Actionability = ActionabilityChecker;
/// Alias for explicit actionability facts.
pub type ActionabilityState = ActionabilityEvidence;
/// Alias for a successful actionability proof.
pub type ActionabilityResult = ActionabilityProof;

/// Check one element action using the shared fail-closed policy.
pub fn check_actionability(
    action: ActionKind,
    element_ref: &ElementRef,
    frame_id: &FrameId,
    provenance: &SnapshotProvenance,
    evidence: &ActionabilityEvidence,
) -> Result<ActionabilityProof, CoreError> {
    ActionabilityChecker::check(action, element_ref, frame_id, provenance, evidence)
}

/// Verify one postcondition using the latest logical generation/hash proof.
pub fn verify_postcondition(
    postcondition: &Postcondition,
    document_generation: Generation,
    snapshot_hash: Option<&ContentHash>,
) -> Result<(), CoreError> {
    PostconditionVerifier::verify(postcondition, document_generation, snapshot_hash)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snapshots::empty_snapshot;
    use agentyc_core::{FrameId, PageId, SpaceId};

    fn fixture() -> (ElementRef, SnapshotProvenance, FrameId) {
        let space = SpaceId::from_suffix("space").expect("space");
        let page = PageId::from_suffix("page").expect("page");
        let frame = FrameId::from_suffix("child").expect("frame");
        let mut snapshot = empty_snapshot(space, page);
        snapshot
            .frame_versions
            .insert(frame.clone(), agentyc_core::FrameVersion::new(1));
        let provenance = snapshot.provenance();
        let element_ref = snapshot
            .make_ref(
                agentyc_core::RefId::from_suffix("element").expect("ref"),
                frame.clone(),
            )
            .expect("ref");
        (element_ref, provenance, frame)
    }

    #[test]
    fn disabled_overlay_and_unknown_evidence_fail_closed() {
        let (element_ref, provenance, frame) = fixture();
        let disabled = ActionabilityEvidence::proven_interactive()
            .with_disabled(true)
            .with_generations(
                None,
                provenance.navigation_generation,
                provenance.document_generation,
                Some(provenance.snapshot_hash.clone()),
            );
        assert_eq!(
            ActionabilityChecker::check(
                ActionKind::Click,
                &element_ref,
                &frame,
                &provenance,
                &disabled
            )
            .expect_err("disabled")
            .code,
            ErrorCode::PermissionDenied
        );
        let covered = ActionabilityEvidence::proven_interactive()
            .with_covered(true)
            .with_generations(
                None,
                provenance.navigation_generation,
                provenance.document_generation,
                Some(provenance.snapshot_hash.clone()),
            );
        assert_eq!(
            ActionabilityChecker::check(
                ActionKind::Click,
                &element_ref,
                &frame,
                &provenance,
                &covered
            )
            .expect_err("covered")
            .code,
            ErrorCode::PermissionDenied
        );
        assert!(
            ActionabilityChecker::check(
                ActionKind::Click,
                &element_ref,
                &frame,
                &provenance,
                &ActionabilityEvidence::unknown()
            )
            .is_err()
        );
        let evidence = ActionabilityEvidence::proven_interactive()
            .with_target_generation(agentyc_core::Generation::new(1))
            .with_generations(
                Some(agentyc_core::Generation::new(1)),
                provenance.navigation_generation,
                provenance.document_generation,
                Some(provenance.snapshot_hash.clone()),
            );
        assert!(
            ActionabilityChecker::check_with_target_generation(
                ActionKind::Click,
                &element_ref,
                &frame,
                &provenance,
                agentyc_core::Generation::new(2),
                &evidence,
            )
            .is_err()
        );
    }

    #[test]
    fn actionability_matrix_fails_closed_for_target_and_ownership_changes() {
        let (element_ref, provenance, frame) = fixture();
        let generation = agentyc_core::Generation::new(1);
        let base = ActionabilityEvidence::proven_interactive()
            .with_target_generation(generation)
            .with_generations(
                Some(generation),
                provenance.navigation_generation,
                provenance.document_generation,
                Some(provenance.snapshot_hash.clone()),
            );
        let cases = vec![
            (
                "disconnected",
                ActionKind::Click,
                base.clone().with_connected(false),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "hidden",
                ActionKind::Click,
                base.clone().with_visible(false),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "disabled",
                ActionKind::Click,
                base.clone().with_disabled(true),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "readonly",
                ActionKind::Input,
                base.clone().with_readonly(true),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "covered",
                ActionKind::Click,
                base.clone().with_covered(true),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "moving",
                ActionKind::Click,
                base.clone().with_moving(true),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "offscreen",
                ActionKind::Click,
                base.clone().with_offscreen(true),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "wrong-hit-target",
                ActionKind::Click,
                base.clone().with_hit_target(false),
                frame.clone(),
                ErrorCode::PermissionDenied,
            ),
            (
                "user-control",
                ActionKind::Click,
                base.clone().with_user_control(true),
                frame.clone(),
                ErrorCode::UserControlRequired,
            ),
            (
                "oopif-frame",
                ActionKind::Click,
                base.clone(),
                FrameId::from_suffix("other").expect("frame"),
                ErrorCode::StaleRef,
            ),
            (
                "rerendered-snapshot",
                ActionKind::Click,
                base.clone().with_generations(
                    Some(generation),
                    provenance.navigation_generation,
                    provenance.document_generation,
                    Some(ContentHash::from_bytes(b"rerendered")),
                ),
                frame.clone(),
                ErrorCode::TargetReplaced,
            ),
            (
                "stale-generation",
                ActionKind::Click,
                base.clone().with_generations(
                    Some(generation),
                    Generation::new(2),
                    provenance.document_generation,
                    Some(provenance.snapshot_hash.clone()),
                ),
                frame.clone(),
                ErrorCode::TargetReplaced,
            ),
        ];
        for (name, action, evidence, requested_frame, expected) in cases {
            let error = ActionabilityChecker::check(
                action,
                &element_ref,
                &requested_frame,
                &provenance,
                &evidence,
            )
            .expect_err(name);
            assert_eq!(error.code, expected, "case {name}");
        }
    }

    #[test]
    fn file_and_evaluate_are_not_element_action_kinds() {
        assert_eq!(
            ActionKind::from(ActionOperation::Upload),
            ActionKind::Evaluate
        );
        assert_eq!(
            ActionKind::from(ActionOperation::Evaluate),
            ActionKind::Evaluate
        );
    }

    #[test]
    fn postconditions_require_exact_generation_or_hash() {
        let (_element_ref, provenance, _frame) = fixture();
        let condition = Postcondition::PageGeneration {
            document_generation: provenance.document_generation,
        };
        assert!(
            PostconditionVerifier::verify(&condition, provenance.document_generation, None).is_ok()
        );
        assert!(
            PostconditionVerifier::verify(&condition, agentyc_core::Generation::new(2), None)
                .is_err()
        );
        let hash = Postcondition::SnapshotHash {
            snapshot_hash: provenance.snapshot_hash.clone(),
        };
        assert!(
            PostconditionVerifier::verify(
                &hash,
                provenance.document_generation,
                Some(&provenance.snapshot_hash)
            )
            .is_ok()
        );
        assert!(
            PostconditionVerifier::verify(&hash, provenance.document_generation, None).is_err()
        );
    }
}
