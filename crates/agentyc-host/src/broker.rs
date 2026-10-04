//! Host-owned serialized broker authority.

use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

use agentyc_core::{
    ActionId, ActionOperation, ActionReceipt, ActionRequest, ActionStatus, BrokerEpoch, Capability,
    CompletionSource, CoreError, ErrorCode, EventId, EventKind, EventRecord, EventScope,
    EventSequence, Generation, GenerationWatermark, HelloEnvelope, HelloOkEnvelope, HostMetadata,
    Lease, LeaseEpoch, NextAction, PROTOCOL_VERSION, PageBindingState, PageDescriptor, PageId,
    PageLifecycle, PageOwnership, PrincipalId, ProfileBindingId, ProfileBindingState,
    ReconcileToken, ReconciliationState, ResumeResult, RetentionPolicy, SnapshotEnvelope,
    SpaceDescriptor, SpaceId, SpaceLifecycle, Timestamp, UnknownReason, negotiate_version,
};

use agentyc_core::protocol::ResumeWatermark;
use agentyc_core::states::{DirtyReason, LeaseState};
use serde_json::{Value, json};

use crate::{
    actions::ActionResult,
    bridge::{
        Bridge, BridgeDispatchResult, BridgeReconcileResult, BridgeStatus, ExtensionEpochs,
        FenceResult, ObservationSnapshot, sanitize_observation_snapshot,
    },
    error::HostError,
    events::{EventBatch, EventQuery},
    leases::{AuthorityTicket, ControlReturn, ControlTicket, LeaseGrant, TakeoverResult},
    ledger::{
        ActiveConnection, ControlTicketRecord, FencePurpose, Ledger, LedgerLimits, LedgerState,
        PendingFenceRecord, TakeoverProofRecord, canonical_action_hash as ledger_action_hash,
        validate_action_payload_contract, validate_public_payload_shape,
    },
    snapshots::{PageGeneration, SnapshotCacheRecord, SnapshotMetadataRead, SnapshotRead},
};

/// Lifecycle of the host broker itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostLifecycle {
    /// The ledger and ownership lock are active.
    Ready,
    /// The host rejects new work while durable state is being drained.
    Draining,
    /// The host has stopped accepting work.
    Stopped,
}

/// Host-assigned connection identity returned after a protocol hello.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Connection {
    /// Logical principal authenticated for this connection.
    pub principal_id: PrincipalId,
    /// Broker epoch that owns this connection.
    pub broker_epoch: BrokerEpoch,
    /// Monotonic connection epoch within the broker.
    pub connection_epoch: agentyc_core::ConnectionEpoch,
    /// Negotiated protocol version.
    pub protocol: u16,
    /// Capabilities advertised by the host bridge boundary.
    pub capabilities: Vec<Capability>,
    /// Resume result for the requested cursor.
    pub resume: ResumeResult,
    /// Host-issued proof used for every subsequent authorized operation.
    authority: AuthorityTicket,
}

impl Connection {
    /// Convert the connection to the frozen core handshake acknowledgement.
    pub fn hello_ok(&self) -> HelloOkEnvelope {
        HelloOkEnvelope {
            protocol: self.protocol,
            broker_epoch: self.broker_epoch,
            connection_epoch: self.connection_epoch,
            capabilities: self.capabilities.clone(),
            resume: self.resume,
            host_metadata: Some(HostMetadata {
                host_name: Some("agentyc-host".to_owned()),
                host_version: Some(env!("CARGO_PKG_VERSION").to_owned()),
                connection_nonce: Some(self.authority.connection_nonce.clone()),
                profile_binding_id: self.authority.profile_binding_id.clone(),
            }),
        }
    }

    /// Return the host-issued authority proof for this connection.
    pub fn authority(&self) -> &AuthorityTicket {
        &self.authority
    }
}

/// The authoritative host broker. Clones share one serialized authority.
#[derive(Clone)]
pub struct Broker {
    inner: Arc<Mutex<BrokerInner>>,
}

struct BrokerInner {
    ledger: Ledger,
    bridge: Arc<dyn Bridge>,
    lifecycle: HostLifecycle,
}

#[derive(Debug, Clone)]
struct CleanupProof {
    page_id: PageId,
    generation: PageGeneration,
}

impl std::fmt::Debug for Broker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("Broker").finish_non_exhaustive()
    }
}

impl Broker {
    /// Open a host broker with the default ledger bounds.
    pub fn open(
        path: impl AsRef<std::path::Path>,
        bridge: impl Bridge + 'static,
    ) -> Result<Self, HostError> {
        let ledger = Ledger::open(path).map_err(HostError::Ledger)?;
        Ok(Self::new(ledger, bridge))
    }

    /// Open a host broker with explicit bounded state limits.
    pub fn open_with_limits(
        path: impl AsRef<std::path::Path>,
        limits: LedgerLimits,
        bridge: impl Bridge + 'static,
    ) -> Result<Self, HostError> {
        let ledger = Ledger::open_with_limits(path, limits).map_err(HostError::Ledger)?;
        Ok(Self::new(ledger, bridge))
    }

    /// Construct a broker around an already opened, exclusively owned ledger.
    pub fn new(ledger: Ledger, bridge: impl Bridge + 'static) -> Self {
        Self {
            inner: Arc::new(Mutex::new(BrokerInner {
                ledger,
                bridge: Arc::new(bridge),
                lifecycle: HostLifecycle::Ready,
            })),
        }
    }

    /// Construct a broker around a shared bridge implementation.
    pub fn with_shared_bridge(ledger: Ledger, bridge: Arc<dyn Bridge>) -> Self {
        Self {
            inner: Arc::new(Mutex::new(BrokerInner {
                ledger,
                bridge,
                lifecycle: HostLifecycle::Ready,
            })),
        }
    }

    /// Return the current broker epoch.
    pub fn broker_epoch(&self) -> Result<BrokerEpoch, HostError> {
        self.with_inner(|inner| Ok(inner.ledger.broker_epoch()))
    }

    /// Return the host lifecycle.
    pub fn lifecycle(&self) -> Result<HostLifecycle, HostError> {
        self.with_inner(|inner| Ok(inner.lifecycle))
    }

    /// Return bridge capabilities without exposing bridge implementation state.
    pub fn capabilities(&self) -> Result<Vec<Capability>, HostError> {
        self.with_inner(|inner| Ok(inner.bridge.capabilities()))
    }

    /// Return safe bridge identity and epoch metadata for host status.
    pub fn bridge_status(&self) -> Result<Option<BridgeStatus>, HostError> {
        self.with_inner(|inner| Ok(inner.bridge.bridge_status()))
    }

    /// Return a principal-filtered JSON representation of the durable state.
    pub fn ledger_json(&self, authority: &AuthorityTicket) -> Result<Vec<u8>, HostError> {
        self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            let state = visible_state(inner.ledger.state(), authority.principal_id());
            serde_json::to_vec_pretty(&state).map_err(HostError::Json)
        })
    }

    /// Clone the principal-filtered durable logical state for adapter serialization.
    pub fn state_snapshot(&self, authority: &AuthorityTicket) -> Result<LedgerState, HostError> {
        self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            Ok(visible_state(
                inner.ledger.state(),
                authority.principal_id(),
            ))
        })
    }

    /// Admit a local client and allocate a host-owned principal/connection epoch.
    pub fn hello(&self, hello: &HelloEnvelope) -> Result<Connection, HostError> {
        hello.validate_handshake()?;
        let negotiated = negotiate_version(&hello.supported_protocols, &[PROTOCOL_VERSION])?;
        if hello.protocol != negotiated.protocol {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                format!("requested protocol {} is not negotiated", hello.protocol),
            )
            .into());
        }
        let capabilities = self.capabilities()?;
        let extension_epochs = self.with_inner(|inner| Ok(inner.bridge.extension_epochs()))?;
        self.with_inner(|inner| {
            let metadata = hello
                .client_metadata
                .as_ref()
                .ok_or_else(|| CoreError::invalid_argument("handshake metadata is missing"))?;
            let connection_nonce = metadata
                .connection_nonce
                .clone()
                .ok_or_else(|| CoreError::invalid_argument("handshake nonce is missing"))?;
            let profile_binding_id = metadata.profile_binding_id.clone();
            let is_extension = metadata.client_name.as_deref() == Some("agentyc-extension");
            let (connection_epoch, principal_id, resume) = inner.ledger.update(|state| {
                if state.used_connection_nonces.contains(&connection_nonce) {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "connection nonce was already used in this broker epoch",
                    )
                    .into());
                }
                let connection_epoch = state
                    .connection_epoch
                    .checked_next()
                    .ok_or_else(|| CoreError::invalid_argument("connection epoch overflow"))?;
                let principal_id = hello.principal_id.clone();
                if is_extension {
                    if let Some(ExtensionEpochs {
                        worker_instance_epoch,
                        browser_session_epoch,
                    }) = extension_epochs
                    {
                        let profile_binding_id = profile_binding_id.as_ref().ok_or_else(|| {
                            CoreError::new(
                                ErrorCode::ProfileNotFound,
                                "extension connection has no profile binding",
                            )
                        })?;
                        if state
                            .extension_profile_binding_id
                            .as_ref()
                            .is_some_and(|current| current != profile_binding_id)
                        {
                            return Err(CoreError::new(
                                ErrorCode::ProfileNotFound,
                                "extension profile binding changed without re-enrollment",
                            )
                            .into());
                        }
                        if state
                            .extension_worker_instance_epoch
                            .is_some_and(|current| worker_instance_epoch < current)
                            || state
                                .extension_browser_session_epoch
                                .is_some_and(|current| browser_session_epoch < current)
                        {
                            return Err(CoreError::new(
                                ErrorCode::PermissionDenied,
                                "extension epoch is older than the durable host fence",
                            )
                            .into());
                        }
                        state.extension_profile_binding_id = Some(profile_binding_id.clone());
                        state.extension_worker_instance_epoch = Some(
                            state
                                .extension_worker_instance_epoch
                                .unwrap_or(0)
                                .max(worker_instance_epoch),
                        );
                        state.extension_browser_session_epoch = Some(
                            state
                                .extension_browser_session_epoch
                                .unwrap_or(0)
                                .max(browser_session_epoch),
                        );
                    }
                    // A Native Messaging reconnect or worker replacement must
                    // revoke the previous extension authority before admitting
                    // the new session. Local agent connections remain
                    // multiplexed through the broker.
                    state
                        .active_connections
                        .retain(|_, current| !current.is_extension);
                }
                state.connection_epoch = connection_epoch;
                state.connection_principal_id = Some(principal_id.clone());
                state.connection_nonce = Some(connection_nonce.clone());
                state.connection_profile_binding_id = profile_binding_id.clone();
                let resume = hello
                    .resume_from
                    .map_or(ResumeResult::Accepted, |watermark| {
                        resume_status(state, watermark)
                    });
                state
                    .used_connection_nonces
                    .insert(connection_nonce.clone());
                state.active_connections.insert(
                    connection_epoch,
                    ActiveConnection {
                        principal_id: principal_id.clone(),
                        connection_nonce: connection_nonce.clone(),
                        profile_binding_id: profile_binding_id.clone(),
                        is_extension,
                    },
                );
                Ok((connection_epoch, principal_id, resume))
            })?;
            let authority = AuthorityTicket::host_issued(
                principal_id.clone(),
                inner.ledger.broker_epoch(),
                connection_epoch,
                profile_binding_id,
                connection_nonce,
            );
            Ok(Connection {
                principal_id,
                broker_epoch: inner.ledger.broker_epoch(),
                connection_epoch,
                protocol: negotiated.protocol,
                capabilities,
                resume,
                authority,
            })
        })
    }

    /// Alias for [`Broker::hello`] used by local protocol adapters.
    pub fn admit(&self, hello: &HelloEnvelope) -> Result<Connection, HostError> {
        self.hello(hello)
    }

    /// Remove one disconnected local connection without affecting other clients.
    pub fn disconnect(&self, authority: &AuthorityTicket) -> Result<(), HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let epoch = authority.connection_epoch();
                let matches = authority.broker_epoch() == state.broker_epoch
                    && state.active_connections.get(&epoch).is_some_and(|current| {
                        current.principal_id == *authority.principal_id()
                            && current.connection_nonce == *authority.connection_nonce()
                            && current.profile_binding_id == authority.profile_binding_id().cloned()
                    });
                if matches {
                    state.active_connections.remove(&epoch);
                }
                Ok(())
            })
        })
    }

    /// Create a new logical space with a host-assigned identity.
    pub fn create_space(
        &self,
        authority: &AuthorityTicket,
        label: impl Into<String>,
    ) -> Result<SpaceDescriptor, HostError> {
        self.create_space_with_retention(authority, label, RetentionPolicy::default())
    }

    /// Create a new logical space with an explicit retention policy.
    pub fn create_space_with_retention(
        &self,
        authority: &AuthorityTicket,
        label: impl Into<String>,
        retention: RetentionPolicy,
    ) -> Result<SpaceDescriptor, HostError> {
        let label = bounded_label(label.into())?;
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                let number = state.next_space_number.checked_add(1).ok_or_else(|| {
                    CoreError::invalid_argument("space identity counter overflow")
                })?;
                let space_id = SpaceId::from_suffix(format!("space-{number}"))
                    .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
                state.next_space_number = number;
                create_space_in_state(
                    state,
                    authority.principal_id().clone(),
                    space_id,
                    label.clone(),
                    retention,
                    authority.profile_binding_id().cloned(),
                )
            })
        })
    }

    /// List only spaces visible to a principal; no bridge inventory is consulted.
    pub fn list_spaces(
        &self,
        authority: &AuthorityTicket,
    ) -> Result<Vec<SpaceDescriptor>, HostError> {
        self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            Ok(inner
                .ledger
                .state()
                .spaces
                .values()
                .filter(|space| visible_to_principal(space, authority.principal_id()))
                .cloned()
                .collect())
        })
    }

    /// List spaces enrolled in the extension's profile for user-control UI.
    ///
    /// This intentionally uses the host-issued profile binding rather than the
    /// extension principal's ownership visibility: the side panel is the
    /// profile owner control surface, while mutation authority still remains
    /// fenced by the broker methods below.
    pub fn list_profile_spaces(
        &self,
        authority: &AuthorityTicket,
    ) -> Result<Vec<SpaceDescriptor>, HostError> {
        self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            let profile = authority.profile_binding_id().ok_or_else(|| {
                CoreError::new(
                    ErrorCode::ProfileNotFound,
                    "profile-bound authority is required for control spaces",
                )
            })?;
            Ok(inner
                .ledger
                .state()
                .spaces
                .values()
                .filter(|space| {
                    inner.ledger.state().profile_bindings.get(&space.space_id) == Some(profile)
                })
                .cloned()
                .collect())
        })
    }

    /// Read one logical space record after a principal visibility check.
    pub fn describe_space(
        &self,
        authority: &AuthorityTicket,
        space_id: &SpaceId,
    ) -> Result<SpaceDescriptor, HostError> {
        self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            let space = inner.ledger.state().spaces.get(space_id).ok_or_else(|| {
                CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
            })?;
            if !visible_to_principal(space, authority.principal_id()) {
                return Err(CoreError::new(
                    ErrorCode::SpaceForbidden,
                    "principal cannot read this space",
                )
                .into());
            }
            Ok(space.clone())
        })
    }

    /// Apply a bounded extension lifecycle event to the durable logical ledger.
    ///
    /// Native Messaging events are advisory browser observations, never client
    /// authority. Page generations only move forward; target-loss events mark a
    /// page lost and invalidate its snapshot cache without closing any tab.
    pub fn apply_bridge_event(
        &self,
        authority: &AuthorityTicket,
        event: &Value,
        _now: Timestamp,
    ) -> Result<(), HostError> {
        let event_name = event.get("event").and_then(Value::as_str).unwrap_or("");
        if event_name.is_empty() || event_name == "inventory" {
            return Ok(());
        }
        let event_payload = event
            .get("payload")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let space_text = event_payload
            .get("space_id")
            .and_then(Value::as_str)
            .or_else(|| event.get("space_id").and_then(Value::as_str));
        let page_text = event_payload
            .get("page_id")
            .and_then(Value::as_str)
            .or_else(|| event.get("page_id").and_then(Value::as_str));
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                if event_name == "browser.session_changed" {
                    // The Native Messaging hello carries the authoritative
                    // browser-session epoch. Do not orphan every space from a
                    // late/replayed UI event; the host applies recovery only
                    // after the new epoch is admitted and a fresh lease/rebind
                    // path is used.
                    return Ok(());
                }
                let (Some(space_text), Some(page_text)) = (space_text, page_text) else {
                    return Ok(());
                };
                let Ok(space_id) = space_text.parse::<SpaceId>() else {
                    return Ok(());
                };
                let Ok(page_id) = page_text.parse::<PageId>() else {
                    return Ok(());
                };
                authorize_profile_for_mutation(state, &space_id, authority)?;
                let mut page_changed = false;
                let target_lost = matches!(event_name, "page.lost" | "page.replaced");
                let target = event_payload
                    .get("target_generation")
                    .and_then(Value::as_u64);
                let navigation = event_payload
                    .get("navigation_generation")
                    .and_then(Value::as_u64);
                let document = event_payload
                    .get("document_generation")
                    .and_then(Value::as_u64);
                if let Some(space) = state.spaces.get_mut(&space_id) {
                    if let Some(page) = space.page_mut(&page_id) {
                        // Cleanup proofs are host-side compare-and-set values.
                        // A late ordinary page.changed event must not advance
                        // a page generation while it is Closing.
                        if page.lifecycle == PageLifecycle::Closing && !target_lost {
                            return Ok(());
                        }
                        if let Some(value) =
                            target.filter(|value| *value > page.target_generation.get())
                        {
                            page.target_generation = Generation::new(value);
                            page_changed = true;
                        }
                        if let Some(value) =
                            navigation.filter(|value| *value > page.navigation_generation.get())
                        {
                            page.navigation_generation = Generation::new(value);
                            page_changed = true;
                        }
                        if let Some(value) =
                            document.filter(|value| *value > page.document_generation.get())
                        {
                            page.document_generation = Generation::new(value);
                            page_changed = true;
                        }
                        if target_lost {
                            if !matches!(
                                page.lifecycle,
                                PageLifecycle::Closed | PageLifecycle::Closing
                            ) {
                                if target.is_none() {
                                    bump_page_generations(page)?;
                                }
                                page.lifecycle = PageLifecycle::TargetLost;
                                page.binding = PageBindingState::Lost;
                                page_changed = true;
                            }
                        }
                        if page_changed {
                            state.snapshots.remove(&space_id);
                        }
                    }
                }
                if page_changed {
                    append_event(
                        state,
                        EventScope::page(space_id, page_id),
                        EventKind::PageChanged,
                        payload([("reason", event_name.to_owned())]),
                        None,
                        target_lost,
                    )?;
                }
                Ok(())
            })
        })
    }

    /// Return a fresh, space-scoped live inventory without changing ledger state.
    pub fn page_inventory(
        &self,
        authority: &AuthorityTicket,
        space_id: &SpaceId,
    ) -> Result<ObservationSnapshot, HostError> {
        self.with_inner(|inner| {
            authorize_visible_space(inner.ledger.state(), authority, space_id)
        })?;
        let bridge = self.bridge()?;
        let observation = bridge.observe().map_err(HostError::Bridge)?;
        let observation = sanitize_observation_snapshot(observation)?;
        self.reconcile_bridge_pages(&observation)?;
        let pages = observation
            .pages
            .into_iter()
            .filter(|record| page_is_visible_in_inventory(record, space_id))
            .collect();
        let groups = observation
            .groups
            .into_iter()
            .filter(|group| {
                group.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
            })
            .collect();
        Ok(ObservationSnapshot {
            pages,
            groups,
            safety: observation.safety,
            recovery_observed: observation.recovery_observed,
        })
    }

    /// Return the current durable handoff ticket for a visible user-owned space.
    pub fn control_ticket(
        &self,
        authority: &AuthorityTicket,
        space_id: &SpaceId,
    ) -> Result<ControlTicket, HostError> {
        self.with_inner(|inner| {
            let state = inner.ledger.state();
            authorize_visible_space(state, authority, space_id)?;
            let record = state.control_tickets.get(space_id).ok_or_else(|| {
                CoreError::new(
                    ErrorCode::UserControlRequired,
                    "space has no current user-control ticket",
                )
            })?;
            Ok(ControlTicket::new(
                record.space_id.clone(),
                record.broker_epoch,
                record.fence_epoch,
                record.token.clone(),
            ))
        })
    }

    /// Acquire the first available lease or acquire a lease after expiry/recovery.
    pub fn acquire_lease(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        now: Timestamp,
        ttl: u64,
    ) -> Result<LeaseGrant, HostError> {
        let (expires_at, renew_by) = lease_times(now, ttl)?;
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                authorize_profile_for_mutation(state, space_id, authority)?;
                let space = state.spaces.get(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                if let Some(current) = &space.lease
                    && current.state == LeaseState::Active
                    && current.expires_at.get() > now.get()
                {
                    if current.principal_id == *authority.principal_id() {
                        return Ok(LeaseGrant {
                            space_id: space_id.clone(),
                            lease: current.clone(),
                        });
                    }
                    return Err(CoreError::new(
                        ErrorCode::SpaceForbidden,
                        "space has an active lease; use explicit takeover",
                    )
                    .into());
                }
                if space.lifecycle == SpaceLifecycle::UserOwned {
                    return Err(CoreError::new(
                        ErrorCode::UserControlRequired,
                        "user-owned space requires an explicit control ticket",
                    )
                    .into());
                }
                if matches!(
                    space.lifecycle,
                    SpaceLifecycle::FencePending
                        | SpaceLifecycle::FenceDispatched
                        | SpaceLifecycle::FenceAcknowledged
                ) {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "space has a pending fence; acknowledge or reconcile it first",
                    )
                    .into());
                }
                if !matches!(
                    space.lifecycle,
                    SpaceLifecycle::Created
                        | SpaceLifecycle::Orphaned
                        | SpaceLifecycle::Paused
                        | SpaceLifecycle::Released
                ) && space.owner != *authority.principal_id()
                {
                    return Err(CoreError::new(
                        ErrorCode::SpaceForbidden,
                        "space is not available for this principal",
                    )
                    .into());
                }
                let previous_epoch = space
                    .lease
                    .as_ref()
                    .map_or(0, |lease| lease.lease_epoch.get());
                let next_epoch = previous_epoch
                    .checked_add(1)
                    .ok_or_else(|| CoreError::invalid_argument("lease epoch overflow"))?;
                let lease = Lease::active(
                    authority.principal_id().clone(),
                    LeaseEpoch::new(next_epoch),
                    expires_at,
                    renew_by,
                );
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                descriptor.lease = Some(lease.clone());
                descriptor.owner = authority.principal_id().clone();
                descriptor.lifecycle = SpaceLifecycle::AgentOwned;
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", next_epoch.to_string()),
                        ("state", "active".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", "agent_owned".to_owned())]),
                    None,
                    false,
                )?;
                Ok(LeaseGrant {
                    space_id: space_id.clone(),
                    lease,
                })
            })
        })
    }

    /// Renew a current lease without changing its fencing epoch.
    pub fn renew_lease(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        ttl: u64,
    ) -> Result<LeaseGrant, HostError> {
        let (expires_at, renew_by) = lease_times(now, ttl)?;
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                authorize_space(state, space_id, authority, lease_epoch, now, true)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let lease = descriptor.lease.as_mut().ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceForbidden, "space has no lease")
                })?;
                lease.expires_at = expires_at;
                lease.renew_by = renew_by;
                lease.state = LeaseState::Active;
                let result = LeaseGrant {
                    space_id: space_id.clone(),
                    lease: lease.clone(),
                };
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", lease_epoch.get().to_string()),
                        ("state", "active".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })
    }

    /// Durably fence the current lease before returning a space to user control.
    pub fn return_control(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<ControlReturn, HostError> {
        let (old_epoch, fence_epoch, fence_token) = self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                authorize_space(state, space_id, authority, lease_epoch, now, true)?;
                let next_number = lease_epoch
                    .get()
                    .checked_add(1)
                    .ok_or_else(|| CoreError::invalid_argument("lease epoch overflow"))?;
                let fence_epoch = LeaseEpoch::new(next_number);
                let fence_token =
                    fence_request_token(state, space_id, fence_epoch, FencePurpose::ReturnControl)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let lease = descriptor.lease.as_mut().ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceForbidden, "space has no active lease")
                })?;
                lease.lease_epoch = fence_epoch;
                lease.state = LeaseState::Fenced;
                descriptor.lifecycle = SpaceLifecycle::FencePending;
                for page in &mut descriptor.pages {
                    bump_page_generations(page)?;
                }
                state.snapshots.remove(space_id);
                let queued = state.action_queues.remove(space_id).unwrap_or_default();
                for action_id in queued {
                    if let Some(receipt) = state.actions.get_mut(&action_id)
                        && receipt.status == ActionStatus::Queued
                    {
                        receipt.cancel(Some(now))?;
                        append_event(
                            state,
                            EventScope::space(space_id.clone()),
                            EventKind::ActionChanged,
                            payload([
                                ("action_id", action_id.to_string()),
                                ("status", "cancelled".to_owned()),
                            ]),
                            None,
                            false,
                        )?;
                    }
                }
                let running: Vec<ActionId> = state
                    .actions
                    .iter()
                    .filter_map(|(action_id, receipt)| {
                        (receipt.space_id == *space_id && receipt.status == ActionStatus::Running)
                            .then_some(action_id.clone())
                    })
                    .collect();
                for action_id in running {
                    if let Some(receipt) = state.actions.get_mut(&action_id) {
                        receipt.mark_unknown(
                            UnknownReason::FenceInterrupted,
                            reconcile_token("return", fence_epoch.get())?,
                        )?;
                        append_event(
                            state,
                            EventScope::space(space_id.clone()),
                            EventKind::ActionChanged,
                            payload([
                                ("action_id", action_id.to_string()),
                                ("status", "unknown".to_owned()),
                            ]),
                            None,
                            true,
                        )?;
                    }
                }
                state.pending_fences.insert(
                    space_id.clone(),
                    PendingFenceRecord {
                        space_id: space_id.clone(),
                        broker_epoch: state.broker_epoch,
                        request_token: fence_token.clone(),
                        old_epoch: Some(lease_epoch),
                        fence_epoch,
                        principal_id: authority.principal_id().clone(),
                        purpose: FencePurpose::ReturnControl,
                    },
                );
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", fence_epoch.get().to_string()),
                        ("state", "fence_pending".to_owned()),
                    ]),
                    None,
                    true,
                )?;
                Ok((Some(lease_epoch), fence_epoch, fence_token))
            })
        })?;
        let bridge = self.bridge()?;
        let fence = bridge.fence(space_id, old_epoch, fence_epoch, self.broker_epoch()?);
        match fence {
            Ok(FenceResult { acknowledged: true }) => self.finish_user_return(
                space_id,
                authority,
                lease_epoch,
                fence_epoch,
                &fence_token,
                now,
            ),
            Ok(FenceResult {
                acknowledged: false,
            }) => {
                self.record_fence_warning(space_id, "user return fence was not acknowledged")?;
                Err(CoreError::new(
                    ErrorCode::ExtensionNotConnected,
                    "user return requires an acknowledged fence",
                )
                .into())
            }
            Err(error) => {
                self.record_fence_warning(space_id, error.message.as_str())?;
                Err(HostError::Bridge(error))
            }
        }
    }

    fn finish_user_return(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        released_epoch: LeaseEpoch,
        fence_epoch: LeaseEpoch,
        fence_token: &ReconcileToken,
        _now: Timestamp,
    ) -> Result<ControlReturn, HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                let pending = state.pending_fences.get(space_id).cloned().ok_or_else(|| {
                    CoreError::new(
                        ErrorCode::StaleLease,
                        "user-return fence completion is no longer pending",
                    )
                })?;
                if pending.purpose != FencePurpose::ReturnControl
                    || pending.request_token != *fence_token
                    || pending.broker_epoch != state.broker_epoch
                    || pending.fence_epoch != fence_epoch
                    || pending.old_epoch != Some(released_epoch)
                    || pending.principal_id != *authority.principal_id()
                {
                    return Err(CoreError::new(
                        ErrorCode::StaleLease,
                        "user-return fence completion does not match the pending request",
                    )
                    .into());
                }
                let token = reconcile_token("control", fence_epoch.get())?;
                {
                    let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    let valid = descriptor.lifecycle == SpaceLifecycle::FencePending
                        && descriptor.lease.as_ref().is_some_and(|lease| {
                            lease.principal_id == *authority.principal_id()
                                && lease.lease_epoch == fence_epoch
                                && lease.state == LeaseState::Fenced
                        });
                    if !valid {
                        return Err(CoreError::stale_lease(
                            fence_epoch.get(),
                            released_epoch.get(),
                        )
                        .into());
                    }
                    if let Some(lease) = &mut descriptor.lease {
                        lease.state = LeaseState::Released;
                    }
                    descriptor.lifecycle = SpaceLifecycle::UserOwned;
                    for page in &mut descriptor.pages {
                        page.ownership = PageOwnership::User;
                        page.binding = PageBindingState::UserOwned;
                        page.lifecycle = PageLifecycle::UserOwned;
                    }
                }
                state.pending_fences.remove(space_id);
                state.control_tickets.insert(
                    space_id.clone(),
                    ControlTicketRecord {
                        space_id: space_id.clone(),
                        broker_epoch: state.broker_epoch,
                        fence_epoch,
                        token: token.clone(),
                    },
                );
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", fence_epoch.get().to_string()),
                        ("state", "released".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", "user_owned".to_owned())]),
                    None,
                    false,
                )?;
                Ok(ControlReturn {
                    space_id: space_id.clone(),
                    released_epoch,
                    fence_epoch,
                    control_ticket: ControlTicket::new(
                        space_id.clone(),
                        state.broker_epoch,
                        fence_epoch,
                        token,
                    ),
                    lifecycle: SpaceLifecycle::UserOwned,
                })
            })
        })
    }

    fn record_fence_warning(&self, space_id: &SpaceId, warning: &str) -> Result<(), HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                descriptor.warnings.push(bounded_warning(warning));
                if descriptor.warnings.len() > 16 {
                    descriptor.warnings.remove(0);
                }
                Ok(())
            })
        })
    }

    /// Increment the lease epoch, fence old work, and transfer ownership.
    ///
    /// A bridge outage is represented as a durable `FencePending` result rather
    /// than as permission to proceed. The caller may retry with
    /// [`Broker::acknowledge_fence`].
    pub fn takeover(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        now: Timestamp,
        ttl: u64,
    ) -> Result<TakeoverResult, HostError> {
        let (expires_at, renew_by) = lease_times(now, ttl)?;
        let (old_epoch, new_epoch, fence_token) = self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                authorize_profile_for_mutation(state, space_id, authority)?;
                let space = state.spaces.get(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                if space.lifecycle == SpaceLifecycle::UserOwned {
                    return Err(CoreError::new(
                        ErrorCode::UserControlRequired,
                        "user-owned space requires its control ticket",
                    )
                    .into());
                }
                if matches!(
                    space.lifecycle,
                    SpaceLifecycle::FencePending
                        | SpaceLifecycle::FenceDispatched
                        | SpaceLifecycle::FenceAcknowledged
                ) {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "space already has a pending fence; acknowledge it first",
                    )
                    .into());
                }
                let current_lease = space.lease.as_ref().ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceForbidden, "space has no current authority")
                })?;
                if current_lease.principal_id != *authority.principal_id()
                    || current_lease.state != LeaseState::Active
                    || current_lease.expires_at.get() <= now.get()
                {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "takeover requires the current authority or a control ticket",
                    )
                    .into());
                }
                let old_epoch = Some(current_lease.lease_epoch);
                let next_number = old_epoch
                    .map_or(0, LeaseEpoch::get)
                    .checked_add(1)
                    .ok_or_else(|| CoreError::invalid_argument("lease epoch overflow"))?;
                let new_epoch = LeaseEpoch::new(next_number);
                let fence_token =
                    fence_request_token(state, space_id, new_epoch, FencePurpose::Takeover)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                for page in &mut descriptor.pages {
                    bump_page_generations(page)?;
                }
                state.snapshots.remove(space_id);
                descriptor.owner = authority.principal_id().clone();
                descriptor.lease = Some(Lease::active(
                    authority.principal_id().clone(),
                    new_epoch,
                    expires_at,
                    renew_by,
                ));
                descriptor.lifecycle = SpaceLifecycle::FencePending;
                let queued = state.action_queues.remove(space_id).unwrap_or_default();
                for action_id in queued {
                    if let Some(receipt) = state.actions.get_mut(&action_id)
                        && receipt.status == ActionStatus::Queued
                    {
                        receipt.cancel(Some(now))?;
                        append_event(
                            state,
                            EventScope::space(space_id.clone()),
                            EventKind::ActionChanged,
                            payload([
                                ("action_id", action_id.to_string()),
                                ("status", "cancelled".to_owned()),
                            ]),
                            None,
                            false,
                        )?;
                    }
                }
                let running: Vec<ActionId> = state
                    .actions
                    .iter()
                    .filter_map(|(action_id, receipt)| {
                        (receipt.space_id == *space_id && receipt.status == ActionStatus::Running)
                            .then_some(action_id.clone())
                    })
                    .collect();
                for action_id in running {
                    if let Some(receipt) = state.actions.get_mut(&action_id) {
                        let token = reconcile_token("fence", new_epoch.get())?;
                        receipt.mark_unknown(UnknownReason::FenceInterrupted, token)?;
                        append_event(
                            state,
                            EventScope::space(space_id.clone()),
                            EventKind::ActionChanged,
                            payload([
                                ("action_id", action_id.to_string()),
                                ("status", "unknown".to_owned()),
                            ]),
                            None,
                            true,
                        )?;
                    }
                }
                state.pending_fences.insert(
                    space_id.clone(),
                    PendingFenceRecord {
                        space_id: space_id.clone(),
                        broker_epoch: state.broker_epoch,
                        request_token: fence_token.clone(),
                        old_epoch,
                        fence_epoch: new_epoch,
                        principal_id: authority.principal_id().clone(),
                        purpose: FencePurpose::Takeover,
                    },
                );
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", new_epoch.get().to_string()),
                        ("state", "fence_pending".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok((old_epoch, new_epoch, fence_token))
            })
        })?;

        let bridge = self.bridge()?;
        let fence = bridge.fence(space_id, old_epoch, new_epoch, self.broker_epoch()?);
        let (fence_acknowledged, bridge_error) = match fence {
            Ok(FenceResult { acknowledged }) => (acknowledged, None),
            Err(error) => (false, Some(error)),
        };
        let (acknowledged, bridge_error) = if fence_acknowledged {
            match self.rebind_pages_after_fence(space_id, authority, new_epoch) {
                Ok(()) => (true, None),
                Err(error) => (false, Some(error.as_core_error())),
            }
        } else {
            (false, bridge_error)
        };
        let lifecycle = self.finish_fence(
            space_id,
            authority,
            new_epoch,
            &fence_token,
            acknowledged,
            bridge_error,
        )?;
        Ok(TakeoverResult {
            space_id: space_id.clone(),
            lease_epoch: new_epoch,
            fence_acknowledged: acknowledged,
            lifecycle,
        })
    }

    /// Reclaim a user-owned space with the one-time ticket returned by `return_control`.
    pub fn takeover_with_control_ticket(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        control_ticket: &ControlTicket,
        now: Timestamp,
        ttl: u64,
    ) -> Result<TakeoverResult, HostError> {
        let (expires_at, renew_by) = lease_times(now, ttl)?;
        let (old_epoch, new_epoch, fence_token) = self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                authorize_profile_for_mutation(state, space_id, authority)?;
                let space = state.spaces.get(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let current_epoch = space
                    .lease
                    .as_ref()
                    .ok_or_else(|| {
                        CoreError::new(
                            ErrorCode::UserControlRequired,
                            "user-owned space has no control fence",
                        )
                    })?
                    .lease_epoch;
                if space.lifecycle != SpaceLifecycle::UserOwned
                    || control_ticket.space_id() != space_id
                    || control_ticket.broker_epoch() != state.broker_epoch
                    || control_ticket.fence_epoch() != current_epoch
                    || state
                        .control_tickets
                        .get(space_id)
                        .is_none_or(|record| record.token != *control_ticket.token())
                {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "control ticket is stale or belongs to another space",
                    )
                    .into());
                }
                let new_epoch = LeaseEpoch::new(
                    current_epoch
                        .get()
                        .checked_add(1)
                        .ok_or_else(|| CoreError::invalid_argument("lease epoch overflow"))?,
                );
                let fence_token =
                    fence_request_token(state, space_id, new_epoch, FencePurpose::Takeover)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                for page in &mut descriptor.pages {
                    bump_page_generations(page)?;
                }
                descriptor.owner = authority.principal_id().clone();
                descriptor.lease = Some(Lease::active(
                    authority.principal_id().clone(),
                    new_epoch,
                    expires_at,
                    renew_by,
                ));
                descriptor.lifecycle = SpaceLifecycle::FencePending;
                state.control_tickets.remove(space_id);
                state.takeover_proofs.remove(space_id);
                state.snapshots.remove(space_id);
                state.pending_fences.insert(
                    space_id.clone(),
                    PendingFenceRecord {
                        space_id: space_id.clone(),
                        broker_epoch: state.broker_epoch,
                        request_token: fence_token.clone(),
                        old_epoch: Some(current_epoch),
                        fence_epoch: new_epoch,
                        principal_id: authority.principal_id().clone(),
                        purpose: FencePurpose::Takeover,
                    },
                );
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", new_epoch.get().to_string()),
                        ("state", "fence_pending".to_owned()),
                    ]),
                    None,
                    true,
                )?;
                Ok((Some(current_epoch), new_epoch, fence_token))
            })
        })?;
        let bridge = self.bridge()?;
        let fence = bridge.fence(space_id, old_epoch, new_epoch, self.broker_epoch()?);
        let (fence_acknowledged, bridge_error) = match fence {
            Ok(FenceResult { acknowledged }) => (acknowledged, None),
            Err(error) => (false, Some(error)),
        };
        let (acknowledged, bridge_error) = if fence_acknowledged {
            match self.rebind_pages_after_fence(space_id, authority, new_epoch) {
                Ok(()) => (true, None),
                Err(error) => (false, Some(error.as_core_error())),
            }
        } else {
            (false, bridge_error)
        };
        let lifecycle = self.finish_fence(
            space_id,
            authority,
            new_epoch,
            &fence_token,
            acknowledged,
            bridge_error,
        )?;
        Ok(TakeoverResult {
            space_id: space_id.clone(),
            lease_epoch: new_epoch,
            fence_acknowledged: acknowledged,
            lifecycle,
        })
    }

    /// Retry an unacknowledged user-return fence and return its durable control ticket.
    pub fn acknowledge_return_control(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        fence_epoch: LeaseEpoch,
    ) -> Result<ControlReturn, HostError> {
        let (released_epoch, fence_token, old_epoch) = self.with_inner(|inner| {
            require_capability(&*inner.bridge, Capability::Action)?;
            authorize_ticket(inner.ledger.state(), authority)?;
            let state = inner.ledger.state();
            let space = state.spaces.get(space_id).ok_or_else(|| {
                CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
            })?;
            let pending = state.pending_fences.get(space_id).ok_or_else(|| {
                CoreError::new(
                    ErrorCode::PermissionDenied,
                    "user-return fence request is missing",
                )
            })?;
            if pending.purpose != FencePurpose::ReturnControl
                || pending.fence_epoch != fence_epoch
                || pending.principal_id != *authority.principal_id()
                || !space.lease.as_ref().is_some_and(|lease| {
                    lease.principal_id == *authority.principal_id()
                        && lease.lease_epoch == fence_epoch
                        && lease.state == LeaseState::Fenced
                        && space.lifecycle == SpaceLifecycle::FencePending
                })
            {
                return Err(CoreError::new(
                    ErrorCode::PermissionDenied,
                    "user-return fence is not owned by this authority",
                )
                .into());
            }
            Ok((
                pending
                    .old_epoch
                    .unwrap_or_else(|| LeaseEpoch::new(fence_epoch.get().saturating_sub(1))),
                pending.request_token.clone(),
                pending.old_epoch,
            ))
        })?;
        let bridge = self.bridge()?;
        let fence = bridge.fence(space_id, old_epoch, fence_epoch, self.broker_epoch()?);
        match fence {
            Ok(FenceResult { acknowledged: true }) => self.finish_user_return(
                space_id,
                authority,
                released_epoch,
                fence_epoch,
                &fence_token,
                Timestamp::new(0),
            ),
            Ok(FenceResult {
                acknowledged: false,
            }) => {
                self.record_fence_warning(space_id, "user return fence was not acknowledged")?;
                Err(CoreError::new(
                    ErrorCode::ExtensionNotConnected,
                    "user return requires an acknowledged fence",
                )
                .into())
            }
            Err(error) => {
                self.record_fence_warning(space_id, error.message.as_str())?;
                Err(HostError::Bridge(error))
            }
        }
    }

    /// Retry a pending fence without allocating a new epoch.
    pub fn acknowledge_fence(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
    ) -> Result<TakeoverResult, HostError> {
        let (return_pending, fence_token, old_epoch) = self.with_inner(|inner| {
            require_capability(&*inner.bridge, Capability::Action)?;
            authorize_ticket(inner.ledger.state(), authority)?;
            let state = inner.ledger.state();
            let space = state.spaces.get(space_id).ok_or_else(|| {
                CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
            })?;
            let pending = state.pending_fences.get(space_id).ok_or_else(|| {
                CoreError::new(
                    ErrorCode::PermissionDenied,
                    "pending fence request is missing",
                )
            })?;
            if pending.fence_epoch != lease_epoch
                || pending.principal_id != *authority.principal_id()
                || !space.lease.as_ref().is_some_and(|lease| {
                    lease.principal_id == *authority.principal_id()
                        && lease.lease_epoch == lease_epoch
                        && space.lifecycle == SpaceLifecycle::FencePending
                })
            {
                return Err(CoreError::new(
                    ErrorCode::PermissionDenied,
                    "fence acknowledgement is not owned by this authority",
                )
                .into());
            }
            Ok((
                pending.purpose == FencePurpose::ReturnControl,
                pending.request_token.clone(),
                pending.old_epoch,
            ))
        })?;
        let bridge = self.bridge()?;
        let fence = bridge.fence(space_id, old_epoch, lease_epoch, self.broker_epoch()?);
        let (fence_acknowledged, bridge_error) = match fence {
            Ok(FenceResult { acknowledged }) => (acknowledged, None),
            Err(error) => (false, Some(error)),
        };
        let (acknowledged, bridge_error) = if !return_pending && fence_acknowledged {
            match self.rebind_pages_after_fence(space_id, authority, lease_epoch) {
                Ok(()) => (true, None),
                Err(error) => (false, Some(error.as_core_error())),
            }
        } else {
            (fence_acknowledged, bridge_error)
        };
        let lifecycle = if return_pending {
            if acknowledged {
                let released_epoch = old_epoch
                    .unwrap_or_else(|| LeaseEpoch::new(lease_epoch.get().saturating_sub(1)));
                self.finish_user_return(
                    space_id,
                    authority,
                    released_epoch,
                    lease_epoch,
                    &fence_token,
                    Timestamp::new(0),
                )?
                .lifecycle
            } else {
                if let Some(error) = bridge_error {
                    self.record_fence_warning(space_id, error.message.as_str())?;
                }
                SpaceLifecycle::FencePending
            }
        } else {
            self.finish_fence(
                space_id,
                authority,
                lease_epoch,
                &fence_token,
                acknowledged,
                bridge_error,
            )?
        };
        Ok(TakeoverResult {
            space_id: space_id.clone(),
            lease_epoch,
            fence_acknowledged: acknowledged,
            lifecycle,
        })
    }

    /// Rebind retained extension pages before durable takeover ownership is published.
    ///
    /// A fence only revokes the old lease. The extension must separately prove that
    /// each inactive retained tab is still the exact logical page before the new
    /// lease can become actionable. Non-extension test bridges have no browser
    /// binding to rebind and intentionally skip this step.
    fn rebind_pages_after_fence(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
    ) -> Result<(), HostError> {
        let pages = self.with_inner(|inner| {
            authorize_ticket(inner.ledger.state(), authority)?;
            let descriptor = inner.ledger.state().spaces.get(space_id).ok_or_else(|| {
                CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
            })?;
            Ok(descriptor
                .pages
                .iter()
                .filter(|page| {
                    matches!(
                        page.binding,
                        PageBindingState::Bound | PageBindingState::UserOwned
                    ) && !matches!(
                        page.lifecycle,
                        PageLifecycle::Closed | PageLifecycle::Closing
                    )
                })
                .map(|page| {
                    (
                        page.page_id.clone(),
                        page.target_generation.get(),
                        page.navigation_generation.get(),
                        page.document_generation.get(),
                        page.url.clone(),
                        page.title.clone(),
                    )
                })
                .collect::<Vec<_>>())
        })?;
        let bridge = self.bridge()?;
        let Some(extension_epochs) = bridge.extension_epochs() else {
            return Ok(());
        };
        let profile_binding_id = authority
            .profile_binding_id()
            .map(|value| value.as_str().to_owned());
        for (page_id, target_generation, navigation_generation, document_generation, url, title) in
            pages
        {
            let mut proof = json!({
                "issued_by_host": true,
                "proof_id": format!("proof-rebind-{}-{}", page_id, lease_epoch.get()),
                "kind": "rebind",
                "purpose": "rebind",
                "space_id": space_id.to_string(),
                "page_id": page_id.to_string(),
                "lease_epoch": lease_epoch.get(),
                "target_generation": target_generation,
                "navigation_generation": navigation_generation,
                "document_generation": document_generation,
                "rebind": true,
                "expires_at": current_millis().saturating_add(15 * 60 * 1000),
            });
            if let Some(object) = proof.as_object_mut() {
                if let Some(url) = &url {
                    object.insert("url".to_owned(), json!(url));
                }
                if let Some(title) = &title {
                    object.insert("title".to_owned(), json!(title));
                }
                if let Some(profile_binding_id) = &profile_binding_id {
                    object.insert("profile_instance_id".to_owned(), json!(profile_binding_id));
                }
                object.insert(
                    "browser_session_epoch".to_owned(),
                    json!(extension_epochs.browser_session_epoch),
                );
            }
            let result = bridge.rebind_page(
                space_id,
                &page_id,
                lease_epoch,
                target_generation,
                navigation_generation,
                document_generation,
                proof,
            )?;
            let object = result.as_object().ok_or_else(|| {
                CoreError::new(
                    ErrorCode::InvalidJson,
                    "managed page rebind result is not an object",
                )
            })?;
            let valid = object.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
                && object.get("page_id").and_then(Value::as_str) == Some(page_id.as_str())
                && object.get("ownership").and_then(Value::as_str) == Some("agent")
                && object.get("lifecycle").and_then(Value::as_str) == Some("managed")
                && object.get("binding_state").and_then(Value::as_str) == Some("bound")
                && object.get("lease_epoch").and_then(Value::as_u64) == Some(lease_epoch.get())
                && object.get("target_generation").and_then(Value::as_u64)
                    == Some(target_generation)
                && object.get("navigation_generation").and_then(Value::as_u64)
                    == Some(navigation_generation)
                && object.get("document_generation").and_then(Value::as_u64)
                    == Some(document_generation);
            if !valid {
                return Err(CoreError::new(
                    ErrorCode::TargetReplaced,
                    "managed page rebind result does not match the durable generation",
                )
                .into());
            }
        }
        Ok(())
    }

    fn finish_fence(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        fence_token: &ReconcileToken,
        acknowledged: bool,
        bridge_error: Option<CoreError>,
    ) -> Result<SpaceLifecycle, HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                let pending = state.pending_fences.get(space_id).cloned().ok_or_else(|| {
                    CoreError::new(
                        ErrorCode::StaleLease,
                        "fence completion is no longer pending",
                    )
                })?;
                if pending.purpose != FencePurpose::Takeover
                    || pending.request_token != *fence_token
                    || pending.broker_epoch != state.broker_epoch
                    || pending.fence_epoch != lease_epoch
                    || pending.principal_id != *authority.principal_id()
                {
                    return Err(CoreError::new(
                        ErrorCode::StaleLease,
                        "fence completion does not match the pending request",
                    )
                    .into());
                }
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let valid_claim = descriptor.lease.as_ref().is_some_and(|lease| {
                    lease.principal_id == *authority.principal_id()
                        && lease.lease_epoch == lease_epoch
                });
                if !valid_claim {
                    let expected = descriptor
                        .lease
                        .as_ref()
                        .map_or(0, |lease| lease.lease_epoch.get());
                    return Err(CoreError::stale_lease(expected, lease_epoch.get()).into());
                }
                if acknowledged {
                    descriptor.lifecycle = SpaceLifecycle::AgentOwned;
                    for page in &mut descriptor.pages {
                        if page.lifecycle == PageLifecycle::UserOwned {
                            page.lifecycle = PageLifecycle::Managed;
                            page.binding = PageBindingState::Bound;
                        }
                        if page.lifecycle == PageLifecycle::Managed {
                            page.ownership = PageOwnership::Agent;
                        }
                    }
                } else {
                    descriptor.lifecycle = SpaceLifecycle::FencePending;
                    if let Some(error) = bridge_error {
                        descriptor.warnings.push(bounded_warning(&format!(
                            "fence pending: {}",
                            error.code_str()
                        )));
                        if descriptor.warnings.len() > 16 {
                            descriptor.warnings.remove(0);
                        }
                    }
                }
                let lifecycle = descriptor.lifecycle;
                if acknowledged {
                    let previous_epoch = pending.old_epoch.ok_or_else(|| {
                        CoreError::new(
                            ErrorCode::LedgerIncompatible,
                            "takeover fence has no previous lease epoch",
                        )
                    })?;
                    state.pending_fences.remove(space_id);
                    let broker_epoch = state.broker_epoch;
                    let proofs = state.takeover_proofs.entry(space_id.clone()).or_default();
                    proofs.push(TakeoverProofRecord {
                        space_id: space_id.clone(),
                        broker_epoch,
                        previous_epoch,
                        current_epoch: lease_epoch,
                        principal_id: authority.principal_id().clone(),
                        request_token: pending.request_token,
                    });
                    if proofs.len() > 64 {
                        proofs.remove(0);
                    }
                }
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([(
                        "lifecycle",
                        if acknowledged {
                            "agent_owned"
                        } else {
                            "fence_pending"
                        }
                        .to_owned(),
                    )]),
                    None,
                    !acknowledged,
                )?;
                Ok(lifecycle)
            })
        })
    }

    /// Add a planned logical page without launching or discovering a browser.
    pub fn create_page(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        label: impl Into<String>,
    ) -> Result<PageDescriptor, HostError> {
        self.create_page_at(space_id, authority, lease_epoch, label, Timestamp::new(0))
    }

    /// Add a planned logical page with an explicit authorization timestamp.
    pub fn create_page_at(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        label: impl Into<String>,
        now: Timestamp,
    ) -> Result<PageDescriptor, HostError> {
        let label = bounded_label(label.into())?;
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_space(state, space_id, authority, lease_epoch, now, true)?;
                let number = state
                    .next_page_number
                    .checked_add(1)
                    .ok_or_else(|| CoreError::invalid_argument("page identity counter overflow"))?;
                let page_id = PageId::from_suffix(format!("page-{number}"))
                    .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
                state.next_page_number = number;
                create_page_in_state(state, space_id, page_id, label.clone())
            })
        })
    }

    /// Create, claim, and visually present one inactive managed page.
    ///
    /// The browser bridge receives only a host-issued logical proof. The
    /// returned browser inventory is validated before the durable page is
    /// marked managed, and group presentation remains best effort because a
    /// Chrome tab group is visual state rather than authority.
    pub fn create_managed_page(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        label: impl Into<String>,
        now: Timestamp,
        url: Option<&str>,
        title: Option<&str>,
    ) -> Result<PageDescriptor, HostError> {
        let planned = self.create_page_at(space_id, authority, lease_epoch, label, now)?;
        let bridge = self.bridge()?;
        let mut proof = json!({
            "issued_by_host": true,
            "proof_id": format!("proof-create-{}-{}", planned.page_id, lease_epoch.get()),
            "kind": "creation",
            "space_id": space_id.to_string(),
            "page_id": planned.page_id.to_string(),
            "lease_epoch": lease_epoch.get(),
        });
        if let Some(object) = proof.as_object_mut() {
            if let Some(profile_binding_id) = authority.profile_binding_id() {
                object.insert(
                    "profile_instance_id".to_owned(),
                    json!(profile_binding_id.as_str()),
                );
            }
            if let Some(epochs) = bridge.extension_epochs() {
                object.insert(
                    "browser_session_epoch".to_owned(),
                    json!(epochs.browser_session_epoch),
                );
            }
        }
        let created_record = bridge
            .create_page(space_id, &planned.page_id, lease_epoch, url, title, proof)
            .map_err(HostError::Bridge)?;
        // Page creation can return before Chrome emits the final navigation
        // update. Refresh the extension-backed logical record so the durable
        // ledger adopts the current navigation/document generations rather than
        // racing the first load and producing a false target-replaced result.
        let record = bridge
            .observe()
            .map_err(HostError::Bridge)?
            .pages
            .into_iter()
            .find(|value| {
                value.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
                    && value.get("page_id").and_then(Value::as_str)
                        == Some(planned.page_id.as_str())
            })
            .unwrap_or(created_record);
        let object = record.as_object().ok_or_else(|| {
            HostError::Core(CoreError::new(
                ErrorCode::InvalidJson,
                "managed page bridge result is not an object",
            ))
        })?;
        if object.get("space_id").and_then(Value::as_str) != Some(space_id.as_str())
            || object.get("page_id").and_then(Value::as_str) != Some(planned.page_id.as_str())
        {
            return Err(HostError::Core(CoreError::new(
                ErrorCode::TargetReplaced,
                "managed page bridge result has the wrong logical scope",
            )));
        }
        let frame_count = object
            .get("frame_count")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok())
            .unwrap_or(0);
        let observed_generation = |field: &str| {
            object
                .get(field)
                .and_then(Value::as_u64)
                .filter(|value| *value > 0)
                .ok_or_else(|| {
                    HostError::Core(CoreError::new(
                        ErrorCode::TargetReplaced,
                        format!("managed page bridge result is missing {field}"),
                    ))
                })
        };
        let bound = self.bind_page_with_generations(
            space_id,
            &planned.page_id,
            authority,
            lease_epoch,
            now,
            object.get("url").and_then(Value::as_str).map(str::to_owned),
            object
                .get("title")
                .and_then(Value::as_str)
                .map(str::to_owned),
            frame_count,
            Some(observed_generation("target_generation")?),
            Some(observed_generation("navigation_generation")?),
            Some(observed_generation("document_generation")?),
        )?;
        let _ = bridge.present_group(space_id, &planned.page_id, lease_epoch, title);
        Ok(bound)
    }

    /// Mark a planned page as logically managed; no raw bridge handle is stored.
    #[allow(clippy::too_many_arguments)]
    pub fn bind_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        url: Option<String>,
        title: Option<String>,
        frame_count: u32,
    ) -> Result<PageDescriptor, HostError> {
        self.bind_page_with_generations(
            space_id,
            page_id,
            authority,
            lease_epoch,
            now,
            url,
            title,
            frame_count,
            None,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn bind_page_with_generations(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        url: Option<String>,
        title: Option<String>,
        frame_count: u32,
        target_generation: Option<u64>,
        navigation_generation: Option<u64>,
        document_generation: Option<u64>,
    ) -> Result<PageDescriptor, HostError> {
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                authorize_space(state, space_id, authority, lease_epoch, now, true)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let page = descriptor.page_mut(page_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                })?;
                if !matches!(
                    page.lifecycle,
                    PageLifecycle::Planned
                        | PageLifecycle::TargetLost
                        | PageLifecycle::Rebinding
                        | PageLifecycle::Adoptable
                ) {
                    return Err(CoreError::new(
                        ErrorCode::TargetReplaced,
                        "page lifecycle does not permit binding",
                    )
                    .into());
                }
                page.lifecycle = PageLifecycle::Managed;
                page.ownership = PageOwnership::Agent;
                page.binding = PageBindingState::Bound;
                page.target_generation = match target_generation {
                    Some(value) if value > 0 => Generation::new(value),
                    Some(_) => {
                        return Err(CoreError::invalid_argument(
                            "target generation must be positive",
                        )
                        .into());
                    }
                    None => page
                        .target_generation
                        .checked_next()
                        .ok_or_else(|| CoreError::invalid_argument("target generation overflow"))?,
                };
                page.navigation_generation = match navigation_generation {
                    Some(value) if value > 0 => Generation::new(value),
                    Some(_) => {
                        return Err(CoreError::invalid_argument(
                            "navigation generation must be positive",
                        )
                        .into());
                    }
                    None => page.navigation_generation.checked_next().ok_or_else(|| {
                        CoreError::invalid_argument("navigation generation overflow")
                    })?,
                };
                page.document_generation = match document_generation {
                    Some(value) if value > 0 => Generation::new(value),
                    Some(_) => {
                        return Err(CoreError::invalid_argument(
                            "document generation must be positive",
                        )
                        .into());
                    }
                    None => page.document_generation.checked_next().ok_or_else(|| {
                        CoreError::invalid_argument("document generation overflow")
                    })?,
                };
                page.url = bounded_optional(url)?;
                page.title = bounded_optional(title)?;
                page.frame_count = frame_count;
                let result = page.clone();
                state
                    .snapshots
                    .get_mut(space_id)
                    .map(|pages| pages.remove(page_id));
                append_event(
                    state,
                    EventScope::page(space_id.clone(), page_id.clone()),
                    EventKind::PageChanged,
                    payload([
                        ("lifecycle", "managed".to_owned()),
                        ("binding", "bound".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })
    }

    /// Mark a logical page target lost without closing anything in the browser.
    pub fn mark_page_lost(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<PageDescriptor, HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_space(state, space_id, authority, lease_epoch, now, false)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let page = descriptor.page_mut(page_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                })?;
                bump_page_generations(page)?;
                page.lifecycle = PageLifecycle::TargetLost;
                page.binding = PageBindingState::Lost;
                let result = page.clone();
                state.snapshots.remove(space_id);
                append_event(
                    state,
                    EventScope::page(space_id.clone(), page_id.clone()),
                    EventKind::PageChanged,
                    payload([("lifecycle", "target_lost".to_owned())]),
                    None,
                    true,
                )?;
                Ok(result)
            })
        })
    }

    /// Explicitly close one logical page after a current lease check.
    pub fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<PageDescriptor, HostError> {
        let generation = self.with_inner(|inner| {
            ensure_ready(inner)?;
            require_capability(&*inner.bridge, Capability::Action)?;
            inner.ledger.update(|state| {
                let generation =
                    authorize_page(state, space_id, page_id, authority, lease_epoch, now, true)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let page = descriptor.page_mut(page_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                })?;
                page.lifecycle = PageLifecycle::Closing;
                append_event(
                    state,
                    EventScope::page(space_id.clone(), page_id.clone()),
                    EventKind::PageChanged,
                    payload([("lifecycle", "closing".to_owned())]),
                    None,
                    false,
                )?;
                Ok(generation)
            })
        })?;
        let bridge = self.bridge()?;
        if let Err(error) = bridge.close_page(space_id, page_id, lease_epoch) {
            let _ = self.mark_page_lost(space_id, page_id, authority, lease_epoch, now);
            return Err(HostError::Bridge(error));
        }
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_space(state, space_id, authority, lease_epoch, now, true)?;
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let page = descriptor.page_mut(page_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                })?;
                if page.lifecycle != PageLifecycle::Closing || !generation.matches_page(page) {
                    return Err(CoreError::new(
                        ErrorCode::TargetReplaced,
                        "page changed while close was in flight",
                    )
                    .into());
                }
                let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                let page = descriptor.page_mut(page_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                })?;
                bump_page_generations(page)?;
                page.lifecycle = PageLifecycle::Closed;
                page.ownership = PageOwnership::Broker;
                page.binding = PageBindingState::Closed;
                let result = page.clone();
                if let Some(pages) = state.snapshots.get_mut(space_id) {
                    pages.remove(page_id);
                }
                append_event(
                    state,
                    EventScope::page(space_id.clone(), page_id.clone()),
                    EventKind::PageChanged,
                    payload([("lifecycle", "closed".to_owned())]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })
    }

    /// Finish an agent-owned space after explicitly closing each managed agent page.
    ///
    /// The transition first persists `Draining`, so no new space or page mutation
    /// can be admitted while bridge cleanup is in flight. Only pages with a
    /// current managed binding are sent to [`Bridge::close_page`]. A bridge error
    /// is treated as an unknown cleanup outcome and leaves the space fenced in
    /// `Draining` rather than retrying the close implicitly.
    pub fn finish_space(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<SpaceDescriptor, HostError> {
        let bridge = self.bridge()?;
        require_capability(&*bridge, Capability::Action)?;
        let observation = bridge.observe().map_err(HostError::Bridge)?;
        self.reconcile_missing_cleanup_pages(space_id, authority, lease_epoch, now, &observation)?;
        let cleanup = self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                let allow_draining = state
                    .spaces
                    .get(space_id)
                    .is_some_and(|space| space.lifecycle == SpaceLifecycle::Draining);
                authorize_finish_claim(
                    state,
                    space_id,
                    authority,
                    lease_epoch,
                    now,
                    allow_draining,
                )?;
                let (cleanup, retention) = {
                    let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    // A prior finish can stop after marking a page Closing.
                    // Re-entering finish with the current lease is the bounded
                    // recovery path; it never blindly replays a browser close
                    // because each attempt obtains a fresh bridge proof.
                    let cleanup = descriptor
                        .pages
                        .iter_mut()
                        .filter(|page| {
                            page.admits_agent_mutations()
                                || (allow_draining && page.lifecycle == PageLifecycle::Closing)
                        })
                        .map(|page| {
                            page.lifecycle = PageLifecycle::Closing;
                            CleanupProof {
                                page_id: page.page_id.clone(),
                                generation: PageGeneration::from_page(page),
                            }
                        })
                        .collect::<Vec<_>>();
                    descriptor.lifecycle = SpaceLifecycle::Draining;
                    (cleanup, descriptor.retention)
                };
                state.snapshots.remove(space_id);
                for proof in &cleanup {
                    append_event(
                        state,
                        EventScope::page(space_id.clone(), proof.page_id.clone()),
                        EventKind::PageChanged,
                        payload([("lifecycle", "closing".to_owned())]),
                        None,
                        false,
                    )?;
                }
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", "draining".to_owned())]),
                    None,
                    false,
                )?;
                Ok((cleanup, retention))
            })
        })?;
        let (cleanup, retention) = cleanup;

        for proof in cleanup {
            self.with_inner(|inner| {
                ensure_ready(inner)?;
                inner.ledger.update(|state| {
                    authorize_cleanup_page(state, space_id, &proof, authority, lease_epoch, now)
                })
            })?;
            match bridge.close_page(space_id, &proof.page_id, lease_epoch) {
                Ok(()) => {
                    self.finish_page_cleanup(space_id, &proof, authority, lease_epoch, now)?
                }
                Err(error) => {
                    // A prior cleanup may have closed the tab before its
                    // response was lost. A fresh bridge inventory is the only
                    // accepted proof for completing that already-Closing page;
                    // never infer absence from the error alone.
                    if matches!(
                        error.code,
                        ErrorCode::Timeout
                            | ErrorCode::UnknownOutcome
                            | ErrorCode::NativeHostUnavailable
                    ) {
                        self.record_cleanup_failure(
                            space_id,
                            &proof,
                            authority,
                            lease_epoch,
                            now,
                            &error,
                        )?;
                        return Err(HostError::Bridge(error));
                    }
                    let observation = bridge.observe().map_err(HostError::Bridge)?;
                    let still_present = observation.pages.iter().any(|record| {
                        record.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
                            && record.get("page_id").and_then(Value::as_str)
                                == Some(proof.page_id.as_str())
                    });
                    if !still_present {
                        self.finish_page_cleanup(space_id, &proof, authority, lease_epoch, now)?;
                        continue;
                    }
                    self.record_cleanup_failure(
                        space_id,
                        &proof,
                        authority,
                        lease_epoch,
                        now,
                        &error,
                    )?;
                    return Err(HostError::Bridge(error));
                }
            }
        }

        self.complete_finish(space_id, authority, lease_epoch, now, retention)
    }

    fn reconcile_missing_cleanup_pages(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        observation: &ObservationSnapshot,
    ) -> Result<(), HostError> {
        let proofs = self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_finish_claim(state, space_id, authority, lease_epoch, now, true)?;
                let descriptor = state.spaces.get(space_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                })?;
                Ok(descriptor
                    .pages
                    .iter()
                    .filter(|page| page.lifecycle == PageLifecycle::Closing)
                    .filter(|page| {
                        !observation.pages.iter().any(|record| {
                            record.get("space_id").and_then(Value::as_str)
                                == Some(space_id.as_str())
                                && record.get("page_id").and_then(Value::as_str)
                                    == Some(page.page_id.as_str())
                        })
                    })
                    .map(|page| CleanupProof {
                        page_id: page.page_id.clone(),
                        generation: PageGeneration::from_page(page),
                    })
                    .collect::<Vec<_>>())
            })
        })?;
        for proof in proofs {
            self.finish_page_cleanup(space_id, &proof, authority, lease_epoch, now)?;
        }
        Ok(())
    }

    /// Release a finished space after checking the current authority and lease.
    ///
    /// This method never crosses the bridge boundary. Any managed or closing
    /// page means cleanup was not proven by [`Broker::finish_space`], so release
    /// fails closed instead of performing an implicit or global close.
    pub fn release_space(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<SpaceDescriptor, HostError> {
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_release_claim(state, space_id, authority, lease_epoch)?;
                let (retention, already_released) = {
                    let descriptor = state.spaces.get(space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    (
                        descriptor.retention,
                        descriptor.lifecycle == SpaceLifecycle::Released,
                    )
                };
                if already_released {
                    return state.spaces.get(space_id).cloned().ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found").into()
                    });
                }
                if let RetentionPolicy::Until { at } = retention
                    && now.get() < at.get()
                {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "retention period has not expired",
                    )
                    .into());
                }
                let has_unresolved_cleanup = state.spaces.get(space_id).is_some_and(|descriptor| {
                    descriptor.pages.iter().any(|page| {
                        page.admits_agent_mutations() || page.lifecycle == PageLifecycle::Closing
                    })
                });
                if has_unresolved_cleanup {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "space has unresolved page cleanup; finish it first",
                    )
                    .into());
                }
                let result = {
                    let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    descriptor.lifecycle = SpaceLifecycle::Released;
                    descriptor.clone()
                };
                state.snapshots.remove(space_id);
                state.takeover_proofs.remove(space_id);
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", "released".to_owned())]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })
    }

    fn finish_page_cleanup(
        &self,
        space_id: &SpaceId,
        proof: &CleanupProof,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<(), HostError> {
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_cleanup_page(state, space_id, proof, authority, lease_epoch, now)?;
                let page = state
                    .spaces
                    .get_mut(space_id)
                    .and_then(|space| space.page_mut(&proof.page_id))
                    .ok_or_else(|| {
                        CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                    })?;
                bump_page_generations(page)?;
                page.lifecycle = PageLifecycle::Closed;
                page.ownership = PageOwnership::Broker;
                page.binding = PageBindingState::Closed;
                if let Some(pages) = state.snapshots.get_mut(space_id) {
                    pages.remove(&proof.page_id);
                }
                append_event(
                    state,
                    EventScope::page(space_id.clone(), proof.page_id.clone()),
                    EventKind::PageChanged,
                    payload([("lifecycle", "closed".to_owned())]),
                    None,
                    false,
                )?;
                Ok(())
            })
        })
    }

    fn record_cleanup_failure(
        &self,
        space_id: &SpaceId,
        proof: &CleanupProof,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        error: &CoreError,
    ) -> Result<(), HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_cleanup_page(state, space_id, proof, authority, lease_epoch, now)?;
                let page = state
                    .spaces
                    .get_mut(space_id)
                    .and_then(|space| space.page_mut(&proof.page_id))
                    .ok_or_else(|| {
                        CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                    })?;
                bump_page_generations(page)?;
                page.lifecycle = PageLifecycle::TargetLost;
                page.binding = PageBindingState::Lost;
                page.ownership = PageOwnership::Agent;
                if let Some(pages) = state.snapshots.get_mut(space_id) {
                    pages.remove(&proof.page_id);
                }
                let warning = bounded_warning(&format!(
                    "page cleanup outcome unknown: {}",
                    error.code_str()
                ));
                if let Some(descriptor) = state.spaces.get_mut(space_id) {
                    descriptor.warnings.push(warning);
                    if descriptor.warnings.len() > 16 {
                        descriptor.warnings.remove(0);
                    }
                }
                append_event(
                    state,
                    EventScope::page(space_id.clone(), proof.page_id.clone()),
                    EventKind::PageChanged,
                    payload([("lifecycle", "target_lost".to_owned())]),
                    None,
                    true,
                )?;
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", "draining".to_owned())]),
                    None,
                    true,
                )?;
                Ok(())
            })
        })
    }

    fn complete_finish(
        &self,
        space_id: &SpaceId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        retention: RetentionPolicy,
    ) -> Result<SpaceDescriptor, HostError> {
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_finish_claim(state, space_id, authority, lease_epoch, now, true)?;
                let lifecycle = if retention == RetentionPolicy::ReleaseOnFinish {
                    SpaceLifecycle::Released
                } else {
                    SpaceLifecycle::Finished
                };
                let result = {
                    let descriptor = state.spaces.get_mut(space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    if descriptor.pages.iter().any(|page| {
                        page.lifecycle == PageLifecycle::Closing || page.admits_agent_mutations()
                    }) {
                        return Err(CoreError::new(
                            ErrorCode::PermissionDenied,
                            "space has unresolved page cleanup",
                        )
                        .into());
                    }
                    let lease = descriptor.lease.as_mut().ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceForbidden, "space has no current lease")
                    })?;
                    lease.state = LeaseState::Released;
                    descriptor.lifecycle = lifecycle;
                    descriptor.clone()
                };
                state.snapshots.remove(space_id);
                state.takeover_proofs.remove(space_id);
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::LeaseChanged,
                    payload([
                        ("lease_epoch", lease_epoch.get().to_string()),
                        ("state", "released".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                append_event(
                    state,
                    EventScope::space(space_id.clone()),
                    EventKind::SpaceChanged,
                    payload([("lifecycle", space_lifecycle_name(lifecycle).to_owned())]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })
    }

    /// Admit a durable action after checking principal, space, page, epoch, and idempotency.
    pub fn enqueue_action(
        &self,
        request: ActionRequest<BTreeMap<String, String>>,
        authority: &AuthorityTicket,
        now: Timestamp,
    ) -> Result<ActionReceipt, HostError> {
        validate_public_payload(&request.payload)?;
        validate_action_payload_contract(request.operation, &request.payload)
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        let expected_hash = ledger_action_hash(&request).map_err(HostError::Ledger)?;
        if request.request_hash != expected_hash {
            return Err(CoreError::invalid_argument(
                "request hash does not match the complete canonical request context",
            )
            .into());
        }
        self.with_inner(|inner| {
            if inner.lifecycle != HostLifecycle::Ready {
                return Err(CoreError::new(
                    ErrorCode::HostDraining,
                    "host is not accepting new work",
                )
                .into());
            }
            require_operation_capabilities(&*inner.bridge, request.operation)?;
            let max_queued_actions = inner.ledger.limits().max_queued_actions_per_space;
            inner.ledger.update(|state| {
                authorize_space(
                    state,
                    &request.space_id,
                    authority,
                    request.lease_epoch,
                    now,
                    true,
                )?;
                if let Some(existing_id) = state.idempotency.get(&request.idempotency_key) {
                    let existing_request =
                        state.action_requests.get(existing_id).ok_or_else(|| {
                            CoreError::new(
                                ErrorCode::LedgerIncompatible,
                                "idempotency index is incomplete",
                            )
                        })?;
                    if existing_request.request_hash != request.request_hash {
                        return Err(CoreError::invalid_argument(
                            "idempotency key conflicts with a different request hash",
                        )
                        .into());
                    }
                    return state.actions.get(existing_id).cloned().ok_or_else(|| {
                        CoreError::new(ErrorCode::LedgerIncompatible, "action receipt is missing")
                            .into()
                    });
                }
                if let Some(existing) = state.actions.get(&request.action_id) {
                    if existing.request_hash == request.request_hash {
                        return Ok(existing.clone());
                    }
                    return Err(CoreError::invalid_argument(
                        "action ID conflicts with a different request",
                    )
                    .into());
                }
                if let Some(page_id) = &request.page_id {
                    let space = state.spaces.get(&request.space_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::SpaceNotFound, "logical space not found")
                    })?;
                    let page = space.page(page_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::PageNotFound, "logical page not found")
                    })?;
                    if !page.admits_agent_mutations() {
                        return Err(CoreError::new(
                            ErrorCode::PageNotOwned,
                            "page is not managed by the lease",
                        )
                        .into());
                    }
                } else if request.operation == ActionOperation::Close {
                    return Err(CoreError::invalid_argument("close requires a logical page").into());
                }
                let queue = state
                    .action_queues
                    .entry(request.space_id.clone())
                    .or_default();
                if queue.len() >= max_queued_actions {
                    return Err(CoreError::new(
                        ErrorCode::MessageTooLarge,
                        "space action queue is full",
                    )
                    .into());
                }
                let receipt = ActionReceipt::queued(
                    request.action_id.clone(),
                    request.request_id.clone(),
                    request.idempotency_key.clone(),
                    request.request_hash.clone(),
                    request.space_id.clone(),
                    request.page_id.clone(),
                    request.lease_epoch,
                    request.operation,
                    request.postcondition.clone(),
                    Some(now),
                );
                state
                    .idempotency
                    .insert(request.idempotency_key.clone(), request.action_id.clone());
                state
                    .action_requests
                    .insert(request.action_id.clone(), request);
                state
                    .actions
                    .insert(receipt.action_id.clone(), receipt.clone());
                queue.push(receipt.action_id.clone());
                append_event(
                    state,
                    EventScope::space(receipt.space_id.clone()),
                    EventKind::ActionChanged,
                    payload([
                        ("action_id", receipt.action_id.to_string()),
                        ("status", "queued".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok(receipt)
            })
        })
    }

    /// Execute the next queued action after three-point lease fencing.
    pub fn dispatch_action(
        &self,
        action_id: &agentyc_core::ActionId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<ActionResult, HostError> {
        let request = self.with_inner(|inner| {
            ensure_ready(inner)?;
            let request = inner.ledger.state().action_requests.get(action_id).cloned();
            if let Some(request) = &request {
                require_operation_capabilities(&*inner.bridge, request.operation)?;
            }
            inner.ledger.update(|state| {
                let request = state
                    .action_requests
                    .get(action_id)
                    .cloned()
                    .ok_or_else(|| CoreError::invalid_argument("action request is missing"))?;
                let current_status = state
                    .actions
                    .get(action_id)
                    .ok_or_else(|| {
                        CoreError::new(ErrorCode::LedgerIncompatible, "action receipt is missing")
                    })?
                    .status;
                match current_status {
                    ActionStatus::Succeeded | ActionStatus::Failed | ActionStatus::Cancelled => {
                        return Ok(None);
                    }
                    ActionStatus::Unknown => {
                        return Err(CoreError::new(
                            ErrorCode::ReconciliationRequired,
                            "unknown action must be reconciled before dispatch",
                        )
                        .into());
                    }
                    ActionStatus::Running => {
                        return Err(CoreError::invalid_argument("action is already running").into());
                    }
                    ActionStatus::Queued => {}
                }
                if lease_epoch != request.lease_epoch {
                    return Err(CoreError::stale_lease(
                        request.lease_epoch.get(),
                        lease_epoch.get(),
                    )
                    .into());
                }
                authorize_space(state, &request.space_id, authority, lease_epoch, now, true)?;
                let queue = state
                    .action_queues
                    .get_mut(&request.space_id)
                    .ok_or_else(|| {
                        CoreError::new(
                            ErrorCode::LedgerIncompatible,
                            "queued action has no space queue",
                        )
                    })?;
                if queue.first() != Some(action_id) {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "actions must dispatch in per-space FIFO order",
                    )
                    .into());
                }
                if is_mutating(request.operation)
                    && state.actions.values().any(|receipt| {
                        receipt.space_id == request.space_id
                            && receipt.status == ActionStatus::Running
                            && is_mutating(receipt.operation)
                    })
                {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "a mutating action is already in flight for this space",
                    )
                    .into());
                }
                let _ = queue.remove(0);
                let receipt = state.actions.get_mut(action_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::LedgerIncompatible, "action receipt is missing")
                })?;
                receipt.mark_dispatched()?;
                append_event(
                    state,
                    EventScope::space(request.space_id.clone()),
                    EventKind::ActionChanged,
                    payload([
                        ("action_id", action_id.to_string()),
                        ("status", "running".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok(Some(request))
            })
        })?;
        let Some(request) = request else {
            return self
                .action_status(authority, action_id)
                .map(|receipt| ActionResult { receipt });
        };

        // Recheck the current lease immediately before the bridge call. A takeover
        // between dequeue and dispatch turns the receipt unknown and never replays it.
        self.with_inner(|inner| {
            ensure_ready(inner)?;
            inner.ledger.update(|state| {
                authorize_space(state, &request.space_id, authority, lease_epoch, now, true)?;
                let receipt = state.actions.get(action_id).ok_or_else(|| {
                    CoreError::new(ErrorCode::LedgerIncompatible, "action receipt is missing")
                })?;
                if receipt.status != ActionStatus::Running {
                    return Err(CoreError::new(
                        ErrorCode::ReconciliationRequired,
                        "action was fenced before dispatch",
                    )
                    .into());
                }
                Ok(())
            })
        })?;

        let bridge = self.bridge()?;
        let outcome = match bridge.dispatch(&request) {
            Ok(outcome) => outcome,
            Err(_) => BridgeDispatchResult::Unknown {
                reason: UnknownReason::BridgeLost,
            },
        };
        let receipt = self.finish_dispatch(action_id, request, authority, outcome, now)?;
        Ok(ActionResult { receipt })
    }

    /// Enqueue and then dispatch one action through the bridge seam.
    pub fn execute_action(
        &self,
        request: ActionRequest<BTreeMap<String, String>>,
        authority: &AuthorityTicket,
        now: Timestamp,
    ) -> Result<ActionResult, HostError> {
        let receipt = self.enqueue_action(request, authority, now)?;
        if receipt.status != ActionStatus::Queued {
            return Ok(ActionResult { receipt });
        }
        self.dispatch_action(&receipt.action_id, authority, receipt.lease_epoch, now)
    }

    fn finish_dispatch(
        &self,
        action_id: &agentyc_core::ActionId,
        request: ActionRequest<BTreeMap<String, String>>,
        authority: &AuthorityTicket,
        outcome: BridgeDispatchResult,
        now: Timestamp,
    ) -> Result<ActionReceipt, HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let lease_current = inner.lifecycle == HostLifecycle::Ready
                    && authority_is_current(state, authority)
                    && state.spaces.get(&request.space_id).is_some_and(|space| {
                        space.lifecycle.admits_mutations()
                            && space.lease.as_ref().is_some_and(|lease| {
                                lease.principal_id == *authority.principal_id()
                                    && lease.lease_epoch == request.lease_epoch
                                    && lease.state == LeaseState::Active
                                    && lease.expires_at.get() > now.get()
                            })
                    });
                let outcome = if lease_current {
                    outcome
                } else {
                    BridgeDispatchResult::Unknown {
                        reason: UnknownReason::FenceInterrupted,
                    }
                };
                let (updated, status, unknown) = {
                    let receipt = state.actions.get_mut(action_id).ok_or_else(|| {
                        CoreError::new(ErrorCode::LedgerIncompatible, "action receipt is missing")
                    })?;
                    if receipt.status != ActionStatus::Running {
                        return Ok(receipt.clone());
                    }
                    match outcome {
                        BridgeDispatchResult::Succeeded => {
                            receipt.mark_succeeded(Some(now), CompletionSource::Extension)?;
                        }
                        BridgeDispatchResult::Failed { code, retryable } => {
                            receipt.mark_failed(code, retryable, Some(now))?;
                        }
                        BridgeDispatchResult::Unknown { reason } => {
                            receipt.mark_unknown(
                                reason,
                                reconcile_token("action", action_sequence(action_id))?,
                            )?;
                        }
                    }
                    (receipt.clone(), receipt.status, receipt.unknown)
                };
                let requires_dirty = is_mutating(request.operation);
                if requires_dirty
                    && let Some(page_id) = &request.page_id
                    && let Some(pages) = state.snapshots.get_mut(&request.space_id)
                    && let Some(snapshot) = pages.get_mut(page_id)
                {
                    snapshot.dirty = true;
                }
                append_event(
                    state,
                    EventScope::space(request.space_id),
                    EventKind::ActionChanged,
                    payload([
                        ("action_id", action_id.to_string()),
                        ("status", status_name(status)),
                    ]),
                    Some(DirtyReason::Action),
                    unknown,
                )?;
                Ok(updated)
            })
        })
    }

    /// Return a bounded action receipt without replaying or dispatching it.
    pub fn action_status(
        &self,
        authority: &AuthorityTicket,
        action_id: &agentyc_core::ActionId,
    ) -> Result<ActionReceipt, HostError> {
        self.with_inner(|inner| {
            let state = inner.ledger.state();
            authorize_ticket(state, authority)?;
            let receipt = state
                .actions
                .get(action_id)
                .ok_or_else(|| CoreError::invalid_argument("action receipt is missing"))?;
            authorize_visible_space(state, authority, &receipt.space_id)?;
            Ok(receipt.clone())
        })
    }

    /// Reconcile an unknown action using a bridge read; never re-dispatches it.
    pub fn reconcile_action(
        &self,
        action_id: &agentyc_core::ActionId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<ActionResult, HostError> {
        let bridge = self.bridge()?;
        require_capability(&*bridge, Capability::Reconcile)?;
        let receipt = self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let existing = authorize_reconciliation_authority(
                    state,
                    action_id,
                    authority,
                    lease_epoch,
                    now,
                )?;
                let receipt = state
                    .actions
                    .get_mut(action_id)
                    .ok_or_else(|| CoreError::invalid_argument("action receipt is missing"))?;
                if receipt.status != ActionStatus::Unknown {
                    return Err(CoreError::new(
                        ErrorCode::ReconciliationRequired,
                        "action does not have an unknown outcome",
                    )
                    .into());
                }
                if receipt.request_hash != existing.request_hash {
                    return Err(CoreError::new(
                        ErrorCode::LedgerIncompatible,
                        "reconciliation request context changed",
                    )
                    .into());
                }
                receipt.begin_reconciliation()?;
                let result = receipt.clone();
                append_event(
                    state,
                    EventScope::space(result.space_id.clone()),
                    EventKind::ActionChanged,
                    payload([
                        ("action_id", action_id.to_string()),
                        ("status", "reconciling".to_owned()),
                    ]),
                    None,
                    false,
                )?;
                Ok(result)
            })
        })?;
        let result = bridge.reconcile(&receipt);
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let lease_current = inner.lifecycle == HostLifecycle::Ready
                    && authority_is_current(state, authority)
                    && authorize_reconciliation_authority(
                        state,
                        action_id,
                        authority,
                        lease_epoch,
                        now,
                    )
                    .is_ok();
                let current = state
                    .actions
                    .get_mut(action_id)
                    .ok_or_else(|| CoreError::invalid_argument("action receipt is missing"))?;
                if current.status != ActionStatus::Unknown {
                    return Ok(current.clone());
                }
                if !lease_current {
                    current.reconciliation_state = ReconciliationState::Required;
                    current.next_action = NextAction::Reconcile;
                } else {
                    match result {
                        Ok(BridgeReconcileResult::Succeeded) => {
                            current.reconcile_succeeded(Some(now))?;
                        }
                        Ok(BridgeReconcileResult::Failed {
                            code,
                            requires_confirmation,
                        }) => {
                            current.reconcile_failed(code, requires_confirmation, Some(now))?;
                        }
                        Ok(BridgeReconcileResult::StillUnknown) | Err(_) => {
                            current.reconciliation_state = ReconciliationState::Required;
                            current.next_action = NextAction::Reconcile;
                        }
                    }
                }
                let result = current.clone();
                append_event(
                    state,
                    EventScope::space(result.space_id.clone()),
                    EventKind::ActionChanged,
                    payload([
                        ("action_id", action_id.to_string()),
                        ("status", status_name(result.status)),
                    ]),
                    None,
                    result.status == ActionStatus::Unknown,
                )?;
                Ok(result)
            })
        })
        .map(|receipt| ActionResult { receipt })
    }

    /// Adopt monotonic page generations observed by the trusted extension bridge.
    ///
    /// Chrome navigation/document events are faster than the host's next client
    /// request. Updating the logical ledger from a sanitized inventory prevents
    /// a valid post-navigation snapshot/action from being rejected merely because
    /// the host still holds the previous generation. Observations can only move
    /// generations forward and never grant ownership or lease authority.
    fn reconcile_bridge_pages(&self, observation: &ObservationSnapshot) -> Result<(), HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let mut changed = Vec::new();
                for record in &observation.pages {
                    let Some(space_id) = record.get("space_id").and_then(Value::as_str) else {
                        continue;
                    };
                    let Some(page_id) = record.get("page_id").and_then(Value::as_str) else {
                        continue;
                    };
                    let Ok(space_id) = space_id.parse::<SpaceId>() else {
                        continue;
                    };
                    let Ok(page_id) = page_id.parse::<PageId>() else {
                        continue;
                    };
                    let Some(target_generation) = record
                        .get("target_generation")
                        .and_then(Value::as_u64)
                        .filter(|value| *value > 0)
                    else {
                        continue;
                    };
                    let Some(navigation_generation) = record
                        .get("navigation_generation")
                        .and_then(Value::as_u64)
                        .filter(|value| *value > 0)
                    else {
                        continue;
                    };
                    let Some(document_generation) = record
                        .get("document_generation")
                        .and_then(Value::as_u64)
                        .filter(|value| *value > 0)
                    else {
                        continue;
                    };
                    let Some(space) = state.spaces.get_mut(&space_id) else {
                        continue;
                    };
                    let Some(page) = space.page_mut(&page_id) else {
                        continue;
                    };
                    if page.ownership != PageOwnership::Agent
                        || page.binding != PageBindingState::Bound
                        || !matches!(page.lifecycle, PageLifecycle::Managed)
                    {
                        continue;
                    }
                    if target_generation < page.target_generation.get()
                        || navigation_generation < page.navigation_generation.get()
                        || document_generation < page.document_generation.get()
                    {
                        continue;
                    }
                    let changed_generation = target_generation > page.target_generation.get()
                        || navigation_generation > page.navigation_generation.get()
                        || document_generation > page.document_generation.get();
                    if !changed_generation {
                        continue;
                    }
                    page.target_generation = Generation::new(target_generation);
                    page.navigation_generation = Generation::new(navigation_generation);
                    page.document_generation = Generation::new(document_generation);
                    if let Some(url) = record.get("url").and_then(Value::as_str) {
                        page.url = bounded_optional(Some(url.to_owned()))?;
                    }
                    if let Some(title) = record.get("title").and_then(Value::as_str) {
                        page.title = bounded_optional(Some(title.to_owned()))?;
                    }
                    state
                        .snapshots
                        .entry(space_id.clone())
                        .or_default()
                        .remove(&page_id);
                    changed.push((space_id, page_id));
                }
                for (space_id, page_id) in changed {
                    append_event(
                        state,
                        EventScope::page(space_id, page_id),
                        EventKind::PageChanged,
                        payload([("source", "bridge_observation".to_owned())]),
                        None,
                        false,
                    )?;
                }
                Ok(())
            })
        })
    }

    /// Read a clean cached snapshot or perform exactly one bridge scan on a miss/dirty entry.
    pub fn read_snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<SnapshotRead, HostError> {
        let bridge = self.bridge()?;
        require_capability(&*bridge, Capability::Snapshot)?;
        let live_observation = bridge.observe().map_err(HostError::Bridge)?;
        let live_observation = sanitize_observation_snapshot(live_observation)?;
        self.reconcile_bridge_pages(&live_observation)?;
        let (cached, generation) = self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let generation =
                    authorize_page(state, space_id, page_id, authority, lease_epoch, now, false)?;
                if let Some(record) = state
                    .snapshots
                    .get(space_id)
                    .and_then(|pages| pages.get(page_id))
                    && !record.dirty
                    && record.generation == generation
                {
                    let mut envelope = record.envelope.clone();
                    envelope.cache_state = agentyc_core::CacheState::Cached;
                    return Ok((
                        Some(SnapshotRead {
                            envelope,
                            cache_state: agentyc_core::CacheState::Cached,
                            scan_performed: false,
                        }),
                        generation,
                    ));
                }
                Ok((None, generation))
            })
        })?;
        if let Some(cached) = cached {
            return Ok(cached);
        }
        let envelope = bridge
            .snapshot(space_id, page_id, lease_epoch)
            .map_err(HostError::Bridge)?;
        envelope
            .validate()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        if envelope.space_id != *space_id || envelope.page_id != *page_id {
            return Err(CoreError::new(
                ErrorCode::InvalidArgument,
                "bridge snapshot scope mismatch",
            )
            .into());
        }
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let current_generation =
                    authorize_page(state, space_id, page_id, authority, lease_epoch, now, false)?;
                if current_generation != generation
                    || envelope.navigation_generation != current_generation.navigation_generation
                    || envelope.document_generation != current_generation.document_generation
                {
                    return Err(CoreError::new(
                        ErrorCode::TargetReplaced,
                        "page generation changed while snapshot was in flight",
                    )
                    .into());
                }
                let mut fresh = envelope.clone();
                fresh.cache_state = agentyc_core::CacheState::Fresh;
                state.snapshots.entry(space_id.clone()).or_default().insert(
                    page_id.clone(),
                    SnapshotCacheRecord {
                        envelope: fresh,
                        generation: current_generation,
                        dirty: false,
                    },
                );
                append_event(
                    state,
                    EventScope::page(space_id.clone(), page_id.clone()),
                    EventKind::SnapshotChanged,
                    payload([("cache_state", "fresh".to_owned())]),
                    None,
                    false,
                )?;
                Ok(SnapshotRead {
                    envelope,
                    cache_state: agentyc_core::CacheState::Fresh,
                    scan_performed: true,
                })
            })
        })
    }

    /// Read snapshot metadata without returning a DOM or delta body.
    ///
    /// A clean cache hit performs no bridge scan, while a miss performs the same
    /// single validated scan as [`Broker::read_snapshot`] and discards its body
    /// before returning to the adapter.
    pub fn read_snapshot_metadata(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        authority: &AuthorityTicket,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<SnapshotMetadataRead, HostError> {
        self.read_snapshot(space_id, page_id, authority, lease_epoch, now)
            .map(|read| read.metadata())
    }

    /// Seed a clean cache entry after proving the current page lease and generation.
    pub fn put_snapshot(
        &self,
        authority: &AuthorityTicket,
        envelope: SnapshotEnvelope,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<(), HostError> {
        envelope
            .validate()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                let generation = authorize_page(
                    state,
                    &envelope.space_id,
                    &envelope.page_id,
                    authority,
                    lease_epoch,
                    now,
                    false,
                )?;
                if envelope.navigation_generation != generation.navigation_generation
                    || envelope.document_generation != generation.document_generation
                {
                    return Err(CoreError::new(
                        ErrorCode::TargetReplaced,
                        "snapshot generation does not match the page",
                    )
                    .into());
                }
                state
                    .snapshots
                    .entry(envelope.space_id.clone())
                    .or_default()
                    .insert(
                        envelope.page_id.clone(),
                        SnapshotCacheRecord {
                            envelope,
                            generation,
                            dirty: false,
                        },
                    );
                Ok(())
            })
        })
    }

    /// Mark a cached page dirty without scanning it.
    pub fn mark_snapshot_dirty(
        &self,
        authority: &AuthorityTicket,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<bool, HostError> {
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_page(state, space_id, page_id, authority, lease_epoch, now, false)?;
                Ok(state
                    .snapshots
                    .get_mut(space_id)
                    .and_then(|pages| pages.get_mut(page_id))
                    .map(|record| {
                        record.dirty = true;
                        true
                    })
                    .unwrap_or(false))
            })
        })
    }

    /// Publish one broker-sequenced logical event.
    pub fn publish_event(
        &self,
        authority: &AuthorityTicket,
        scope: EventScope,
        event: EventKind,
        payload: BTreeMap<String, String>,
    ) -> Result<EventRecord, HostError> {
        validate_public_payload(&payload)?;
        self.with_inner(|inner| {
            inner.ledger.update(|state| {
                authorize_ticket(state, authority)?;
                if let Some(space_id) = &scope.space_id {
                    authorize_visible_space(state, authority, space_id)?;
                    if let Some(page_id) = &scope.page_id
                        && state
                            .spaces
                            .get(space_id)
                            .and_then(|space| space.page(page_id))
                            .is_none()
                    {
                        return Err(CoreError::new(
                            ErrorCode::PageNotFound,
                            "logical page not found",
                        )
                        .into());
                    }
                }
                append_event(state, scope, event, payload, None, false)
            })
        })
    }

    /// Return events after a cursor, filtered by principal and logical scope.
    pub fn resume_events(
        &self,
        authority: &AuthorityTicket,
        query: EventQuery,
    ) -> Result<EventBatch, HostError> {
        self.with_inner(|inner| {
            let state = inner.ledger.state();
            authorize_ticket(state, authority)?;
            if let Some(scope) = &query.scope
                && let Some(space_id) = &scope.space_id
            {
                authorize_visible_space(state, authority, space_id)?;
                if let Some(page_id) = &scope.page_id
                    && state
                        .spaces
                        .get(space_id)
                        .and_then(|space| space.page(page_id))
                        .is_none()
                {
                    return Err(
                        CoreError::new(ErrorCode::PageNotFound, "logical page not found").into(),
                    );
                }
            }
            let cursor = agentyc_core::EventCursor {
                broker_epoch: state.broker_epoch,
                sequence: state.event_sequence,
            };
            if query.after.broker_epoch != state.broker_epoch
                || query.after.sequence.get() > state.event_sequence.get()
                || history_lagged(state, query.after.sequence)
            {
                return Ok(EventBatch {
                    broker_epoch: state.broker_epoch,
                    result: ResumeResult::ResyncRequired,
                    events: Vec::new(),
                    cursor,
                });
            }
            let events = state
                .events
                .iter()
                .filter(|event| event.is_after(query.after.sequence))
                .filter(|event| {
                    event.scope.space_id.as_ref().is_none_or(|space_id| {
                        state.spaces.get(space_id).is_some_and(|space| {
                            visible_to_principal(space, authority.principal_id())
                        })
                    })
                })
                .filter(|event| {
                    query.scope.as_ref().is_none_or(|requested| {
                        event.scope.matches(requested)
                            || (event.scope.space_id.is_none() && event.scope.page_id.is_none())
                    })
                })
                .cloned()
                .collect();
            Ok(EventBatch {
                broker_epoch: state.broker_epoch,
                result: ResumeResult::Accepted,
                events,
                cursor,
            })
        })
    }

    /// Return the current event cursor after authorizing the principal.
    pub fn event_cursor(
        &self,
        authority: &AuthorityTicket,
    ) -> Result<agentyc_core::EventCursor, HostError> {
        self.with_inner(|inner| {
            let state = inner.ledger.state();
            authorize_ticket(state, authority)?;
            Ok(agentyc_core::EventCursor {
                broker_epoch: state.broker_epoch,
                sequence: state.event_sequence,
            })
        })
    }

    /// Drain durable host work without closing any page or whole browser group.
    pub fn shutdown(&self, now: Timestamp) -> Result<(), HostError> {
        self.with_inner(|inner| {
            if inner.lifecycle == HostLifecycle::Stopped {
                return Ok(());
            }
            inner.ledger.update(|state| {
                let running: Vec<agentyc_core::ActionId> = state
                    .actions
                    .iter()
                    .filter_map(|(id, receipt)| {
                        (receipt.status == ActionStatus::Running).then_some(id.clone())
                    })
                    .collect();
                for action_id in running {
                    let space_id = state
                        .actions
                        .get(&action_id)
                        .map(|receipt| receipt.space_id.clone());
                    if let Some(receipt) = state.actions.get_mut(&action_id) {
                        receipt.mark_unknown(
                            UnknownReason::HostRestarted,
                            reconcile_token("shutdown", action_sequence(&action_id))?,
                        )?;
                    }
                    if let Some(space_id) = space_id {
                        append_event(
                            state,
                            EventScope::space(space_id),
                            EventKind::ActionChanged,
                            payload([
                                ("action_id", action_id.to_string()),
                                ("status", "unknown".to_owned()),
                            ]),
                            None,
                            true,
                        )?;
                    }
                }
                append_event(
                    state,
                    EventScope {
                        space_id: None,
                        page_id: None,
                    },
                    EventKind::BrokerDraining,
                    payload([("at", now.get().to_string())]),
                    None,
                    false,
                )?;
                Ok(())
            })?;
            inner.lifecycle = HostLifecycle::Stopped;
            Ok(())
        })
    }

    fn bridge(&self) -> Result<Arc<dyn Bridge>, HostError> {
        self.with_inner(|inner| Ok(inner.bridge.clone()))
    }

    fn with_inner<T, F>(&self, operation: F) -> Result<T, HostError>
    where
        F: FnOnce(&mut BrokerInner) -> Result<T, HostError>,
    {
        let mut inner = self.inner.lock().map_err(|_| HostError::StatePoisoned)?;
        operation(&mut inner)
    }
}

fn page_is_visible_in_inventory(record: &Value, space_id: &SpaceId) -> bool {
    let record_space = record.get("space_id").and_then(Value::as_str);
    if record_space.is_some_and(|record_space| record_space != space_id.as_str()) {
        return false;
    }
    if record.get("ownership").and_then(Value::as_str) == Some("unmanaged") {
        return record.get("active").and_then(Value::as_bool) == Some(true);
    }
    record_space == Some(space_id.as_str())
}

fn create_space_in_state(
    state: &mut LedgerState,
    principal_id: PrincipalId,
    space_id: SpaceId,
    label: String,
    retention: RetentionPolicy,
    profile_binding_id: Option<ProfileBindingId>,
) -> Result<SpaceDescriptor, HostError> {
    if state.spaces.contains_key(&space_id) {
        return Err(CoreError::invalid_argument("logical space already exists").into());
    }
    let descriptor = SpaceDescriptor {
        space_id: space_id.clone(),
        label,
        lifecycle: SpaceLifecycle::Created,
        owner: principal_id,
        lease: None,
        profile_binding: if profile_binding_id.is_some() {
            ProfileBindingState::Bound
        } else {
            ProfileBindingState::Unbound
        },
        pages: Vec::new(),
        visual_group_hint: None,
        capabilities: vec![
            Capability::Snapshot,
            Capability::Action,
            Capability::Wait,
            Capability::Reconcile,
        ],
        warnings: Vec::new(),
        retention,
    };
    state.spaces.insert(space_id.clone(), descriptor.clone());
    if let Some(profile_binding_id) = profile_binding_id {
        state
            .profile_bindings
            .insert(space_id.clone(), profile_binding_id);
    }
    state
        .space_generations
        .insert(space_id.clone(), Generation::new(0));
    append_event(
        state,
        EventScope::space(space_id),
        EventKind::SpaceChanged,
        payload([("lifecycle", "created".to_owned())]),
        None,
        false,
    )?;
    Ok(descriptor)
}

fn create_page_in_state(
    state: &mut LedgerState,
    space_id: &SpaceId,
    page_id: PageId,
    label: String,
) -> Result<PageDescriptor, HostError> {
    let descriptor = state
        .spaces
        .get_mut(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if descriptor.page(&page_id).is_some() {
        return Err(CoreError::invalid_argument("logical page already exists").into());
    }
    let page = PageDescriptor {
        page_id: page_id.clone(),
        space_id: space_id.clone(),
        label,
        lifecycle: PageLifecycle::Planned,
        ownership: PageOwnership::Agent,
        binding: PageBindingState::Unbound,
        url: None,
        title: None,
        target_generation: Generation::new(0),
        navigation_generation: Generation::new(0),
        document_generation: Generation::new(0),
        frame_count: 0,
        retained: true,
    };
    descriptor.pages.push(page.clone());
    append_event(
        state,
        EventScope::page(space_id.clone(), page_id),
        EventKind::PageChanged,
        payload([("lifecycle", "planned".to_owned())]),
        None,
        false,
    )?;
    Ok(page)
}

fn authority_is_current(state: &LedgerState, authority: &AuthorityTicket) -> bool {
    if authority.broker_epoch() != state.broker_epoch || authority.connection_epoch().get() == 0 {
        return false;
    }
    state
        .active_connections
        .get(&authority.connection_epoch())
        .is_some_and(|current| {
            current.principal_id == *authority.principal_id()
                && current.connection_nonce == *authority.connection_nonce()
                && current.profile_binding_id == authority.profile_binding_id().cloned()
        })
}

fn authorize_ticket(state: &LedgerState, authority: &AuthorityTicket) -> Result<(), HostError> {
    if authority.broker_epoch() != state.broker_epoch {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "authority ticket belongs to another broker epoch",
        )
        .into());
    }
    if !authority_is_current(state, authority) {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "authority ticket is not an active connection authority",
        )
        .into());
    }
    Ok(())
}

fn ensure_ready(inner: &BrokerInner) -> Result<(), HostError> {
    if inner.lifecycle == HostLifecycle::Ready {
        Ok(())
    } else {
        Err(CoreError::new(ErrorCode::HostDraining, "host is not accepting new work").into())
    }
}

fn require_capability(bridge: &dyn Bridge, capability: Capability) -> Result<(), HostError> {
    if bridge.capabilities().contains(&capability) {
        Ok(())
    } else {
        Err(CoreError::new(
            ErrorCode::CapabilityUnavailable,
            format!("bridge does not provide {capability:?}"),
        )
        .into())
    }
}

fn require_operation_capabilities(
    bridge: &dyn Bridge,
    operation: ActionOperation,
) -> Result<(), HostError> {
    let required = match operation {
        ActionOperation::Evaluate => vec![Capability::Evaluate],
        // Screenshot is an allowlisted debugger action; artifact transport is
        // only required for uploads and other out-of-band artifact flows.
        ActionOperation::Screenshot => vec![Capability::Action],
        ActionOperation::Upload => vec![Capability::Action, Capability::Artifact],
        ActionOperation::Wait => vec![Capability::Wait],
        ActionOperation::Navigate
        | ActionOperation::Click
        | ActionOperation::Input
        | ActionOperation::Scroll
        | ActionOperation::StorageWrite
        | ActionOperation::CookieWrite
        | ActionOperation::Close => vec![Capability::Action],
    };
    for capability in required {
        require_capability(bridge, capability)?;
    }
    Ok(())
}

fn authorize_profile_for_mutation(
    state: &LedgerState,
    space_id: &SpaceId,
    authority: &AuthorityTicket,
) -> Result<(), HostError> {
    let space = state
        .spaces
        .get(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    match space.profile_binding {
        ProfileBindingState::Unbound => {
            if authority.profile_binding_id().is_some() {
                return Err(CoreError::new(
                    ErrorCode::ProfileNotFound,
                    "space has no matching profile binding",
                )
                .into());
            }
        }
        ProfileBindingState::Bound => {
            let expected = state.profile_bindings.get(space_id).ok_or_else(|| {
                CoreError::new(
                    ErrorCode::LedgerIncompatible,
                    "profile binding index is incomplete",
                )
            })?;
            if authority.profile_binding_id() != Some(expected) {
                return Err(CoreError::new(
                    ErrorCode::ProfileNotFound,
                    "authority profile binding does not match the space",
                )
                .into());
            }
        }
        ProfileBindingState::RebindRequired | ProfileBindingState::Revoked => {
            return Err(CoreError::new(
                ErrorCode::ProfileNotFound,
                "space profile binding requires explicit re-enrollment",
            )
            .into());
        }
    }
    Ok(())
}

fn authorize_finish_claim(
    state: &LedgerState,
    space_id: &SpaceId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
    now: Timestamp,
    allow_draining: bool,
) -> Result<(), HostError> {
    authorize_ticket(state, authority)?;
    authorize_profile_for_mutation(state, space_id, authority)?;
    let space = state
        .spaces
        .get(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if space.lifecycle == SpaceLifecycle::UserOwned {
        return Err(CoreError::new(
            ErrorCode::UserControlRequired,
            "user-owned space requires its control ticket",
        )
        .into());
    }
    if !matches!(
        space.lifecycle,
        SpaceLifecycle::AgentOwned | SpaceLifecycle::Recovering
    ) && !(allow_draining && space.lifecycle == SpaceLifecycle::Draining)
    {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "space lifecycle does not permit finishing",
        )
        .into());
    }
    let lease = space
        .lease
        .as_ref()
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceForbidden, "space has no active lease"))?;
    if lease.principal_id != *authority.principal_id() || space.owner != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the space lease",
        )
        .into());
    }
    if lease.lease_epoch != lease_epoch {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if lease.state != LeaseState::Active {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if lease.expires_at.get() <= now.get() {
        return Err(CoreError::new(ErrorCode::LeaseExpired, "lease has expired").into());
    }
    Ok(())
}

fn authorize_cleanup_page(
    state: &LedgerState,
    space_id: &SpaceId,
    proof: &CleanupProof,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
    now: Timestamp,
) -> Result<(), HostError> {
    authorize_finish_claim(state, space_id, authority, lease_epoch, now, true)?;
    let page = state
        .spaces
        .get(space_id)
        .and_then(|space| space.page(&proof.page_id))
        .ok_or_else(|| CoreError::new(ErrorCode::PageNotFound, "logical page not found"))?;
    if page.lifecycle != PageLifecycle::Closing
        || page.ownership != PageOwnership::Agent
        || page.binding != PageBindingState::Bound
        || !proof.generation.matches_page(page)
    {
        return Err(
            CoreError::new(ErrorCode::TargetReplaced, "page cleanup proof is stale").into(),
        );
    }
    Ok(())
}

fn authorize_release_claim(
    state: &LedgerState,
    space_id: &SpaceId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
) -> Result<(), HostError> {
    authorize_ticket(state, authority)?;
    authorize_profile_for_mutation(state, space_id, authority)?;
    let space = state
        .spaces
        .get(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if !matches!(
        space.lifecycle,
        SpaceLifecycle::Finished | SpaceLifecycle::Released
    ) {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "space must be finished before release",
        )
        .into());
    }
    if space.owner != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the finished space",
        )
        .into());
    }
    let lease = space
        .lease
        .as_ref()
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceForbidden, "space has no release lease"))?;
    if lease.principal_id != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the release lease",
        )
        .into());
    }
    if lease.lease_epoch != lease_epoch {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if lease.state != LeaseState::Released {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "space release requires a durably released lease",
        )
        .into());
    }
    Ok(())
}

fn authorize_reconciliation_authority(
    state: &LedgerState,
    action_id: &ActionId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
    now: Timestamp,
) -> Result<ActionReceipt, HostError> {
    authorize_ticket(state, authority)?;
    let receipt = state
        .actions
        .get(action_id)
        .ok_or_else(|| CoreError::invalid_argument("action receipt is missing"))?
        .clone();
    if receipt.status != ActionStatus::Unknown {
        return Err(CoreError::new(
            ErrorCode::ReconciliationRequired,
            "action does not have an unknown outcome",
        )
        .into());
    }
    authorize_profile_for_mutation(state, &receipt.space_id, authority)?;
    let space = state
        .spaces
        .get(&receipt.space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if !space.lifecycle.admits_mutations() || space.owner != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the current reconciliation lease",
        )
        .into());
    }
    let lease = space
        .lease
        .as_ref()
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceForbidden, "space has no current lease"))?;
    if lease.principal_id != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the current reconciliation lease",
        )
        .into());
    }
    if lease.lease_epoch != lease_epoch {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if lease.state != LeaseState::Active || lease.expires_at.get() <= now.get() {
        return Err(CoreError::new(ErrorCode::LeaseExpired, "lease has expired").into());
    }
    if receipt.lease_epoch != lease.lease_epoch {
        let proof_exists = state
            .takeover_proofs
            .get(&receipt.space_id)
            .is_some_and(|proofs| {
                proofs.iter().any(|proof| {
                    proof.broker_epoch == state.broker_epoch
                        && proof.previous_epoch == receipt.lease_epoch
                        && proof.current_epoch == lease.lease_epoch
                        && proof.principal_id == *authority.principal_id()
                })
            });
        if receipt.lease_epoch > lease.lease_epoch || !proof_exists {
            return Err(CoreError::new(
                ErrorCode::ReconciliationRequired,
                "older-epoch reconciliation requires a durable takeover proof",
            )
            .into());
        }
    }
    Ok(receipt)
}

fn authorize_space(
    state: &LedgerState,
    space_id: &SpaceId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
    now: Timestamp,
    mutation: bool,
) -> Result<(), HostError> {
    authorize_ticket(state, authority)?;
    authorize_profile_for_mutation(state, space_id, authority)?;
    let space = state
        .spaces
        .get(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if space.lifecycle == SpaceLifecycle::UserOwned {
        return Err(CoreError::new(
            ErrorCode::UserControlRequired,
            "user control currently fences agent mutations",
        )
        .into());
    }
    let lease = space
        .lease
        .as_ref()
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceForbidden, "space has no active lease"))?;
    if lease.principal_id != *authority.principal_id() {
        return Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal does not own the space",
        )
        .into());
    }
    if lease.lease_epoch != lease_epoch {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if lease.state == LeaseState::Expired || lease.expires_at.get() <= now.get() {
        return Err(CoreError::new(ErrorCode::LeaseExpired, "lease has expired").into());
    }
    if lease.state != LeaseState::Active {
        return Err(CoreError::stale_lease(lease.lease_epoch.get(), lease_epoch.get()).into());
    }
    if mutation && !space.lifecycle.admits_mutations() {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "space lifecycle fences mutations",
        )
        .into());
    }
    Ok(())
}

fn space_lifecycle_name(lifecycle: SpaceLifecycle) -> &'static str {
    match lifecycle {
        SpaceLifecycle::Created => "created",
        SpaceLifecycle::AgentOwned => "agent_owned",
        SpaceLifecycle::HandoffRequested => "handoff_requested",
        SpaceLifecycle::Draining => "draining",
        SpaceLifecycle::Paused => "paused",
        SpaceLifecycle::UserOwned => "user_owned",
        SpaceLifecycle::Orphaned => "orphaned",
        SpaceLifecycle::Recovering => "recovering",
        SpaceLifecycle::Finished => "finished",
        SpaceLifecycle::Released => "released",
        SpaceLifecycle::FencePending => "fence_pending",
        SpaceLifecycle::FenceDispatched => "fence_dispatched",
        SpaceLifecycle::FenceAcknowledged => "fence_acknowledged",
    }
}

fn visible_to_principal(space: &SpaceDescriptor, principal_id: &PrincipalId) -> bool {
    space.owner == *principal_id
        || space
            .lease
            .as_ref()
            .is_some_and(|lease| lease.principal_id == *principal_id)
}

fn visible_state(state: &LedgerState, principal_id: &PrincipalId) -> LedgerState {
    let visible_spaces: std::collections::BTreeSet<_> = state
        .spaces
        .iter()
        .filter(|(_, space)| visible_to_principal(space, principal_id))
        .map(|(space_id, _)| space_id.clone())
        .collect();
    let mut filtered = state.clone();
    filtered
        .spaces
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered
        .space_generations
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered
        .profile_bindings
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered
        .control_tickets
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered
        .actions
        .retain(|_, receipt| visible_spaces.contains(&receipt.space_id));
    filtered
        .action_requests
        .retain(|action_id, _| filtered.actions.contains_key(action_id));
    filtered
        .idempotency
        .retain(|_, action_id| filtered.actions.contains_key(action_id));
    filtered
        .action_queues
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered
        .snapshots
        .retain(|space_id, _| visible_spaces.contains(space_id));
    filtered.events.retain(|event| {
        event
            .scope
            .space_id
            .as_ref()
            .is_none_or(|space_id| visible_spaces.contains(space_id))
    });
    filtered
}

fn authorize_visible_space(
    state: &LedgerState,
    authority: &AuthorityTicket,
    space_id: &SpaceId,
) -> Result<(), HostError> {
    authorize_ticket(state, authority)?;
    let space = state
        .spaces
        .get(space_id)
        .ok_or_else(|| CoreError::new(ErrorCode::SpaceNotFound, "logical space not found"))?;
    if visible_to_principal(space, authority.principal_id()) {
        Ok(())
    } else {
        Err(CoreError::new(
            ErrorCode::SpaceForbidden,
            "principal cannot read this space",
        )
        .into())
    }
}

fn authorize_page(
    state: &LedgerState,
    space_id: &SpaceId,
    page_id: &PageId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
    now: Timestamp,
    mutation: bool,
) -> Result<PageGeneration, HostError> {
    authorize_space(state, space_id, authority, lease_epoch, now, mutation)?;
    let page = state
        .spaces
        .get(space_id)
        .and_then(|space| space.page(page_id))
        .ok_or_else(|| CoreError::new(ErrorCode::PageNotFound, "logical page not found"))?;
    if mutation && !page.admits_agent_mutations() {
        return Err(
            CoreError::new(ErrorCode::PageNotOwned, "page is not managed by the lease").into(),
        );
    }
    if !mutation
        && (page.binding != PageBindingState::Bound || page.lifecycle != PageLifecycle::Managed)
    {
        return Err(CoreError::new(ErrorCode::UnmanagedPage, "page is not currently bound").into());
    }
    Ok(PageGeneration::from_page(page))
}

fn bump_page_generations(page: &mut PageDescriptor) -> Result<(), HostError> {
    page.target_generation = page
        .target_generation
        .checked_next()
        .ok_or_else(|| CoreError::invalid_argument("target generation overflow"))?;
    page.navigation_generation = page
        .navigation_generation
        .checked_next()
        .ok_or_else(|| CoreError::invalid_argument("navigation generation overflow"))?;
    page.document_generation = page
        .document_generation
        .checked_next()
        .ok_or_else(|| CoreError::invalid_argument("document generation overflow"))?;
    Ok(())
}

fn bounded_warning(warning: &str) -> String {
    warning
        .chars()
        .filter(|character| !character.is_control())
        .take(512)
        .collect()
}

fn current_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| {
            duration.as_millis().min(u128::from(u64::MAX)) as u64
        })
}

fn lease_times(now: Timestamp, ttl: u64) -> Result<(Timestamp, Timestamp), HostError> {
    if ttl == 0 {
        return Err(CoreError::invalid_argument("lease TTL must be positive").into());
    }
    let expires = now
        .get()
        .checked_add(ttl)
        .ok_or_else(|| CoreError::invalid_argument("lease expiry overflow"))?;
    let renew = now
        .get()
        .checked_add((ttl / 2).max(1))
        .ok_or_else(|| CoreError::invalid_argument("lease renewal deadline overflow"))?;
    Ok((Timestamp::new(expires), Timestamp::new(renew)))
}

fn bounded_label(label: String) -> Result<String, HostError> {
    if label.is_empty() || label.len() > 256 || label.chars().any(char::is_control) {
        return Err(
            CoreError::invalid_argument("label must be a bounded non-control string").into(),
        );
    }
    Ok(label)
}

fn bounded_optional(value: Option<String>) -> Result<Option<String>, HostError> {
    if value
        .as_ref()
        .is_some_and(|value| value.len() > 4_096 || value.chars().any(char::is_control))
    {
        return Err(CoreError::invalid_argument(
            "metadata value is too large or contains control data",
        )
        .into());
    }
    Ok(value)
}

fn validate_public_payload(payload: &BTreeMap<String, String>) -> Result<(), HostError> {
    for (key, value) in payload {
        if key.is_empty() || key.len() > 128 || value.len() > 65_536 {
            return Err(CoreError::invalid_argument("action payload is out of bounds").into());
        }
        let normalized: String = key
            .bytes()
            .filter(u8::is_ascii_alphanumeric)
            .map(|byte| byte.to_ascii_lowercase() as char)
            .collect();
        if matches!(
            normalized.as_str(),
            "targetid"
                | "sessionid"
                | "tabid"
                | "debuggerid"
                | "browserid"
                | "windowid"
                | "connectionid"
                | "chromeid"
                | "rawid"
        ) || (normalized.contains("target") && normalized.contains("id"))
        {
            return Err(CoreError::invalid_argument(
                "raw browser identities are not public action fields",
            )
            .into());
        }
    }
    Ok(())
}

fn payload<const N: usize>(items: [(&str, String); N]) -> BTreeMap<String, String> {
    items
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

fn append_event(
    state: &mut LedgerState,
    scope: EventScope,
    event: EventKind,
    payload: BTreeMap<String, String>,
    dirty_reason: Option<DirtyReason>,
    resync_required: bool,
) -> Result<EventRecord, HostError> {
    validate_public_payload_shape(&payload, 512).map_err(HostError::Ledger)?;
    let sequence = state
        .event_sequence
        .checked_next()
        .ok_or_else(|| CoreError::invalid_argument("event sequence overflow"))?;
    state.event_sequence = sequence;
    if let Some(space_id) = &scope.space_id {
        let generation = state
            .space_generations
            .entry(space_id.clone())
            .or_insert(Generation::new(0));
        *generation = generation
            .checked_next()
            .ok_or_else(|| CoreError::invalid_argument("space generation overflow"))?;
    }
    let generation = generation_watermark(state, &scope);
    let event_id = EventId::from_suffix(format!("{}-{}", state.broker_epoch.get(), sequence.get()))
        .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    let record = EventRecord {
        protocol: PROTOCOL_VERSION,
        event_id,
        broker_epoch: state.broker_epoch,
        sequence,
        scope,
        event,
        generation,
        dirty_reason,
        coalesced: false,
        resync_required,
        payload,
    };
    state.events.push(record.clone());
    Ok(record)
}

fn generation_watermark(state: &LedgerState, scope: &EventScope) -> GenerationWatermark {
    let page = scope
        .space_id
        .as_ref()
        .and_then(|space_id| state.spaces.get(space_id))
        .and_then(|space| {
            scope
                .page_id
                .as_ref()
                .and_then(|page_id| space.page(page_id))
        });
    GenerationWatermark {
        space_generation: scope
            .space_id
            .as_ref()
            .and_then(|space_id| state.space_generations.get(space_id))
            .copied()
            .unwrap_or_default(),
        page_generation: page.map_or(Generation::new(0), |page| page.target_generation),
        navigation_generation: page.map_or(Generation::new(0), |page| page.navigation_generation),
        document_generation: page.map_or(Generation::new(0), |page| page.document_generation),
        snapshot_version: scope
            .space_id
            .as_ref()
            .and_then(|space_id| state.snapshots.get(space_id))
            .and_then(|pages| {
                scope
                    .page_id
                    .as_ref()
                    .and_then(|page_id| pages.get(page_id))
            })
            .map(|record| record.envelope.snapshot_version),
    }
}

fn history_lagged(state: &LedgerState, sequence: EventSequence) -> bool {
    if state.events.is_empty() {
        return state.event_sequence.get() > sequence.get();
    }
    state
        .events
        .first()
        .is_some_and(|first| sequence.get().saturating_add(1) < first.sequence.get())
}

fn resume_status(state: &LedgerState, watermark: ResumeWatermark) -> ResumeResult {
    if watermark.broker_epoch != state.broker_epoch
        || watermark.sequence.get() > state.event_sequence.get()
        || history_lagged(state, watermark.sequence)
    {
        ResumeResult::ResyncRequired
    } else {
        ResumeResult::Accepted
    }
}

fn reconcile_token(prefix: &str, number: u64) -> Result<ReconcileToken, HostError> {
    ReconcileToken::from_suffix(format!("{prefix}-{number}"))
        .map_err(|error| CoreError::invalid_argument(error.to_string()).into())
}

fn fence_request_token(
    state: &LedgerState,
    space_id: &SpaceId,
    fence_epoch: LeaseEpoch,
    purpose: FencePurpose,
) -> Result<ReconcileToken, HostError> {
    let purpose_name = match purpose {
        FencePurpose::Takeover => "takeover",
        FencePurpose::ReturnControl => "return",
    };
    ReconcileToken::from_suffix(format!(
        "fence-{purpose_name}-{}-{}-{}",
        state.broker_epoch.get(),
        space_id,
        fence_epoch.get()
    ))
    .map_err(|error| CoreError::invalid_argument(error.to_string()).into())
}

fn action_sequence(action_id: &agentyc_core::ActionId) -> u64 {
    action_id.as_str().bytes().fold(0_u64, |value, byte| {
        value.wrapping_mul(31).wrapping_add(u64::from(byte))
    })
}

/// Compute the canonical request-context hash expected by [`Broker::enqueue_action`].
///
/// The host recomputes and compares this value at admission; callers should not
/// treat it as an authentication token.
pub fn canonical_action_hash(
    request: &ActionRequest<BTreeMap<String, String>>,
) -> Result<agentyc_core::ContentHash, HostError> {
    ledger_action_hash(request).map_err(HostError::Ledger)
}

fn is_mutating(operation: ActionOperation) -> bool {
    !matches!(
        operation,
        ActionOperation::Wait | ActionOperation::Screenshot
    )
}

fn status_name(status: ActionStatus) -> String {
    format!("{status:?}").to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bridge::FakeBridge;
    use agentyc_core::{ClientId, ClientMetadata, ConnectionNonce};
    use tempfile::tempdir;

    fn principal(suffix: &str) -> PrincipalId {
        PrincipalId::from_suffix(suffix).expect("principal")
    }

    fn admit_authority(broker: &Broker, suffix: &str) -> AuthorityTicket {
        admit_authority_with_nonce(broker, suffix, suffix)
    }

    fn admit_authority_with_nonce(
        broker: &Broker,
        principal_suffix: &str,
        nonce_suffix: &str,
    ) -> AuthorityTicket {
        let hello = HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: principal(principal_suffix),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: Some(
                    ClientId::from_suffix(format!("client-{nonce_suffix}")).expect("client"),
                ),
                client_name: Some("host-unit-test".to_owned()),
                client_version: Some("2".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix(format!("nonce-{nonce_suffix}")).expect("nonce"),
                ),
                profile_binding_id: Some(
                    ProfileBindingId::from_suffix("host-test").expect("profile"),
                ),
            }),
        };
        broker.hello(&hello).expect("hello").authority().clone()
    }

    #[test]
    fn two_spaces_are_isolated_and_stale_epochs_are_rejected() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let one_authority = admit_authority(&broker, "one");
        let one = broker
            .create_space(&one_authority, "one")
            .expect("space one");
        let lease_one = broker
            .acquire_lease(&one.space_id, &one_authority, Timestamp::new(0), 100)
            .expect("lease one");
        let two_authority = admit_authority(&broker, "two");
        let two = broker
            .create_space(&two_authority, "two")
            .expect("space two");
        let lease_two = broker
            .acquire_lease(&two.space_id, &two_authority, Timestamp::new(0), 100)
            .expect("lease two");
        assert!(
            broker
                .create_page(
                    &one.space_id,
                    &two_authority,
                    lease_two.lease.lease_epoch,
                    "bad"
                )
                .is_err()
        );
        let one_authority = admit_authority_with_nonce(&broker, "one", "one-reconnect");
        assert!(matches!(
            broker.renew_lease(
                &one.space_id,
                &one_authority,
                LeaseEpoch::new(0),
                Timestamp::new(1),
                100
            ),
            Err(HostError::Core(CoreError {
                code: ErrorCode::StaleLease,
                ..
            }))
        ));
        assert_eq!(broker.list_spaces(&one_authority).expect("list").len(), 1);
        assert_eq!(lease_one.lease.lease_epoch.get(), 1);
        assert_eq!(lease_two.lease.lease_epoch.get(), 1);
    }

    #[test]
    fn takeover_fences_old_epoch_and_pending_fence_blocks_new_work() {
        let directory = tempdir().expect("tempdir");
        let bridge = FakeBridge::new();
        bridge.set_fence_acknowledged(false);
        let broker = Broker::open(directory.path(), bridge).expect("broker");
        let one_authority = admit_authority(&broker, "one");
        let space = broker.create_space(&one_authority, "one").expect("space");
        let lease = broker
            .acquire_lease(&space.space_id, &one_authority, Timestamp::new(0), 100)
            .expect("lease");
        let takeover = broker
            .takeover(&space.space_id, &one_authority, Timestamp::new(1), 100)
            .expect("takeover pending");
        assert!(!takeover.fence_acknowledged);
        assert_eq!(takeover.lifecycle, SpaceLifecycle::FencePending);
        assert!(
            broker
                .renew_lease(
                    &space.space_id,
                    &one_authority,
                    lease.lease.lease_epoch,
                    Timestamp::new(2),
                    100
                )
                .is_err()
        );
    }

    #[test]
    fn bridge_page_loss_events_fence_logical_pages_without_closing_tabs() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let owner = admit_authority(&broker, "owner");
        let space = broker.create_space(&owner, "loss").expect("space");
        let lease = broker
            .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
            .expect("lease");
        let page = broker
            .create_page_at(
                &space.space_id,
                &owner,
                lease.lease.lease_epoch,
                "page",
                Timestamp::new(0),
            )
            .expect("page");
        let bound = broker
            .bind_page(
                &space.space_id,
                &page.page_id,
                &owner,
                lease.lease.lease_epoch,
                Timestamp::new(0),
                Some("https://agent.test/".to_owned()),
                None,
                0,
            )
            .expect("bound page");
        let extension = admit_authority_with_nonce(&broker, "extension", "extension-loss");
        let event = serde_json::json!({
            "kind": "event",
            "event": "page.lost",
            "payload": {
                "space_id": space.space_id,
                "page_id": page.page_id,
                "target_generation": bound.target_generation.get() + 1,
                "navigation_generation": bound.navigation_generation.get() + 1,
                "document_generation": bound.document_generation.get() + 1,
            }
        });
        broker
            .apply_bridge_event(&extension, &event, Timestamp::new(1))
            .expect("bridge event");
        let updated = broker
            .describe_space(&owner, &space.space_id)
            .expect("updated space");
        let updated_page = updated.page(&page.page_id).expect("updated page");
        assert_eq!(updated_page.lifecycle, PageLifecycle::TargetLost);
        assert_eq!(updated_page.binding, PageBindingState::Lost);
        assert_eq!(
            updated_page.target_generation.get(),
            bound.target_generation.get() + 1
        );
    }

    #[test]
    fn forged_principal_nonce_profile_and_old_epoch_tickets_are_rejected() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let authority = admit_authority(&broker, "legitimate");

        let forged_principal = AuthorityTicket::host_issued(
            principal("impostor"),
            authority.broker_epoch(),
            authority.connection_epoch(),
            authority.profile_binding_id().cloned(),
            authority.connection_nonce().clone(),
        );
        assert!(matches!(
            broker.ledger_json(&forged_principal),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        let forged_nonce = AuthorityTicket::host_issued(
            authority.principal_id().clone(),
            authority.broker_epoch(),
            authority.connection_epoch(),
            authority.profile_binding_id().cloned(),
            ConnectionNonce::from_suffix("wrong-nonce").expect("nonce"),
        );
        assert!(matches!(
            broker.ledger_json(&forged_nonce),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        let forged_profile = AuthorityTicket::host_issued(
            authority.principal_id().clone(),
            authority.broker_epoch(),
            authority.connection_epoch(),
            Some(ProfileBindingId::from_suffix("wrong-profile").expect("profile")),
            authority.connection_nonce().clone(),
        );
        assert!(matches!(
            broker.ledger_json(&forged_profile),
            Err(HostError::Core(CoreError {
                code: ErrorCode::PermissionDenied,
                ..
            }))
        ));

        let newer = admit_authority(&broker, "newer");
        assert!(broker.ledger_json(&authority).is_ok());
        assert!(broker.ledger_json(&newer).is_ok());
        broker
            .disconnect(&authority)
            .expect("disconnect first session");
        assert!(broker.ledger_json(&authority).is_err());
        assert!(broker.ledger_json(&newer).is_ok());
    }
}
