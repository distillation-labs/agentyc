//! Bounded, fail-closed, atomically persisted host state.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

use agentyc_core::{
    ActionId, ActionOperation, ActionReceipt, ActionRequest, ActionStatus, BrokerEpoch,
    CompletionSource, ConnectionEpoch, ConnectionNonce, DispatchState, EventRecord, EventSequence,
    IdempotencyKey, LeaseEpoch, PageBindingState, PageId, PageLifecycle, PageOwnership,
    PrincipalId, ProfileBindingId, ProfileBindingState, ReconcileToken, ReconciliationState,
    SpaceDescriptor, SpaceId, SpaceLifecycle, UnknownReason,
};
use serde::{Deserialize, Serialize};

use agentyc_core::states::LeaseState;

use crate::{HostError, error::LedgerError, snapshots::SnapshotCacheRecord};

/// Current durable ledger schema understood by this crate.
pub const LEDGER_SCHEMA_VERSION: u16 = 2;

const MAX_LOCK_TOKEN_BYTES: usize = 256;

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);
static LOCK_SEQUENCE: AtomicU64 = AtomicU64::new(1);
static QUARANTINE_SEQUENCE: AtomicU64 = AtomicU64::new(1);

/// Resource limits applied both in memory and before every durable replacement.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct LedgerLimits {
    /// Maximum number of logical spaces.
    pub max_spaces: usize,
    /// Maximum logical pages in one space.
    pub max_pages_per_space: usize,
    /// Maximum retained action receipts.
    pub max_actions: usize,
    /// Maximum queued actions in one space.
    pub max_queued_actions_per_space: usize,
    /// Maximum retained broker events.
    pub max_events: usize,
    /// Maximum cached snapshots across all pages.
    pub max_snapshots: usize,
    /// Maximum serialized bytes for the ledger file.
    pub max_ledger_bytes: usize,
    /// Maximum serialized bytes for one cached snapshot.
    pub max_snapshot_bytes: usize,
}

/// Purpose of a durable fence request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FencePurpose {
    /// A new agent lease is waiting for bridge acknowledgement.
    Takeover,
    /// An agent lease is being returned to user control.
    ReturnControl,
}

/// Exact durable identity of a fence request whose bridge completion is pending.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PendingFenceRecord {
    /// Logical space covered by the fence.
    pub space_id: SpaceId,
    /// Broker epoch that created the request.
    pub broker_epoch: BrokerEpoch,
    /// Exact host request identity used for compare-and-set completion.
    pub request_token: ReconcileToken,
    /// Epoch being invalidated, when one exists.
    pub old_epoch: Option<LeaseEpoch>,
    /// New fence epoch.
    pub fence_epoch: LeaseEpoch,
    /// Principal that must complete the fence.
    pub principal_id: PrincipalId,
    /// Transition waiting for completion.
    pub purpose: FencePurpose,
}

/// Durable proof that a takeover fence completed for an older lease epoch.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TakeoverProofRecord {
    /// Logical space covered by the proof.
    pub space_id: SpaceId,
    /// Broker epoch that issued the proof.
    pub broker_epoch: BrokerEpoch,
    /// Epoch of actions that may be reconciled with this proof.
    pub previous_epoch: LeaseEpoch,
    /// Current lease epoch established by the takeover.
    pub current_epoch: LeaseEpoch,
    /// Principal that owns the current lease.
    pub principal_id: PrincipalId,
    /// Exact fence request that produced the proof.
    pub request_token: ReconcileToken,
}

/// Durable one-time handoff proof retained while a space is user-owned.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControlTicketRecord {
    /// Logical space covered by the proof.
    pub space_id: SpaceId,
    /// Broker epoch that issued the proof.
    pub broker_epoch: BrokerEpoch,
    /// Fence epoch that invalidated the former lease.
    pub fence_epoch: agentyc_core::LeaseEpoch,
    /// Opaque host-issued token.
    pub token: ReconcileToken,
}

impl Default for LedgerLimits {
    fn default() -> Self {
        Self {
            max_spaces: 64,
            max_pages_per_space: 128,
            max_actions: 4_096,
            max_queued_actions_per_space: 256,
            max_events: 4_096,
            max_snapshots: 512,
            max_ledger_bytes: 8 * 1024 * 1024,
            max_snapshot_bytes: 2 * 1024 * 1024,
        }
    }
}

/// One live local connection admitted by the current broker process.
///
/// This registry is intentionally skipped by serde: connection authority is
/// process-local and must never survive a broker restart or become durable
/// ledger state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ActiveConnection {
    pub(crate) principal_id: PrincipalId,
    pub(crate) connection_nonce: ConnectionNonce,
    pub(crate) profile_binding_id: Option<ProfileBindingId>,
    pub(crate) is_extension: bool,
}

/// The complete JSON-compatible logical state owned by one broker.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LedgerState {
    /// Durable schema version.
    pub schema_version: u16,
    /// Current broker lifecycle epoch.
    pub broker_epoch: BrokerEpoch,
    /// Monotonic local connection epoch counter.
    pub connection_epoch: ConnectionEpoch,
    /// Live connection authorities for this broker process.
    ///
    /// This is an in-memory registry only. The field is skipped during
    /// serialization so restart clears every prior authority.
    #[serde(skip)]
    pub(crate) active_connections: BTreeMap<ConnectionEpoch, ActiveConnection>,
    /// Nonces already admitted in this broker epoch.
    #[serde(skip)]
    pub(crate) used_connection_nonces: BTreeSet<ConnectionNonce>,
    /// Principal bound to the most recently admitted connection.
    #[serde(skip)]
    pub(crate) connection_principal_id: Option<PrincipalId>,
    /// Nonce bound to the most recently admitted connection.
    #[serde(skip)]
    pub(crate) connection_nonce: Option<ConnectionNonce>,
    /// Profile bound to the most recently admitted connection.
    #[serde(skip)]
    pub(crate) connection_profile_binding_id: Option<ProfileBindingId>,
    /// Host-assigned logical identity counters.
    pub next_space_number: u64,
    /// Host-assigned logical identity counter for pages.
    pub next_page_number: u64,
    /// Broker-wide event sequence within the current epoch.
    pub event_sequence: EventSequence,
    /// Durable logical spaces and pages.
    pub spaces: BTreeMap<SpaceId, SpaceDescriptor>,
    /// Durable action receipts.
    pub actions: BTreeMap<ActionId, ActionReceipt>,
    /// Original logical requests retained for dispatch/reconciliation metadata.
    pub action_requests: BTreeMap<ActionId, ActionRequest<BTreeMap<String, String>>>,
    /// Idempotency index.
    pub idempotency: BTreeMap<IdempotencyKey, ActionId>,
    /// Per-space FIFO action queues.
    pub action_queues: BTreeMap<SpaceId, Vec<ActionId>>,
    /// Retained broker events in sequence order.
    pub events: Vec<EventRecord>,
    /// Logical snapshot cache partitioned by space and page.
    pub snapshots: BTreeMap<SpaceId, BTreeMap<PageId, SnapshotCacheRecord>>,
    /// One-time control proofs retained for user-owned spaces.
    #[serde(default)]
    pub control_tickets: BTreeMap<SpaceId, ControlTicketRecord>,
    /// At most one exact fence request may be pending per logical space.
    #[serde(default)]
    pub pending_fences: BTreeMap<SpaceId, PendingFenceRecord>,
    /// Durable proofs retained for current-owner reconciliation after takeover.
    #[serde(default)]
    pub takeover_proofs: BTreeMap<SpaceId, Vec<TakeoverProofRecord>>,
    /// Profile bindings retained without exposing profile handles in core records.
    #[serde(default)]
    pub profile_bindings: BTreeMap<SpaceId, agentyc_core::ProfileBindingId>,
    /// Last Native Messaging extension profile admitted by this broker state.
    #[serde(default)]
    pub(crate) extension_profile_binding_id: Option<ProfileBindingId>,
    /// Highest worker epoch admitted for the extension profile.
    #[serde(default)]
    pub(crate) extension_worker_instance_epoch: Option<u64>,
    /// Highest browser session epoch admitted for the extension profile.
    #[serde(default)]
    pub(crate) extension_browser_session_epoch: Option<u64>,
    /// Logical space generation watermarks used in event records.
    pub space_generations: BTreeMap<SpaceId, agentyc_core::Generation>,
}

impl LedgerState {
    /// Construct an empty state for a newly created broker epoch.
    pub fn new(broker_epoch: BrokerEpoch) -> Self {
        Self {
            schema_version: LEDGER_SCHEMA_VERSION,
            broker_epoch,
            connection_epoch: ConnectionEpoch::new(0),
            active_connections: BTreeMap::new(),
            used_connection_nonces: BTreeSet::new(),
            connection_principal_id: None,
            connection_nonce: None,
            connection_profile_binding_id: None,
            next_space_number: 0,
            next_page_number: 0,
            event_sequence: EventSequence::new(0),
            spaces: BTreeMap::new(),
            actions: BTreeMap::new(),
            action_requests: BTreeMap::new(),
            idempotency: BTreeMap::new(),
            action_queues: BTreeMap::new(),
            events: Vec::new(),
            snapshots: BTreeMap::new(),
            control_tickets: BTreeMap::new(),
            pending_fences: BTreeMap::new(),
            takeover_proofs: BTreeMap::new(),
            profile_bindings: BTreeMap::new(),
            extension_profile_binding_id: None,
            extension_worker_instance_epoch: None,
            extension_browser_session_epoch: None,
            space_generations: BTreeMap::new(),
        }
    }
}

/// A single-process-owned durable ledger.
///
/// The lock is held for the lifetime of this value. Every mutation checks that
/// the lock path still refers to the same owner before replacing the JSON file.
pub struct Ledger {
    directory: PathBuf,
    state_path: PathBuf,
    lock: LedgerLock,
    state: LedgerState,
    limits: LedgerLimits,
}

impl std::fmt::Debug for Ledger {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("Ledger")
            .field("directory", &self.directory)
            .field("state_path", &self.state_path)
            .field("state", &self.state)
            .field("limits", &self.limits)
            .finish_non_exhaustive()
    }
}

impl Ledger {
    /// Open or create a state directory and acquire its exclusive broker lock.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, LedgerError> {
        Self::open_with_limits(path, LedgerLimits::default())
    }

    /// Open a ledger with explicit in-memory and persistence bounds.
    pub fn open_with_limits(
        path: impl AsRef<Path>,
        limits: LedgerLimits,
    ) -> Result<Self, LedgerError> {
        let directory = path.as_ref().to_path_buf();
        ensure_directory(&directory)?;
        let state_path = directory.join("ledger.json");
        let lock_path = directory.join("broker.lock");
        let lock = LedgerLock::acquire(lock_path)?;

        let (mut state, fresh) = if !path_is_present(&state_path)? {
            (LedgerState::new(BrokerEpoch::new(1)), true)
        } else {
            let bytes = read_bounded(&state_path, limits.max_ledger_bytes)?;
            let state: LedgerState = match serde_json::from_slice(&bytes) {
                Ok(state) => state,
                Err(error) => {
                    quarantine_bytes(&state_path, &bytes);
                    return Err(LedgerError::Corrupt(error.to_string()));
                }
            };
            if state.schema_version != LEDGER_SCHEMA_VERSION {
                quarantine_bytes(&state_path, &bytes);
                return Err(LedgerError::Incompatible(format!(
                    "expected schema {}, got {}",
                    LEDGER_SCHEMA_VERSION, state.schema_version
                )));
            }
            if let Err(error) = validate_state_with_limits(&state, limits) {
                quarantine_bytes(&state_path, &bytes);
                return Err(error);
            }
            (state, false)
        };

        if !fresh {
            state.broker_epoch = state
                .broker_epoch
                .checked_next()
                .ok_or_else(|| LedgerError::Incompatible("broker epoch overflow".to_owned()))?;
            state.connection_epoch = ConnectionEpoch::new(0);
            state.connection_principal_id = None;
            state.connection_nonce = None;
            state.connection_profile_binding_id = None;
            state.event_sequence = EventSequence::new(0);
            state.events.clear();
            recover_after_restart(&mut state)?;
        }

        let ledger = Self {
            directory,
            state_path,
            lock,
            state,
            limits,
        };
        ledger.validate_state()?;
        ledger.persist_current()?;
        Ok(ledger)
    }

    /// Borrow the current logical state.
    pub(crate) fn state(&self) -> &LedgerState {
        &self.state
    }

    /// Return the state directory owned by this ledger.
    pub fn directory(&self) -> &Path {
        &self.directory
    }

    /// Return the current broker epoch.
    pub fn broker_epoch(&self) -> BrokerEpoch {
        self.state.broker_epoch
    }

    /// Return the configured bounds.
    pub fn limits(&self) -> LedgerLimits {
        self.limits
    }

    /// Apply one serialized state transition and atomically persist it.
    pub(crate) fn update<T, F>(&mut self, transition: F) -> Result<T, HostError>
    where
        F: FnOnce(&mut LedgerState) -> Result<T, HostError>,
    {
        self.lock.ensure_owner().map_err(HostError::Ledger)?;
        let previous = self.state.clone();
        let result = transition(&mut self.state);
        match result {
            Ok(value) => {
                if self.state.events.len() > self.limits.max_events {
                    let remove = self.state.events.len() - self.limits.max_events;
                    self.state.events.drain(0..remove);
                }
                if let Err(error) = self.validate_state().and_then(|_| self.persist_current()) {
                    self.state = previous;
                    return Err(HostError::Ledger(error));
                }
                Ok(value)
            }
            Err(error) => {
                self.state = previous;
                Err(error)
            }
        }
    }

    /// Verify lock ownership without changing state.
    pub fn check_owner(&self) -> Result<(), LedgerError> {
        self.lock.ensure_owner()
    }

    fn validate_state(&self) -> Result<(), LedgerError> {
        validate_state_with_limits(&self.state, self.limits)
    }

    fn persist_current(&self) -> Result<(), LedgerError> {
        self.lock.ensure_owner()?;
        if path_is_symlink(&self.state_path)? {
            return Err(LedgerError::Ownership(
                "ledger path is a symlink".to_owned(),
            ));
        }
        if path_is_present(&self.state_path)? {
            let metadata = fs::metadata(&self.state_path).map_err(LedgerError::Io)?;
            ensure_private_file(&metadata)?;
        }
        let bytes = serde_json::to_vec_pretty(&self.state).map_err(LedgerError::Serialization)?;
        if bytes.len() > self.limits.max_ledger_bytes {
            return Err(LedgerError::BoundExceeded(
                "serialized ledger bytes".to_owned(),
            ));
        }
        let suffix = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temp_path = self
            .directory
            .join(format!("ledger.json.tmp-{}-{suffix}", std::process::id()));
        let result = (|| {
            let mut temporary = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp_path)
                .map_err(LedgerError::Io)?;
            set_private_file(&temporary)?;
            temporary.write_all(&bytes).map_err(LedgerError::Io)?;
            temporary.sync_all().map_err(LedgerError::Io)?;
            if path_is_symlink(&self.state_path)? {
                return Err(LedgerError::Ownership(
                    "ledger path was replaced".to_owned(),
                ));
            }
            fs::rename(&temp_path, &self.state_path).map_err(LedgerError::Io)?;
            let directory = File::open(&self.directory).map_err(LedgerError::Io)?;
            directory.sync_all().map_err(LedgerError::Io)?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        result
    }
}

fn validate_state_with_limits(
    state: &LedgerState,
    limits: LedgerLimits,
) -> Result<(), LedgerError> {
    if state.schema_version != LEDGER_SCHEMA_VERSION {
        return Err(LedgerError::Incompatible(format!(
            "expected schema {}, got {}",
            LEDGER_SCHEMA_VERSION, state.schema_version
        )));
    }
    // Connection authority is process-local and skipped by serde. The durable
    // ledger retains only the monotonic epoch counter; all live sessions must
    // reconnect after restart.
    if state.spaces.len() > limits.max_spaces {
        return Err(LedgerError::BoundExceeded("space count".to_owned()));
    }
    if state.actions.len() > limits.max_actions {
        return Err(LedgerError::BoundExceeded("action count".to_owned()));
    }
    if state.events.len() > limits.max_events {
        return Err(LedgerError::BoundExceeded("event count".to_owned()));
    }
    if state.control_tickets.len() > state.spaces.len() {
        return Err(LedgerError::Corrupt("too many control tickets".to_owned()));
    }
    if state.pending_fences.len() > state.spaces.len()
        || state.takeover_proofs.len() > state.spaces.len()
    {
        return Err(LedgerError::Corrupt("too many fence indexes".to_owned()));
    }

    let mut page_ids = BTreeSet::new();
    for (space_id, space) in &state.spaces {
        if &space.space_id != space_id {
            return Err(LedgerError::Corrupt(
                "space index does not match descriptor".to_owned(),
            ));
        }
        if space.pages.len() > limits.max_pages_per_space {
            return Err(LedgerError::BoundExceeded(format!(
                "page count for {space_id}"
            )));
        }
        if space.label.is_empty() {
            return Err(LedgerError::Corrupt("space label is empty".to_owned()));
        }
        validate_bounded_text(&space.label, 256, "space label")?;
        if space.warnings.len() > 16 {
            return Err(LedgerError::BoundExceeded("space warnings".to_owned()));
        }
        for warning in &space.warnings {
            validate_bounded_text(warning, 512, "space warning")?;
        }
        if space
            .visual_group_hint
            .as_ref()
            .is_some_and(|hint| validate_bounded_text(hint, 256, "visual group hint").is_err())
        {
            return Err(LedgerError::Corrupt(
                "visual group hint is out of bounds".to_owned(),
            ));
        }
        validate_space_lifecycle(state, space_id, space)?;
        let mut local_page_ids = BTreeSet::new();
        for page in &space.pages {
            if page.space_id != *space_id {
                return Err(LedgerError::Corrupt(
                    "page belongs to another space".to_owned(),
                ));
            }
            if page.label.is_empty() {
                return Err(LedgerError::Corrupt("page label is empty".to_owned()));
            }
            validate_bounded_text(&page.label, 256, "page label")?;
            if page
                .url
                .as_ref()
                .is_some_and(|url| validate_bounded_text(url, 4_096, "page url").is_err())
                || page
                    .title
                    .as_ref()
                    .is_some_and(|title| validate_bounded_text(title, 4_096, "page title").is_err())
            {
                return Err(LedgerError::Corrupt(
                    "page metadata is out of bounds".to_owned(),
                ));
            }
            if !local_page_ids.insert(page.page_id.clone())
                || !page_ids.insert(page.page_id.clone())
            {
                return Err(LedgerError::Corrupt(
                    "duplicate logical page identity".to_owned(),
                ));
            }
            validate_page_lifecycle(page)?;
        }
    }
    if state.space_generations.len() != state.spaces.len()
        || state
            .spaces
            .keys()
            .any(|space_id| !state.space_generations.contains_key(space_id))
    {
        return Err(LedgerError::Corrupt(
            "space generation index is incomplete".to_owned(),
        ));
    }
    if state
        .profile_bindings
        .keys()
        .any(|space_id| !state.spaces.contains_key(space_id))
        || state
            .spaces
            .iter()
            .any(|(space_id, space)| match space.profile_binding {
                ProfileBindingState::Bound => !state.profile_bindings.contains_key(space_id),
                ProfileBindingState::Unbound => state.profile_bindings.contains_key(space_id),
                ProfileBindingState::RebindRequired | ProfileBindingState::Revoked => false,
            })
    {
        return Err(LedgerError::Corrupt(
            "profile binding index is inconsistent".to_owned(),
        ));
    }
    if state
        .extension_worker_instance_epoch
        .is_some_and(|epoch| epoch == 0)
        || state
            .extension_browser_session_epoch
            .is_some_and(|epoch| epoch == 0)
    {
        return Err(LedgerError::Corrupt(
            "extension epoch floor is invalid".to_owned(),
        ));
    }

    for (space_id, space) in &state.spaces {
        if matches!(
            space.lifecycle,
            SpaceLifecycle::FencePending
                | SpaceLifecycle::FenceDispatched
                | SpaceLifecycle::FenceAcknowledged
        ) && !state.pending_fences.contains_key(space_id)
        {
            return Err(LedgerError::Corrupt(
                "fence lifecycle has no pending fence record".to_owned(),
            ));
        }
    }

    for (space_id, ticket) in &state.control_tickets {
        if ticket.space_id != *space_id
            || ticket.broker_epoch != state.broker_epoch
            || !state.spaces.get(space_id).is_some_and(|space| {
                space.lifecycle == SpaceLifecycle::UserOwned
                    && space
                        .lease
                        .as_ref()
                        .is_some_and(|lease| lease.lease_epoch == ticket.fence_epoch)
            })
        {
            return Err(LedgerError::Corrupt(
                "control ticket does not match user-owned space".to_owned(),
            ));
        }
    }

    for (space_id, pending) in &state.pending_fences {
        let Some(space) = state.spaces.get(space_id) else {
            return Err(LedgerError::Corrupt(
                "pending fence references unknown space".to_owned(),
            ));
        };
        let Some(lease) = space.lease.as_ref() else {
            return Err(LedgerError::Corrupt(
                "pending fence has no lease".to_owned(),
            ));
        };
        if pending.space_id != *space_id
            || pending.broker_epoch != state.broker_epoch
            || pending.fence_epoch != lease.lease_epoch
            || pending.principal_id != lease.principal_id
            || space.lifecycle != SpaceLifecycle::FencePending
            || pending
                .old_epoch
                .is_some_and(|old| old >= pending.fence_epoch)
        {
            return Err(LedgerError::Corrupt(
                "pending fence does not match space lease".to_owned(),
            ));
        }
        let valid_purpose = match pending.purpose {
            FencePurpose::Takeover => lease.state == LeaseState::Active,
            FencePurpose::ReturnControl => {
                lease.state == LeaseState::Fenced && pending.old_epoch.is_some()
            }
        };
        if !valid_purpose {
            return Err(LedgerError::Corrupt(
                "pending fence purpose does not match lease state".to_owned(),
            ));
        }
    }

    for (space_id, proofs) in &state.takeover_proofs {
        let Some(space) = state.spaces.get(space_id) else {
            return Err(LedgerError::Corrupt(
                "takeover proof references unknown space".to_owned(),
            ));
        };
        if proofs.is_empty() || proofs.len() > 64 {
            return Err(LedgerError::BoundExceeded(
                "takeover proof count".to_owned(),
            ));
        }
        let mut proof_epochs = BTreeSet::new();
        for proof in proofs {
            if proof.space_id != *space_id
                || proof.broker_epoch != state.broker_epoch
                || proof.previous_epoch >= proof.current_epoch
                || !proof_epochs.insert((proof.previous_epoch, proof.current_epoch))
                || proof.principal_id != space.owner
            {
                return Err(LedgerError::Corrupt(
                    "takeover proof is inconsistent".to_owned(),
                ));
            }
            if proof.current_epoch
                == space
                    .lease
                    .as_ref()
                    .map_or(LeaseEpoch::new(0), |lease| lease.lease_epoch)
                && space.lease.as_ref().is_none_or(|lease| {
                    lease.principal_id != proof.principal_id || lease.state != LeaseState::Active
                })
            {
                return Err(LedgerError::Corrupt(
                    "current takeover proof does not match lease".to_owned(),
                ));
            }
        }
    }

    let mut queued_ids = BTreeSet::new();
    for (space_id, queue) in &state.action_queues {
        if !state.spaces.contains_key(space_id) {
            return Err(LedgerError::Corrupt(
                "action queue references unknown space".to_owned(),
            ));
        }
        if queue.len() > limits.max_queued_actions_per_space {
            return Err(LedgerError::BoundExceeded("queued action count".to_owned()));
        }
        for action_id in queue {
            if !queued_ids.insert(action_id.clone()) {
                return Err(LedgerError::Corrupt(
                    "action appears in multiple queues".to_owned(),
                ));
            }
            let receipt = state.actions.get(action_id).ok_or_else(|| {
                LedgerError::Corrupt("action queue references missing receipt".to_owned())
            })?;
            if receipt.space_id != *space_id || receipt.status != ActionStatus::Queued {
                return Err(LedgerError::Corrupt(
                    "queued action index is inconsistent".to_owned(),
                ));
            }
        }
    }
    if state.action_requests.len() != state.actions.len()
        || state.idempotency.len() != state.actions.len()
    {
        return Err(LedgerError::Corrupt(
            "action indexes do not have matching cardinality".to_owned(),
        ));
    }
    let mut idempotency_ids = BTreeSet::new();
    let mut running_mutations = BTreeSet::new();
    for (action_id, receipt) in &state.actions {
        if &receipt.action_id != action_id {
            return Err(LedgerError::Corrupt(
                "action index does not match receipt".to_owned(),
            ));
        }
        let request = state.action_requests.get(action_id).ok_or_else(|| {
            LedgerError::Corrupt("action receipt has no request context".to_owned())
        })?;
        if request.action_id != *action_id
            || request.request_id != receipt.request_id
            || request.idempotency_key != receipt.idempotency_key
            || request.request_hash != receipt.request_hash
            || request.space_id != receipt.space_id
            || request.page_id != receipt.page_id
            || request.lease_epoch != receipt.lease_epoch
            || request.operation != receipt.operation
            || request.postcondition != receipt.postcondition
            || canonical_action_hash(request)? != receipt.request_hash
        {
            return Err(LedgerError::Corrupt(
                "action receipt and request context disagree".to_owned(),
            ));
        }
        validate_public_payload_shape(&request.payload, 65_536)?;
        validate_action_payload_contract(receipt.operation, &request.payload)?;
        let space = state
            .spaces
            .get(&receipt.space_id)
            .ok_or_else(|| LedgerError::Corrupt("action references unknown space".to_owned()))?;
        if let Some(page_id) = &receipt.page_id {
            let page = space
                .page(page_id)
                .ok_or_else(|| LedgerError::Corrupt("action references unknown page".to_owned()))?;
            if receipt.operation != ActionOperation::Wait
                && receipt.operation != ActionOperation::Screenshot
                && !page.admits_agent_mutations()
                && receipt.status == ActionStatus::Queued
            {
                return Err(LedgerError::Corrupt(
                    "queued action targets a non-managed page".to_owned(),
                ));
            }
        } else if receipt.operation == ActionOperation::Close {
            return Err(LedgerError::Corrupt(
                "close action has no page target".to_owned(),
            ));
        }
        match receipt.status {
            ActionStatus::Queued => {
                if receipt.dispatch_state != DispatchState::NotDispatched
                    || !queued_ids.contains(action_id)
                    || receipt.unknown
                    || receipt.reconciliation_state != ReconciliationState::NotRequired
                    || receipt.completion_source != CompletionSource::None
                {
                    return Err(LedgerError::Corrupt(
                        "queued action state is inconsistent".to_owned(),
                    ));
                }
            }
            ActionStatus::Running => {
                if !matches!(
                    receipt.dispatch_state,
                    DispatchState::Dispatched | DispatchState::Acknowledged
                ) || queued_ids.contains(action_id)
                    || receipt.unknown
                    || receipt.reconciliation_state != ReconciliationState::NotRequired
                {
                    return Err(LedgerError::Corrupt(
                        "running action state is inconsistent".to_owned(),
                    ));
                }
                if is_mutating_action(receipt.operation)
                    && !running_mutations.insert(receipt.space_id.clone())
                {
                    return Err(LedgerError::Corrupt(
                        "more than one mutating action is running in a space".to_owned(),
                    ));
                }
            }
            ActionStatus::Unknown => {
                if !receipt.unknown
                    || !matches!(
                        receipt.reconciliation_state,
                        ReconciliationState::Required | ReconciliationState::InProgress
                    )
                    || queued_ids.contains(action_id)
                    || receipt.completion_source != CompletionSource::None
                    || receipt.next_action != agentyc_core::NextAction::Reconcile
                {
                    return Err(LedgerError::Corrupt(
                        "unknown action state is inconsistent".to_owned(),
                    ));
                }
            }
            ActionStatus::Succeeded => {
                if queued_ids.contains(action_id)
                    || !matches!(
                        receipt.dispatch_state,
                        DispatchState::Dispatched | DispatchState::Acknowledged
                    )
                    || receipt.unknown
                    || !matches!(
                        receipt.reconciliation_state,
                        ReconciliationState::NotRequired | ReconciliationState::ReconciledSucceeded
                    )
                {
                    return Err(LedgerError::Corrupt(
                        "successful action state is inconsistent".to_owned(),
                    ));
                }
            }
            ActionStatus::Failed => {
                if queued_ids.contains(action_id)
                    || !matches!(
                        receipt.dispatch_state,
                        DispatchState::Dispatched | DispatchState::Acknowledged
                    )
                    || receipt.unknown
                    || !matches!(
                        receipt.reconciliation_state,
                        ReconciliationState::NotRequired
                            | ReconciliationState::ReconciledFailed
                            | ReconciliationState::RequiresConfirmation
                    )
                {
                    return Err(LedgerError::Corrupt(
                        "failed action state is inconsistent".to_owned(),
                    ));
                }
            }
            ActionStatus::Cancelled => {
                if queued_ids.contains(action_id)
                    || receipt.dispatch_state != DispatchState::Rejected
                    || receipt.unknown
                    || receipt.reconciliation_state != ReconciliationState::NotRequired
                    || receipt.completion_source != CompletionSource::Ledger
                {
                    return Err(LedgerError::Corrupt(
                        "cancelled action state is inconsistent".to_owned(),
                    ));
                }
            }
        }
        let indexed = state
            .idempotency
            .get(&receipt.idempotency_key)
            .ok_or_else(|| LedgerError::Corrupt("idempotency index is incomplete".to_owned()))?;
        if indexed != action_id || !idempotency_ids.insert(receipt.idempotency_key.clone()) {
            return Err(LedgerError::Corrupt(
                "idempotency index is inconsistent".to_owned(),
            ));
        }
    }

    let mut snapshot_count = 0_usize;
    for (space_id, pages) in &state.snapshots {
        let space = state.spaces.get(space_id).ok_or_else(|| {
            LedgerError::Corrupt("snapshot cache references unknown space".to_owned())
        })?;
        snapshot_count = snapshot_count
            .checked_add(pages.len())
            .ok_or_else(|| LedgerError::BoundExceeded("snapshot count overflow".to_owned()))?;
        for (page_id, record) in pages {
            let page = space.page(page_id).ok_or_else(|| {
                LedgerError::Corrupt("snapshot cache references unknown page".to_owned())
            })?;
            record
                .envelope
                .validate()
                .map_err(|error| LedgerError::Corrupt(error.to_string()))?;
            if record.envelope.space_id != *space_id
                || record.envelope.page_id != *page_id
                || !record.generation.matches_page(page)
            {
                return Err(LedgerError::Corrupt(
                    "snapshot cache provenance is inconsistent".to_owned(),
                ));
            }
            let bytes = serde_json::to_vec(&record.envelope)
                .map_err(LedgerError::Serialization)?
                .len();
            if bytes > limits.max_snapshot_bytes {
                return Err(LedgerError::BoundExceeded("snapshot bytes".to_owned()));
            }
        }
    }
    if snapshot_count > limits.max_snapshots {
        return Err(LedgerError::BoundExceeded("snapshot count".to_owned()));
    }

    let mut previous_sequence = 0_u64;
    for event in &state.events {
        if event.protocol != agentyc_core::PROTOCOL_VERSION
            || event.broker_epoch != state.broker_epoch
            || event.sequence.get() <= previous_sequence
            || event.sequence.get() > state.event_sequence.get()
        {
            return Err(LedgerError::Corrupt(
                "event history is not monotonic".to_owned(),
            ));
        }
        previous_sequence = event.sequence.get();
        validate_public_payload_shape(&event.payload, 512)?;
        if let Some(page_id) = &event.scope.page_id {
            let space_id =
                event.scope.space_id.as_ref().ok_or_else(|| {
                    LedgerError::Corrupt("page event has no space scope".to_owned())
                })?;
            if !state
                .spaces
                .get(space_id)
                .is_some_and(|space| space.page(page_id).is_some())
            {
                return Err(LedgerError::Corrupt(
                    "event references unknown page".to_owned(),
                ));
            }
        } else if let Some(space_id) = &event.scope.space_id
            && !state.spaces.contains_key(space_id)
        {
            return Err(LedgerError::Corrupt(
                "event references unknown space".to_owned(),
            ));
        }
    }
    if state.events.is_empty() && state.event_sequence.get() == 0 {
        // Empty history is valid for a new epoch or an intentionally zero-retention ledger.
    } else if state.events.is_empty() && limits.max_events != 0 {
        return Err(LedgerError::Corrupt(
            "event sequence has no retained history".to_owned(),
        ));
    }

    let bytes = serde_json::to_vec(state).map_err(LedgerError::Serialization)?;
    if bytes.len() > limits.max_ledger_bytes {
        return Err(LedgerError::BoundExceeded(
            "serialized ledger bytes".to_owned(),
        ));
    }
    Ok(())
}

fn validate_bounded_text(value: &str, max_bytes: usize, field: &str) -> Result<(), LedgerError> {
    if value.len() > max_bytes || value.chars().any(char::is_control) {
        return Err(LedgerError::Corrupt(format!("{field} is out of bounds")));
    }
    Ok(())
}

fn validate_space_lifecycle(
    state: &LedgerState,
    space_id: &SpaceId,
    space: &SpaceDescriptor,
) -> Result<(), LedgerError> {
    let lease = space.lease.as_ref();
    let owner_matches = lease.is_none_or(|lease| lease.principal_id == space.owner);
    let valid = match space.lifecycle {
        SpaceLifecycle::Created => lease.is_none(),
        SpaceLifecycle::AgentOwned | SpaceLifecycle::Recovering => {
            lease.is_some_and(|lease| lease.state == LeaseState::Active) && owner_matches
        }
        SpaceLifecycle::FencePending
        | SpaceLifecycle::FenceDispatched
        | SpaceLifecycle::FenceAcknowledged => {
            lease
                .is_some_and(|lease| matches!(lease.state, LeaseState::Active | LeaseState::Fenced))
                && owner_matches
        }
        SpaceLifecycle::UserOwned => {
            lease.is_some_and(|lease| lease.state == LeaseState::Released)
                && state.control_tickets.contains_key(space_id)
        }
        SpaceLifecycle::Orphaned => lease.is_some_and(|lease| {
            matches!(
                lease.state,
                LeaseState::Fenced | LeaseState::Expired | LeaseState::Released
            )
        }),
        SpaceLifecycle::Released | SpaceLifecycle::Finished | SpaceLifecycle::Paused => lease
            .is_none_or(|lease| {
                matches!(
                    lease.state,
                    LeaseState::Released | LeaseState::Fenced | LeaseState::Expired
                )
            }),
        SpaceLifecycle::HandoffRequested | SpaceLifecycle::Draining => lease.is_some(),
    };
    if !valid {
        return Err(LedgerError::Corrupt(format!(
            "invalid lifecycle/lease combination for {space_id}"
        )));
    }
    if space.profile_binding == ProfileBindingState::Revoked
        && space.lifecycle == SpaceLifecycle::AgentOwned
    {
        return Err(LedgerError::Corrupt(
            "revoked profile owns an active space".to_owned(),
        ));
    }
    Ok(())
}

fn validate_page_lifecycle(page: &agentyc_core::PageDescriptor) -> Result<(), LedgerError> {
    let valid = match page.lifecycle {
        PageLifecycle::Planned => {
            page.binding == PageBindingState::Unbound && page.ownership == PageOwnership::Agent
        }
        PageLifecycle::Managed => page.admits_agent_mutations(),
        PageLifecycle::Closing => {
            page.ownership == PageOwnership::Agent && page.binding == PageBindingState::Bound
        }
        PageLifecycle::TargetLost => page.binding == PageBindingState::Lost,
        PageLifecycle::Rebinding | PageLifecycle::Adoptable | PageLifecycle::Unknown => true,
        PageLifecycle::UserOwned => {
            page.binding == PageBindingState::UserOwned && page.ownership == PageOwnership::User
        }
        PageLifecycle::Closed => {
            page.binding == PageBindingState::Closed && page.ownership == PageOwnership::Broker
        }
        PageLifecycle::Retired => page.binding == PageBindingState::Closed,
        PageLifecycle::Creating | PageLifecycle::Unmanaged => true,
    };
    if valid {
        Ok(())
    } else {
        Err(LedgerError::Corrupt(
            "invalid page lifecycle combination".to_owned(),
        ))
    }
}

/// Compute the server-owned idempotency context hash.
pub(crate) fn canonical_action_hash(
    request: &ActionRequest<BTreeMap<String, String>>,
) -> Result<agentyc_core::ContentHash, LedgerError> {
    let context = (
        &request.request_id,
        &request.action_id,
        &request.idempotency_key,
        &request.space_id,
        &request.page_id,
        &request.lease_epoch,
        &request.operation,
        &request.payload,
        &request.postcondition,
    );
    let bytes = serde_json::to_vec(&context).map_err(LedgerError::Serialization)?;
    Ok(agentyc_core::ContentHash::from_bytes(&bytes))
}

pub(crate) fn validate_action_payload_contract(
    operation: ActionOperation,
    payload: &BTreeMap<String, String>,
) -> Result<(), LedgerError> {
    if !matches!(
        operation,
        ActionOperation::Evaluate
            | ActionOperation::CookieWrite
            | ActionOperation::StorageWrite
            | ActionOperation::Upload
    ) {
        return Ok(());
    }
    let approval = payload.get("approval").ok_or_else(|| {
        LedgerError::Corrupt("sensitive action requires explicit approval".to_owned())
    })?;
    if !matches!(approval.as_str(), "true" | "approved" | "confirm") {
        return Err(LedgerError::Corrupt(
            "sensitive action approval must be explicit".to_owned(),
        ));
    }
    let intent = payload.get("user_intent").ok_or_else(|| {
        LedgerError::Corrupt("sensitive action requires explicit user intent".to_owned())
    })?;
    if intent.is_empty()
        || intent.len() > 512
        || intent.chars().any(char::is_control)
        || matches!(intent.as_str(), "false" | "none" | "unspecified")
    {
        return Err(LedgerError::Corrupt(
            "sensitive action user intent is not explicit".to_owned(),
        ));
    }
    Ok(())
}

pub(crate) fn validate_public_payload_shape(
    payload: &BTreeMap<String, String>,
    max_value_bytes: usize,
) -> Result<(), LedgerError> {
    for (key, value) in payload {
        if key.is_empty() || key.len() > 128 || value.len() > max_value_bytes {
            return Err(LedgerError::Corrupt(
                "public payload is out of bounds".to_owned(),
            ));
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
            return Err(LedgerError::Corrupt(
                "public payload contains a raw browser identity field".to_owned(),
            ));
        }
    }
    Ok(())
}

fn recover_after_restart(state: &mut LedgerState) -> Result<(), LedgerError> {
    state.pending_fences.clear();
    state.takeover_proofs.clear();
    for ticket in state.control_tickets.values_mut() {
        ticket.broker_epoch = state.broker_epoch;
    }
    let mut lost_spaces = BTreeSet::new();
    for (space_id, space) in &mut state.spaces {
        if let Some(lease) = &mut space.lease
            && matches!(lease.state, LeaseState::Active | LeaseState::Renewing)
        {
            lease.state = LeaseState::Fenced;
        }
        if matches!(
            space.lifecycle,
            SpaceLifecycle::AgentOwned
                | SpaceLifecycle::HandoffRequested
                | SpaceLifecycle::Draining
                | SpaceLifecycle::Recovering
                | SpaceLifecycle::FencePending
                | SpaceLifecycle::FenceAcknowledged
                | SpaceLifecycle::FenceDispatched
        ) {
            space.lifecycle = SpaceLifecycle::Orphaned;
            lost_spaces.insert(space_id.clone());
        }
    }
    for space_id in &lost_spaces {
        if let Some(space) = state.spaces.get_mut(space_id) {
            for page in &mut space.pages {
                bump_page_generations(page)?;
            }
        }
        state.snapshots.remove(space_id);
    }
    let orphaned_queues: Vec<Vec<ActionId>> = lost_spaces
        .iter()
        .filter_map(|space_id| state.action_queues.remove(space_id))
        .collect();
    for queue in orphaned_queues {
        for action_id in queue {
            if let Some(receipt) = state.actions.get_mut(&action_id)
                && receipt.status == ActionStatus::Queued
            {
                receipt
                    .cancel(None)
                    .map_err(|error| LedgerError::Incompatible(error.to_string()))?;
            }
        }
    }

    let mut recovery_number = 0_u64;
    for receipt in state.actions.values_mut() {
        if receipt.status != ActionStatus::Running {
            continue;
        }
        let token = ReconcileToken::from_suffix(format!("restart-{recovery_number}"))
            .map_err(|error| LedgerError::Incompatible(error.to_string()))?;
        recovery_number = recovery_number
            .checked_add(1)
            .ok_or_else(|| LedgerError::Incompatible("recovery counter overflow".to_owned()))?;
        if matches!(
            receipt.dispatch_state,
            DispatchState::Dispatched | DispatchState::Acknowledged
        ) {
            receipt
                .mark_unknown(UnknownReason::HostRestarted, token)
                .map_err(|error| LedgerError::Incompatible(error.to_string()))?;
        } else {
            receipt.status = ActionStatus::Unknown;
            receipt.unknown = true;
            receipt.unknown_reason = Some(UnknownReason::HostRestarted);
            receipt.reconciliation_state = ReconciliationState::Required;
            receipt.reconcile_token = Some(token);
            receipt.error_code = Some(agentyc_core::ErrorCode::UnknownOutcome);
            receipt.retryable = false;
            receipt.completion_source = CompletionSource::Timeout;
            receipt.next_action = agentyc_core::NextAction::Reconcile;
        }
    }
    Ok(())
}

fn is_mutating_action(operation: ActionOperation) -> bool {
    !matches!(
        operation,
        ActionOperation::Wait | ActionOperation::Screenshot
    )
}

fn bump_page_generations(page: &mut agentyc_core::PageDescriptor) -> Result<(), LedgerError> {
    page.target_generation = page
        .target_generation
        .checked_next()
        .ok_or_else(|| LedgerError::Incompatible("target generation overflow".to_owned()))?;
    page.navigation_generation = page
        .navigation_generation
        .checked_next()
        .ok_or_else(|| LedgerError::Incompatible("navigation generation overflow".to_owned()))?;
    page.document_generation = page
        .document_generation
        .checked_next()
        .ok_or_else(|| LedgerError::Incompatible("document generation overflow".to_owned()))?;
    Ok(())
}

fn ensure_directory(path: &Path) -> Result<(), LedgerError> {
    if path_is_symlink(path)? {
        return Err(LedgerError::Ownership(
            "state directory is a symlink".to_owned(),
        ));
    }
    let created = !path.exists();
    if created {
        fs::create_dir_all(path).map_err(LedgerError::Io)?;
    }
    set_private_directory(path)?;
    let metadata = fs::symlink_metadata(path).map_err(LedgerError::Io)?;
    if !metadata.is_dir() {
        return Err(LedgerError::Ownership(
            "state path is not a directory".to_owned(),
        ));
    }
    ensure_private_directory(&metadata)?;
    Ok(())
}

fn path_is_present(path: &Path) -> Result<bool, LedgerError> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(LedgerError::Io(error)),
    }
}

fn path_is_symlink(path: &Path) -> Result<bool, LedgerError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(metadata.file_type().is_symlink()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(LedgerError::Io(error)),
    }
}

fn read_bounded(path: &Path, max_bytes: usize) -> Result<Vec<u8>, LedgerError> {
    if path_is_symlink(path)? {
        return Err(LedgerError::Ownership(
            "ledger path is a symlink".to_owned(),
        ));
    }
    let metadata = fs::metadata(path).map_err(LedgerError::Io)?;
    if !metadata.is_file() {
        return Err(LedgerError::Ownership(
            "ledger path is not a file".to_owned(),
        ));
    }
    ensure_private_file(&metadata)?;
    let length = usize::try_from(metadata.len())
        .map_err(|_| LedgerError::BoundExceeded("ledger file length".to_owned()))?;
    if length > max_bytes {
        return Err(LedgerError::BoundExceeded("ledger file bytes".to_owned()));
    }
    let read_limit = max_bytes
        .checked_add(1)
        .ok_or_else(|| LedgerError::BoundExceeded("read bound overflow".to_owned()))?;
    let file = File::open(path).map_err(LedgerError::Io)?;
    let mut bytes = Vec::with_capacity(length.min(max_bytes));
    file.take(
        u64::try_from(read_limit)
            .map_err(|_| LedgerError::BoundExceeded("read bound conversion".to_owned()))?,
    )
    .read_to_end(&mut bytes)
    .map_err(LedgerError::Io)?;
    if bytes.len() > max_bytes {
        return Err(LedgerError::BoundExceeded(
            "ledger file grew while reading".to_owned(),
        ));
    }
    Ok(bytes)
}

fn ensure_private_directory(metadata: &std::fs::Metadata) -> Result<(), LedgerError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(LedgerError::Ownership(
                "state directory permissions are not private".to_owned(),
            ));
        }
    }
    Ok(())
}

fn set_private_directory(path: &Path) -> Result<(), LedgerError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(LedgerError::Io)?;
    }
    Ok(())
}

fn ensure_private_file(metadata: &std::fs::Metadata) -> Result<(), LedgerError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(LedgerError::Ownership(
                "state file permissions are not private".to_owned(),
            ));
        }
    }
    Ok(())
}

fn set_private_file(file: &File) -> Result<(), LedgerError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(LedgerError::Io)?;
    }
    Ok(())
}

fn quarantine_bytes(path: &Path, bytes: &[u8]) {
    let Some(parent) = path.parent() else {
        return;
    };
    let suffix = QUARANTINE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let quarantine = parent.join(format!(
        "{}.quarantine-{}-{suffix}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("ledger"),
        std::process::id()
    ));
    let result = (|| -> Result<(), LedgerError> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&quarantine)
            .map_err(LedgerError::Io)?;
        set_private_file(&file)?;
        file.write_all(bytes).map_err(LedgerError::Io)?;
        file.sync_all().map_err(LedgerError::Io)?;
        let directory = File::open(parent).map_err(LedgerError::Io)?;
        directory.sync_all().map_err(LedgerError::Io)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(quarantine);
    }
}

struct LedgerLock {
    path: PathBuf,
    file: File,
    token: String,
    identity: LockIdentity,
}

impl LedgerLock {
    fn acquire(path: PathBuf) -> Result<Self, LedgerError> {
        if path_is_symlink(&path)? {
            return Err(LedgerError::Ownership("lock path is a symlink".to_owned()));
        }
        let token = format!(
            "pid={}:lock={}",
            std::process::id(),
            LOCK_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        );
        let mut file = match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return Err(LedgerError::AlreadyOwned);
            }
            Err(error) => return Err(LedgerError::Io(error)),
        };
        let result = (|| {
            set_private_file(&file)?;
            file.write_all(token.as_bytes()).map_err(LedgerError::Io)?;
            file.sync_all().map_err(LedgerError::Io)?;
            let metadata = file.metadata().map_err(LedgerError::Io)?;
            ensure_private_file(&metadata)?;
            let identity = LockIdentity::from_metadata(&metadata);
            Ok(Self {
                path: path.clone(),
                file,
                token: token.clone(),
                identity,
            })
        })();
        match result {
            Ok(lock) => Ok(lock),
            Err(error) => {
                let _ = fs::remove_file(&path);
                Err(error)
            }
        }
    }

    fn ensure_owner(&self) -> Result<(), LedgerError> {
        if path_is_symlink(&self.path)? {
            return Err(LedgerError::Ownership(
                "lock path was replaced by a symlink".to_owned(),
            ));
        }
        let metadata = fs::metadata(&self.path).map_err(LedgerError::Io)?;
        ensure_private_file(&metadata)?;
        if !metadata.is_file() || !self.identity.matches(&metadata) {
            return Err(LedgerError::Ownership("lock owner changed".to_owned()));
        }
        let token = read_bounded(&self.path, MAX_LOCK_TOKEN_BYTES).and_then(|bytes| {
            String::from_utf8(bytes).map_err(|error| {
                LedgerError::Ownership(format!("lock token is not utf-8: {error}"))
            })
        })?;
        if token != self.token {
            return Err(LedgerError::Ownership("lock token changed".to_owned()));
        }
        Ok(())
    }
}

impl Drop for LedgerLock {
    fn drop(&mut self) {
        let _ = self.file.sync_all();
        let should_remove = !path_is_symlink(&self.path).unwrap_or(true)
            && fs::metadata(&self.path)
                .map(|metadata| {
                    metadata.is_file()
                        && ensure_private_file(&metadata).is_ok()
                        && self.identity.matches(&metadata)
                })
                .unwrap_or(false)
            && read_bounded(&self.path, MAX_LOCK_TOKEN_BYTES)
                .ok()
                .and_then(|bytes| String::from_utf8(bytes).ok())
                .is_some_and(|token| token == self.token);
        if should_remove {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[derive(Clone, Copy)]
struct LockIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(not(unix))]
    length: u64,
}

impl LockIdentity {
    fn from_metadata(metadata: &std::fs::Metadata) -> Self {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            Self {
                device: metadata.dev(),
                inode: metadata.ino(),
            }
        }
        #[cfg(not(unix))]
        {
            Self {
                length: metadata.len(),
            }
        }
    }

    fn matches(self, metadata: &std::fs::Metadata) -> bool {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            self.device == metadata.dev() && self.inode == metadata.ino()
        }
        #[cfg(not(unix))]
        {
            self.length == metadata.len()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn lock_is_exclusive_and_released_by_owner() {
        let directory = tempdir().expect("tempdir");
        let first = Ledger::open(directory.path()).expect("first owner");
        assert!(matches!(
            Ledger::open(directory.path()),
            Err(LedgerError::AlreadyOwned)
        ));
        drop(first);
        Ledger::open(directory.path()).expect("lock released");
    }

    #[test]
    fn malformed_json_fails_closed_without_replacement() {
        let directory = tempdir().expect("tempdir");
        let first = Ledger::open(directory.path()).expect("create");
        drop(first);
        let state_path = directory.path().join("ledger.json");
        fs::write(&state_path, b"not json").expect("corrupt");
        let error = Ledger::open(directory.path()).expect_err("corrupt ledger");
        assert!(matches!(error, LedgerError::Corrupt(_)));
        assert_eq!(fs::read(&state_path).expect("read"), b"not json");
    }
}
