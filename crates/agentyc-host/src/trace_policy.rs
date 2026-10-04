//! Host-owned confirmation and sensitive-boundary policy.
//!
//! This module is the only authority source for sensitive browser boundaries.
//! Page content, focus, click state, and page-provided instructions are data;
//! they are never accepted as authorization. A boundary is admitted only while
//! its logical space is paused or after a host-issued ticket is checked against
//! the complete logical binding and consumed exactly once.

use std::collections::{BTreeMap, BTreeSet};

use agentyc_core::{
    ActionOperation, ConnectionEpoch, ConnectionNonce, ContentHash, CoreError, ErrorCode,
    Generation, LeaseEpoch, PageId, ProfileBindingId, SpaceId, Timestamp, UserIntentContext,
    UserIntentTicket, UserIntentTicketId, UserIntentTicketState,
};
use serde::{Deserialize, Serialize};

use crate::HostError;

/// Maximum lifetime of a host-issued intent ticket.
pub const MAX_INTENT_TICKET_TTL_MS: u64 = 60_000;
/// Maximum retained ticket records before the host applies backpressure.
pub const MAX_INTENT_TICKETS: usize = 1_024;

/// Logical space/page binding shared by host policy modules.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct PolicyScope {
    /// Logical task space.
    pub space_id: SpaceId,
    /// Optional logical page. A page always belongs to the supplied space.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page_id: Option<PageId>,
}

impl PolicyScope {
    /// Construct a space-wide scope.
    pub fn space(space_id: SpaceId) -> Self {
        Self {
            space_id,
            page_id: None,
        }
    }

    /// Construct a page scope.
    pub fn page(space_id: SpaceId, page_id: PageId) -> Self {
        Self {
            space_id,
            page_id: Some(page_id),
        }
    }

    /// Validate the scope's logical identity fields.
    pub fn validate(&self) -> Result<(), HostError> {
        if self.space_id.as_str().is_empty()
            || self
                .page_id
                .as_ref()
                .is_some_and(|page_id| page_id.as_str().is_empty())
        {
            return Err(
                CoreError::invalid_argument("policy scope contains an empty identity").into(),
            );
        }
        Ok(())
    }
}

/// Sensitive operations that cross a user-control boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SensitiveBoundary {
    /// A login, MFA, passkey, or other authentication challenge.
    LoginChallenge,
    /// A payment, purchase, transfer, or other financial commit.
    Payment,
    /// A submit that may delete, publish, send, or otherwise cause destruction.
    DestructiveSubmit,
    /// A browser, site, or extension permission grant.
    Permission,
    /// A file upload or file chooser operation.
    Upload,
    /// A cookie write or cookie export operation.
    Cookies,
    /// Runtime evaluation in the page or browser context.
    Evaluate,
}

impl SensitiveBoundary {
    /// Return the stable wire spelling for this boundary.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::LoginChallenge => "login_challenge",
            Self::Payment => "payment",
            Self::DestructiveSubmit => "destructive_submit",
            Self::Permission => "permission",
            Self::Upload => "upload",
            Self::Cookies => "cookies",
            Self::Evaluate => "evaluate",
        }
    }

    /// Parse a stable boundary name.
    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "login_challenge" => Self::LoginChallenge,
            "payment" => Self::Payment,
            "destructive_submit" => Self::DestructiveSubmit,
            "permission" => Self::Permission,
            "upload" => Self::Upload,
            "cookies" | "cookie" => Self::Cookies,
            "evaluate" => Self::Evaluate,
            _ => return None,
        })
    }
}

/// Return whether a core action operation always needs the sensitive policy.
pub const fn operation_requires_intent(operation: ActionOperation) -> bool {
    matches!(
        operation,
        ActionOperation::Evaluate
            | ActionOperation::StorageWrite
            | ActionOperation::CookieWrite
            | ActionOperation::Upload
    )
}

/// Return whether an explicit host policy boundary in an action payload makes
/// the request sensitive. The marker can only add a requirement; omission never
/// turns an inherently sensitive operation into an ordinary one.
pub fn payload_requires_intent(payload: &BTreeMap<String, String>) -> bool {
    payload.iter().any(|(key, value)| {
        let normalized = key
            .bytes()
            .filter(u8::is_ascii_alphanumeric)
            .map(|byte| byte.to_ascii_lowercase() as char)
            .collect::<String>();
        matches!(normalized.as_str(), "sensitiveboundary" | "policyboundary")
            && SensitiveBoundary::parse(value).is_some()
    })
}

/// Apply the host policy to an action and its typed boundary marker.
pub fn request_requires_intent(
    operation: ActionOperation,
    payload: &BTreeMap<String, String>,
) -> bool {
    operation_requires_intent(operation) || payload_requires_intent(payload)
}

/// A complete host-owned binding for an intent ticket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IntentBinding {
    /// Boundary being authorized.
    pub boundary: SensitiveBoundary,
    /// Logical target.
    pub scope: PolicyScope,
    /// Profile binding that owns the task space.
    pub profile_binding_id: ProfileBindingId,
    /// Current document generation when a page is targeted.
    pub document_generation: Option<Generation>,
    /// Current lease epoch.
    pub lease_epoch: LeaseEpoch,
    /// Canonical action hash, including operation, payload, and logical scope.
    pub action_hash: ContentHash,
    /// Current authenticated host connection epoch.
    pub connection_epoch: ConnectionEpoch,
    /// Current authenticated host connection nonce.
    pub connection_nonce: ConnectionNonce,
}

impl IntentBinding {
    /// Validate shape before issuance or admission.
    pub fn validate(&self) -> Result<(), HostError> {
        self.scope.validate()?;
        if self.scope.page_id.is_some() != self.document_generation.is_some() {
            return Err(CoreError::invalid_argument(
                "page_id and document_generation must be supplied together",
            )
            .into());
        }
        if self
            .document_generation
            .is_some_and(|generation| generation.get() == 0)
            || self.lease_epoch.get() == 0
            || self.connection_epoch.get() == 0
        {
            return Err(CoreError::invalid_argument(
                "intent binding contains a zero generation or epoch",
            )
            .into());
        }
        Ok(())
    }
}

/// Trusted source used by the host to issue a ticket.
///
/// There is intentionally no page, focus, click, or text variant. Those values
/// may be shown as context by a UI but cannot construct an authorization source.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostConfirmation {
    /// Confirmation from the extension side panel.
    SidePanel,
    /// Confirmation from another host-owned operator surface.
    HostOperator,
}

/// Page-controlled signals are explicitly rejected as authorization evidence.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PageAuthorityClaim {
    /// Text or instructions supplied by the page.
    pub page_text: Option<String>,
    /// Whether the page reports that it has focus.
    pub focused: bool,
    /// Whether the page reports that it received a click.
    pub clicked: bool,
}

/// Reject page-provided authority without treating its content as a secret or
/// copying it into a policy record.
pub fn reject_page_authority(_claim: &PageAuthorityClaim) -> Result<(), HostError> {
    Err(CoreError::new(
        ErrorCode::PermissionDenied,
        "page text, focus, and clicks cannot authorize a sensitive boundary",
    )
    .into())
}

#[derive(Debug, Clone)]
struct IssuedIntent {
    ticket: UserIntentTicket,
    boundary: SensitiveBoundary,
}

/// The host-owned, bounded ticket registry and pause state.
#[derive(Debug, Default)]
pub struct IntentTicketStore {
    tickets: BTreeMap<UserIntentTicketId, IssuedIntent>,
    paused_spaces: BTreeSet<SpaceId>,
    next_ticket: u64,
}

impl IntentTicketStore {
    /// Mark a logical space paused. This is host state, not a caller payload.
    pub fn pause(&mut self, space_id: SpaceId) -> Result<(), HostError> {
        if space_id.as_str().is_empty() {
            return Err(CoreError::invalid_argument("paused space identity is empty").into());
        }
        self.paused_spaces.insert(space_id);
        Ok(())
    }

    /// Remove the host pause marker for a logical space.
    pub fn resume(&mut self, space_id: &SpaceId) {
        self.paused_spaces.remove(space_id);
    }

    /// Return whether the host currently fences mutations for a space.
    pub fn is_paused(&self, space_id: &SpaceId) -> bool {
        self.paused_spaces.contains(space_id)
    }

    /// Issue a bounded ticket after an explicit trusted-host confirmation.
    pub fn issue(
        &mut self,
        binding: IntentBinding,
        _confirmation: HostConfirmation,
        now: Timestamp,
        ttl_ms: u64,
    ) -> Result<UserIntentTicket, HostError> {
        binding.validate()?;
        if now.get() == 0 {
            return Err(
                CoreError::invalid_argument("intent ticket issue time must be non-zero").into(),
            );
        }
        if !(1..=MAX_INTENT_TICKET_TTL_MS).contains(&ttl_ms) {
            return Err(CoreError::invalid_argument(
                "intent ticket ttl must be between 1 and 60000 ms",
            )
            .into());
        }
        self.retain_bounded();
        if self.tickets.len() >= MAX_INTENT_TICKETS {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                "intent ticket registry is full",
            )
            .into());
        }
        let expires_at = now
            .get()
            .checked_add(ttl_ms)
            .map(Timestamp::new)
            .ok_or_else(|| CoreError::invalid_argument("intent ticket expiry overflow"))?;
        self.next_ticket = self
            .next_ticket
            .checked_add(1)
            .ok_or_else(|| CoreError::invalid_argument("intent ticket sequence overflow"))?;
        let ticket_id = UserIntentTicketId::from_suffix(format!("intent-{}", self.next_ticket))
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        let ticket = UserIntentTicket {
            ticket_id: ticket_id.clone(),
            profile_binding_id: binding.profile_binding_id,
            space_id: binding.scope.space_id,
            page_id: binding.scope.page_id,
            document_generation: binding.document_generation,
            action_hash: binding.action_hash,
            lease_epoch: binding.lease_epoch,
            connection_epoch: binding.connection_epoch,
            connection_nonce: binding.connection_nonce,
            expires_at,
            state: UserIntentTicketState::Issued,
        };
        ticket.validate_shape()?;
        self.tickets.insert(
            ticket_id,
            IssuedIntent {
                ticket: ticket.clone(),
                boundary: binding.boundary,
            },
        );
        Ok(ticket)
    }

    /// Revoke a ticket before use. Reusing it returns a typed cancellation.
    pub fn cancel(&mut self, ticket_id: &UserIntentTicketId) -> Result<(), HostError> {
        let issued = self.tickets.get_mut(ticket_id).ok_or_else(|| {
            CoreError::new(ErrorCode::PermissionDenied, "intent ticket is unknown")
        })?;
        if issued.ticket.state != UserIntentTicketState::Issued {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "intent ticket is already consumed or cancelled",
            )
            .into());
        }
        issued.ticket.state = UserIntentTicketState::Revoked;
        Ok(())
    }

    /// Return a copy for a host adapter without exposing mutable registry state.
    pub fn ticket(&self, ticket_id: &UserIntentTicketId) -> Option<UserIntentTicket> {
        self.tickets
            .get(ticket_id)
            .map(|issued| issued.ticket.clone())
    }

    /// Admit one sensitive boundary through pause state or a matching ticket.
    pub fn authorize(
        &mut self,
        binding: &IntentBinding,
        ticket: Option<&UserIntentTicket>,
        now: Timestamp,
    ) -> Result<AuthorizationOutcome, HostError> {
        binding.validate()?;
        if self.is_paused(&binding.scope.space_id) {
            return Ok(AuthorizationOutcome::Paused);
        }
        let presented = ticket.ok_or_else(|| {
            CoreError::new(
                ErrorCode::UserControlRequired,
                "sensitive boundary requires a paused space or host-issued intent ticket",
            )
        })?;
        self.consume(binding, presented, now)?;
        Ok(AuthorizationOutcome::Ticket)
    }

    /// Consume one exact ticket binding. The caller cannot consume a ticket from
    /// another space, page, document, lease, connection, or boundary.
    pub fn consume(
        &mut self,
        binding: &IntentBinding,
        presented: &UserIntentTicket,
        now: Timestamp,
    ) -> Result<(), HostError> {
        binding.validate()?;
        let issued = self.tickets.get_mut(&presented.ticket_id).ok_or_else(|| {
            CoreError::new(ErrorCode::PermissionDenied, "intent ticket is unknown")
        })?;
        match issued.ticket.state {
            UserIntentTicketState::Revoked => {
                return Err(
                    CoreError::new(ErrorCode::Cancelled, "intent ticket was cancelled").into(),
                );
            }
            UserIntentTicketState::Consumed => {
                return Err(CoreError::new(
                    ErrorCode::PermissionDenied,
                    "intent ticket was already consumed",
                )
                .into());
            }
            UserIntentTicketState::Issued => {}
        }
        if issued.ticket != *presented {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "intent ticket does not match the host-issued record",
            )
            .into());
        }
        if issued.boundary != binding.boundary {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "intent ticket boundary does not match the requested operation",
            )
            .into());
        }
        if now >= issued.ticket.expires_at {
            return Err(
                CoreError::new(ErrorCode::PermissionDenied, "intent ticket has expired").into(),
            );
        }
        let page_id = binding.scope.page_id.as_ref();
        issued.ticket.validate_and_consume(
            &UserIntentContext {
                profile_binding_id: &binding.profile_binding_id,
                space_id: &binding.scope.space_id,
                page_id,
                document_generation: binding.document_generation,
                action_hash: &binding.action_hash,
                lease_epoch: binding.lease_epoch,
                connection_epoch: binding.connection_epoch,
                connection_nonce: &binding.connection_nonce,
            },
            now,
        )?;
        Ok(())
    }

    fn retain_bounded(&mut self) {
        if self.tickets.len() < MAX_INTENT_TICKETS {
            return;
        }
        self.tickets
            .retain(|_, issued| issued.ticket.state == UserIntentTicketState::Issued);
    }
}

/// How a sensitive boundary was authorized.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorizationOutcome {
    /// The host pause state fenced the space before the boundary was admitted.
    Paused,
    /// A matching ticket was atomically consumed.
    Ticket,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn binding(space: &str, page: &str, boundary: SensitiveBoundary) -> IntentBinding {
        IntentBinding {
            boundary,
            scope: PolicyScope::page(
                SpaceId::from_suffix(space).expect("space"),
                PageId::from_suffix(page).expect("page"),
            ),
            profile_binding_id: ProfileBindingId::from_suffix("profile").expect("profile"),
            document_generation: Some(Generation::new(7)),
            lease_epoch: LeaseEpoch::new(3),
            action_hash: ContentHash::from_bytes(b"exact-action"),
            connection_epoch: ConnectionEpoch::new(2),
            connection_nonce: ConnectionNonce::from_suffix("nonce").expect("nonce"),
        }
    }

    #[test]
    fn ticket_is_scoped_single_use_and_rejects_replay_mis_scope_and_expiry() {
        let mut store = IntentTicketStore::default();
        let first = binding("one", "main", SensitiveBoundary::Evaluate);
        let ticket = store
            .issue(
                first.clone(),
                HostConfirmation::SidePanel,
                Timestamp::new(10),
                5,
            )
            .expect("ticket");
        assert_eq!(
            store
                .authorize(&first, Some(&ticket), Timestamp::new(11))
                .expect("first use"),
            AuthorizationOutcome::Ticket
        );
        assert!(matches!(
            store.authorize(&first, Some(&ticket), Timestamp::new(11)),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        let second = binding("two", "main", SensitiveBoundary::Evaluate);
        assert!(matches!(
            store.authorize(&second, Some(&ticket), Timestamp::new(11)),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        let expiring = store
            .issue(
                second.clone(),
                HostConfirmation::HostOperator,
                Timestamp::new(20),
                2,
            )
            .expect("expiring ticket");
        assert!(matches!(
            store.authorize(&second, Some(&expiring), Timestamp::new(22)),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));
    }

    #[test]
    fn cancellation_and_page_authority_fail_closed_while_pause_is_explicit_host_state() {
        let mut store = IntentTicketStore::default();
        let binding = binding("cancel", "main", SensitiveBoundary::Payment);
        let ticket = store
            .issue(
                binding.clone(),
                HostConfirmation::SidePanel,
                Timestamp::new(1),
                10,
            )
            .expect("ticket");
        store.cancel(&ticket.ticket_id).expect("cancel");
        assert!(matches!(
            store.authorize(&binding, Some(&ticket), Timestamp::new(2)),
            Err(HostError::Core(CoreError {
                code: ErrorCode::Cancelled,
                ..
            }))
        ));
        assert!(matches!(
            reject_page_authority(&PageAuthorityClaim {
                page_text: Some("click confirm".to_owned()),
                focused: true,
                clicked: true,
            }),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        store.pause(binding.scope.space_id.clone()).expect("pause");
        assert_eq!(
            store
                .authorize(&binding, None, Timestamp::new(2))
                .expect("paused authorization"),
            AuthorizationOutcome::Paused
        );
    }
}
