//! Chrome Native Messaging transport and the production extension bridge.
//!
//! This module is intentionally separate from [`crate::protocol`]. Chrome's
//! Native Messaging transport uses a four-byte little-endian length prefix,
//! while the agent/local protocol uses the core codec's four-byte big-endian
//! prefix. Mixing the two transports would make framing and failure handling
//! ambiguous.

use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    io::{self, Read, Write},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, SyncSender},
    },
    thread,
    time::Duration,
};

use agentyc_core::{
    ActionReceipt, ActionRequest, BrokerEpoch, Capability, ClientId, ClientMetadata,
    ConnectionNonce, CoreError, ErrorCode, HelloEnvelope, LeaseEpoch, PROTOCOL_VERSION, PageId,
    PrincipalId, ProfileBindingId, SnapshotEnvelope, SpaceId, UnknownReason,
};
use serde_json::{Map, Value, json};
use thiserror::Error;

use crate::bridge::{
    Bridge, BridgeDispatchResult, BridgeReconcileResult, BridgeStatus, ExtensionEpochs,
    FenceResult, ObservationSnapshot, sanitize_observation_snapshot,
};

/// Maximum control payload accepted from or sent to Chrome.
///
/// Chrome permits a larger extension-to-host ceiling, but the product keeps a
/// symmetric one-megabyte control bound and uses separate artifact chunks.
pub const MAX_NATIVE_CONTROL_BYTES: usize = 1024 * 1024;
/// Maximum amount read from the Native Messaging pipe in one operation.
pub const MAX_NATIVE_READ_CHUNK_BYTES: usize = 64 * 1024;
/// Maximum queued unsolicited events retained for the host.
pub const MAX_NATIVE_EVENT_QUEUE: usize = 256;
/// Maximum inbound extension requests retained for the host supervisor.
pub const MAX_NATIVE_REQUEST_QUEUE: usize = 256;
/// Maximum number of request/response operations waiting on one connection.
pub const MAX_NATIVE_PENDING_REQUESTS: usize = 256;
/// Default time allowed for an extension response.
pub const DEFAULT_NATIVE_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
/// Default time allowed for the first extension hello.
pub const DEFAULT_NATIVE_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);

const MAX_NATIVE_DEPTH: usize = 8;
const MAX_NATIVE_COLLECTION_ITEMS: usize = 256;
const MAX_NATIVE_STRING_BYTES: usize = 64 * 1024;
const MAX_NATIVE_TIMED_OUT_REQUESTS: usize = 256;
const MAX_NATIVE_UNKNOWN_ACTIONS: usize = 128;

/// Errors raised by the Chrome Native Messaging boundary.
#[derive(Debug, Clone, Error, PartialEq, Eq)]
pub enum NativeHostError {
    /// The peer sent a malformed, stale, replayed, or unsupported message.
    #[error("native messaging protocol rejected input: {0}")]
    Protocol(String),
    /// The pipe closed or the writer could not be used.
    #[error("native messaging transport is unavailable: {0}")]
    Unavailable(String),
    /// A bounded request exceeded its response deadline.
    #[error("native messaging request timed out")]
    Timeout,
    /// The host received an invalid or missing configured extension origin.
    #[error("native messaging extension origin is invalid")]
    OriginInvalid,
    /// A bounded Native Messaging payload exceeded the product limit.
    #[error("native messaging payload exceeds the bound")]
    MessageTooLarge,
}

impl NativeHostError {
    fn disconnected() -> Self {
        Self::Unavailable("Native Messaging pipe closed".to_owned())
    }

    fn as_core_error(&self) -> CoreError {
        match self {
            Self::Timeout => CoreError::new(ErrorCode::Timeout, self.to_string()),
            Self::MessageTooLarge => CoreError::new(ErrorCode::MessageTooLarge, self.to_string()),
            Self::Protocol(_) | Self::OriginInvalid => {
                CoreError::new(ErrorCode::ProtocolMismatch, self.to_string())
            }
            Self::Unavailable(_) => {
                CoreError::new(ErrorCode::NativeHostUnavailable, self.to_string())
            }
        }
    }
}

/// Configuration for one Chrome Native Messaging connection.
#[derive(Debug, Clone)]
pub struct NativeMessagingConfig {
    /// Exact extension origin registered for this host.
    pub expected_origin: String,
    /// Maximum wait for the extension's first hello message.
    pub handshake_timeout: Duration,
    /// Maximum wait for a correlated extension response.
    pub request_timeout: Duration,
}

impl NativeMessagingConfig {
    /// Construct configuration after validating the exact stable extension origin.
    pub fn new(expected_origin: impl Into<String>) -> Result<Self, NativeHostError> {
        let expected_origin = normalize_extension_origin(&expected_origin.into())?;
        Ok(Self {
            expected_origin,
            handshake_timeout: DEFAULT_NATIVE_HANDSHAKE_TIMEOUT,
            request_timeout: DEFAULT_NATIVE_REQUEST_TIMEOUT,
        })
    }

    /// Change the handshake timeout without changing origin admission.
    #[must_use]
    pub const fn with_handshake_timeout(mut self, timeout: Duration) -> Self {
        self.handshake_timeout = timeout;
        self
    }

    /// Change the request timeout without changing origin admission.
    #[must_use]
    pub const fn with_request_timeout(mut self, timeout: Duration) -> Self {
        self.request_timeout = timeout;
        self
    }
}

/// Extension metadata received from Chrome's transport peer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NativeHello {
    /// Negotiated protocol requested by the extension.
    pub protocol: u16,
    /// Fresh extension-side connection nonce.
    pub nonce: String,
    /// First extension-to-host sequence, which must be one.
    pub sequence: u64,
    /// MV3 worker instance epoch.
    pub worker_instance_epoch: u64,
    /// Browser/profile session epoch.
    pub browser_session_epoch: u64,
    /// Logical profile binding selected by the extension.
    pub profile_instance_id: String,
    /// Installed extension version.
    pub extension_version: String,
    /// Extension capability strings.
    pub capabilities: Vec<String>,
}

impl NativeHello {
    /// Convert the Native Messaging hello into the core host handshake.
    pub fn to_core_hello(
        &self,
        principal_id: PrincipalId,
    ) -> Result<HelloEnvelope, NativeHostError> {
        let nonce = ConnectionNonce::new(self.nonce.clone())
            .map_err(|_| NativeHostError::Protocol("hello nonce is not a core nonce".to_owned()))?;
        let profile_binding_id = ProfileBindingId::new(self.profile_instance_id.clone())
            .map_err(|_| NativeHostError::Protocol("profile binding is invalid".to_owned()))?;
        let client_id = ClientId::from_suffix("extension").map_err(|_| {
            NativeHostError::Protocol("extension client identity is invalid".to_owned())
        })?;
        Ok(HelloEnvelope {
            protocol: self.protocol,
            supported_protocols: vec![self.protocol],
            principal_id,
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: Some(client_id),
                client_name: Some("agentyc-extension".to_owned()),
                client_version: Some(self.extension_version.clone()),
                connection_nonce: Some(nonce),
                profile_binding_id: Some(profile_binding_id),
            }),
        })
    }
}

#[derive(Debug, Clone)]
struct SessionMetadata {
    expected_origin: String,
    hello: NativeHello,
    broker_epoch: Option<u64>,
    connection_epoch: Option<u64>,
    next_inbound_sequence: u64,
    next_outbound_sequence: u64,
    handshake_complete: bool,
    capabilities: Vec<Capability>,
}

/// One validated inbound request waiting for the native-host supervisor.
#[derive(Debug, Clone, PartialEq)]
pub struct NativeRequest {
    /// Extension-generated logical request identity used for response correlation.
    pub request_id: String,
    /// Optional extension-generated logical mutation identity.
    pub action_id: Option<String>,
    /// Logical host method requested by the extension.
    pub method: String,
    /// Untrusted request parameters retained until supervisor validation.
    pub params: Value,
}

#[derive(Debug, Default)]
struct InventoryState {
    pages: BTreeMap<(String, String), Value>,
    unmanaged_pages: Vec<Value>,
    groups: Vec<Value>,
    safety: Option<Value>,
    recovery_observed: bool,
    unknown_action_ids: BTreeSet<String>,
    unknown_actions_overflow: bool,
}

struct NativeShared {
    writer: Mutex<Box<dyn Write + Send>>,
    outbound: Mutex<()>,
    session: Mutex<SessionMetadata>,
    pending: Mutex<BTreeMap<String, SyncSender<Result<Value, NativeHostError>>>>,
    timed_out: Mutex<BTreeSet<String>>,
    events: Mutex<VecDeque<Value>>,
    requests: Mutex<VecDeque<NativeRequest>>,
    closed: Mutex<Option<NativeHostError>>,
    closed_cv: Condvar,
    request_counter: AtomicU64,
    request_timeout: Duration,
    inventory: Mutex<InventoryState>,
}

/// A cloneable, synchronous bridge backed by one persistent Native Messaging pipe.
///
/// The broker calls the existing synchronous [`Bridge`] trait. A dedicated
/// reader thread handles the bidirectional Chrome pipe, validates inbound
/// sequence/epoch state, and routes responses by `request_id`; bridge methods
/// block only on their own bounded response channel.
#[derive(Clone)]
pub struct NativeMessagingBridge {
    shared: Arc<NativeShared>,
}

impl NativeMessagingBridge {
    /// Accept a Chrome-launched Native Messaging stream and wait for its hello.
    ///
    /// The caller must invoke [`Self::complete_handshake`] after admitting the
    /// hello through the host broker. Until then no extension request is sent.
    pub fn accept<R, W>(
        reader: R,
        writer: W,
        config: NativeMessagingConfig,
    ) -> Result<(NativeHello, Self), NativeHostError>
    where
        R: Read + Send + 'static,
        W: Write + Send + 'static,
    {
        let (hello_tx, hello_rx) = mpsc::sync_channel(1);
        let shared = Arc::new(NativeShared {
            writer: Mutex::new(Box::new(writer)),
            outbound: Mutex::new(()),
            session: Mutex::new(SessionMetadata {
                expected_origin: config.expected_origin.clone(),
                hello: NativeHello {
                    protocol: 0,
                    nonce: String::new(),
                    sequence: 0,
                    worker_instance_epoch: 0,
                    browser_session_epoch: 0,
                    profile_instance_id: String::new(),
                    extension_version: String::new(),
                    capabilities: Vec::new(),
                },
                broker_epoch: None,
                connection_epoch: None,
                next_inbound_sequence: 1,
                next_outbound_sequence: 1,
                handshake_complete: false,
                capabilities: Vec::new(),
            }),
            pending: Mutex::new(BTreeMap::new()),
            timed_out: Mutex::new(BTreeSet::new()),
            events: Mutex::new(VecDeque::new()),
            requests: Mutex::new(VecDeque::new()),
            closed: Mutex::new(None),
            closed_cv: Condvar::new(),
            request_counter: AtomicU64::new(1),
            request_timeout: config.request_timeout,
            inventory: Mutex::new(InventoryState::default()),
        });
        let reader_shared = Arc::clone(&shared);
        thread::Builder::new()
            .name("agentyc-native-reader".to_owned())
            .spawn(move || read_loop(Box::new(reader), reader_shared, hello_tx))
            .map_err(|_| {
                NativeHostError::Unavailable("reader thread could not start".to_owned())
            })?;

        let hello =
            hello_rx
                .recv_timeout(config.handshake_timeout)
                .map_err(|error| match error {
                    mpsc::RecvTimeoutError::Timeout => NativeHostError::Timeout,
                    mpsc::RecvTimeoutError::Disconnected => NativeHostError::disconnected(),
                })??;
        Ok((hello, Self { shared }))
    }

    /// Accept the process's standard input/output streams as a Native Messaging peer.
    pub fn accept_stdio(
        config: NativeMessagingConfig,
    ) -> Result<(NativeHello, Self), NativeHostError> {
        Self::accept(io::stdin(), io::stdout(), config)
    }

    /// Send a bounded unsolicited host event to the extension.
    pub fn send_event(&self, event: &str, payload: Value) -> Result<(), NativeHostError> {
        let mut fields = Map::new();
        fields.insert("event".to_owned(), Value::String(event.to_owned()));
        fields.insert("payload".to_owned(), payload);
        self.post("event", fields)
    }

    /// Send the host handshake acknowledgement after the broker admits the peer.
    pub fn complete_handshake(
        &self,
        hello: &NativeHello,
        broker_epoch: BrokerEpoch,
        connection_epoch: agentyc_core::ConnectionEpoch,
        capabilities: &[Capability],
    ) -> Result<(), NativeHostError> {
        let mut session =
            self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
        if session.hello != *hello {
            return Err(NativeHostError::Protocol(
                "handshake hello does not match the accepted connection".to_owned(),
            ));
        }
        if session.handshake_complete {
            return Err(NativeHostError::Protocol(
                "duplicate host handshake acknowledgement".to_owned(),
            ));
        }
        session.broker_epoch = Some(broker_epoch.get());
        session.connection_epoch = Some(connection_epoch.get());
        session.capabilities = capabilities.to_vec();
        let extension_capabilities = hello.capabilities.clone();
        let envelope = json!({
            "protocol": PROTOCOL_VERSION,
            "kind": "hello_ok",
            "nonce": hello.nonce,
            "sequence": session.next_outbound_sequence,
            "broker_epoch": broker_epoch.get(),
            "connection_epoch": connection_epoch.get(),
            "worker_instance_epoch": hello.worker_instance_epoch,
            "browser_session_epoch": hello.browser_session_epoch,
            "capabilities": extension_capabilities,
        });
        session.next_outbound_sequence = session
            .next_outbound_sequence
            .checked_add(1)
            .ok_or_else(|| NativeHostError::Protocol("outbound sequence overflow".to_owned()))?;
        // Chrome may post inventory immediately after receiving hello_ok.
        session.handshake_complete = true;
        drop(session);
        write_envelope(&self.shared, &envelope)
    }

    /// Return the extension hello accepted by this connection.
    pub fn hello(&self) -> Result<NativeHello, NativeHostError> {
        self.shared
            .session
            .lock()
            .map(|session| session.hello.clone())
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))
    }

    /// Wait until Chrome closes the Native Messaging connection.
    pub fn wait_closed(&self) -> NativeHostError {
        let mut closed = self
            .shared
            .closed
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        while closed.is_none() {
            closed = self
                .shared
                .closed_cv
                .wait(closed)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        closed.clone().unwrap_or_else(NativeHostError::disconnected)
    }

    /// Return the first terminal transport error without waiting.
    pub fn closed(&self) -> Option<NativeHostError> {
        self.shared
            .closed
            .lock()
            .map(|closed| closed.clone())
            .unwrap_or_else(|_| {
                Some(NativeHostError::Unavailable(
                    "closed state is poisoned".to_owned(),
                ))
            })
    }

    /// Return whether the reader has observed a terminal transport failure.
    pub fn is_closed(&self) -> bool {
        self.closed().is_some()
    }

    /// Return and clear bounded inbound extension requests without waiting.
    pub fn drain_requests(&self) -> Vec<NativeRequest> {
        let mut requests = self
            .shared
            .requests
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        requests.drain(..).collect()
    }

    /// Return and clear bounded unsolicited extension events.
    pub fn drain_events(&self) -> Vec<Value> {
        let mut events = self
            .shared
            .events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        events.drain(..).collect()
    }

    /// Return the latest logical inventory records observed from the extension.
    pub fn inventory(&self) -> Vec<Value> {
        self.inventory_snapshot().pages
    }

    /// Return the latest cached logical pages and visual group hints.
    pub fn inventory_snapshot(&self) -> ObservationSnapshot {
        self.shared
            .inventory
            .lock()
            .map(|inventory| {
                let mut pages: Vec<_> = inventory.pages.values().cloned().collect();
                pages.extend(inventory.unmanaged_pages.iter().cloned());
                ObservationSnapshot {
                    pages,
                    groups: inventory.groups.clone(),
                    safety: inventory.safety.clone(),
                    recovery_observed: inventory.recovery_observed,
                }
            })
            .unwrap_or_default()
    }

    /// Request a fresh bounded inventory from the extension.
    fn live_inventory(&self) -> Result<ObservationSnapshot, CoreError> {
        let result = self.request_value("tab.inventory", Map::new(), None)?;
        let pages = result
            .get("pages")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::InvalidJson,
                    "live extension inventory pages are missing",
                )
            })?;
        let groups = result
            .get("groups")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::InvalidJson,
                    "live extension inventory groups are missing",
                )
            })?;
        let safety = result.get("safety").cloned();
        let recovery_observed = result
            .get("recovery_observed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let expected_session_epoch = self
            .hello()
            .map_err(|error| error.as_core_error())?
            .browser_session_epoch;
        for page in pages {
            if let Some(page_epoch) = page.get("browser_session_epoch").and_then(Value::as_u64)
                && page_epoch != expected_session_epoch
            {
                return Err(CoreError::new(
                    ErrorCode::TargetReplaced,
                    "live inventory page belongs to another browser session",
                ));
            }
        }
        sanitize_observation_snapshot(ObservationSnapshot {
            pages: pages.to_vec(),
            groups: groups.to_vec(),
            safety,
            recovery_observed,
        })
    }

    /// Return extension-reported mutation outcomes that require host reconciliation.
    pub fn inventory_unknown_actions(&self) -> (Vec<String>, bool) {
        self.shared
            .inventory
            .lock()
            .map(|inventory| {
                (
                    inventory.unknown_action_ids.iter().cloned().collect(),
                    inventory.unknown_actions_overflow,
                )
            })
            .unwrap_or_default()
    }

    /// Ask the extension to create one inactive agent page.
    ///
    /// This is an explicit host helper used by the coexistence executor. The
    /// logical page and host-issued proof remain the authorization boundary;
    /// the returned value is kept in memory only.
    pub fn create_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        url: Option<&str>,
        title: Option<&str>,
        ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("page_id".to_owned(), json!(page_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(lease_epoch.get()));
        params.insert("ownership_proof".to_owned(), ownership_proof);
        if let Some(url) = url {
            params.insert("url".to_owned(), json!(url));
        }
        if let Some(title) = title {
            params.insert("title".to_owned(), json!(title));
        }
        self.request_value("page.create", params, None)
    }

    /// Ask the extension to present a page in its logical space's visual group.
    /// Rebind one retained page after an acknowledged lease fence.
    pub fn rebind_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        target_generation: u64,
        navigation_generation: u64,
        document_generation: u64,
        ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("page_id".to_owned(), json!(page_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(lease_epoch.get()));
        params.insert("target_generation".to_owned(), json!(target_generation));
        params.insert(
            "navigation_generation".to_owned(),
            json!(navigation_generation),
        );
        params.insert("document_generation".to_owned(), json!(document_generation));
        params.insert("ownership_proof".to_owned(), ownership_proof);
        self.request_value("page.rebind", params, None)
    }

    /// Present one managed page in its space's visual tab group.
    pub fn present_group(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        title: Option<&str>,
    ) -> Result<Value, CoreError> {
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("page_id".to_owned(), json!(page_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(lease_epoch.get()));
        if let Some(title) = title {
            params.insert("title".to_owned(), json!(title));
        }
        self.request_value("group.present", params, None)
    }

    fn request_value(
        &self,
        method: &str,
        params: Map<String, Value>,
        action_id: Option<&str>,
    ) -> Result<Value, CoreError> {
        let request_id = self.next_request_id();
        let (sender, receiver) = mpsc::sync_channel(1);
        {
            let mut pending = self.shared.pending.lock().map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "pending state is poisoned",
                )
            })?;
            if pending.len() >= MAX_NATIVE_PENDING_REQUESTS {
                return Err(CoreError::new(
                    ErrorCode::MessageTooLarge,
                    "Native Messaging pending request bound reached",
                ));
            }
            pending.insert(request_id.clone(), sender);
        }
        let mut fields = Map::new();
        fields.insert("request_id".to_owned(), json!(request_id.clone()));
        fields.insert("method".to_owned(), json!(method));
        fields.insert("params".to_owned(), Value::Object(params));
        if let Some(action_id) = action_id {
            fields.insert("action_id".to_owned(), json!(action_id));
        }
        if let Err(error) = self.post("request", fields) {
            self.shared
                .pending
                .lock()
                .ok()
                .and_then(|mut pending| pending.remove(&request_id));
            return Err(error.as_core_error());
        }
        let response = match receiver.recv_timeout(self.shared.request_timeout) {
            Ok(response) => response.map_err(|error| error.as_core_error())?,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.shared
                    .pending
                    .lock()
                    .ok()
                    .and_then(|mut pending| pending.remove(&request_id));
                self.remember_timed_out(&request_id);
                return Err(CoreError::new(
                    ErrorCode::UnknownOutcome,
                    "Native Messaging response timed out after dispatch",
                ));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.shared
                    .pending
                    .lock()
                    .ok()
                    .and_then(|mut pending| pending.remove(&request_id));
                return Err(CoreError::new(
                    ErrorCode::UnknownOutcome,
                    "Native Messaging response channel disconnected after dispatch",
                ));
            }
        };
        response_result(response)
    }

    /// Return one correlated response to an inbound extension request.
    pub fn respond(
        &self,
        request: &NativeRequest,
        result: Result<Value, CoreError>,
    ) -> Result<(), NativeHostError> {
        if agentyc_core::RequestId::new(request.request_id.clone()).is_err() {
            return Err(NativeHostError::Protocol(
                "Native Messaging request_id is not a logical request id".to_owned(),
            ));
        }
        if let Some(action_id) = &request.action_id
            && agentyc_core::ActionId::new(action_id.clone()).is_err()
        {
            return Err(NativeHostError::Protocol(
                "Native Messaging action_id is not a logical action id".to_owned(),
            ));
        }
        let mut fields = Map::new();
        fields.insert("request_id".to_owned(), json!(request.request_id));
        if let Some(action_id) = &request.action_id {
            fields.insert("action_id".to_owned(), json!(action_id));
        }
        match result {
            Ok(result) => {
                assert_no_raw_browser_identifiers(&result, "result")?;
                fields.insert("ok".to_owned(), json!(true));
                fields.insert("result".to_owned(), result);
            }
            Err(error) => {
                fields.insert("ok".to_owned(), json!(false));
                fields.insert(
                    "error".to_owned(),
                    serde_json::to_value(error).map_err(|_| {
                        NativeHostError::Protocol("response error is not JSON".to_owned())
                    })?,
                );
            }
        }
        self.post("response", fields)
    }

    fn next_request_id(&self) -> String {
        let number = self.shared.request_counter.fetch_add(1, Ordering::Relaxed);
        format!("req_native_{number}")
    }

    fn remember_timed_out(&self, request_id: &str) {
        if let Ok(mut timed_out) = self.shared.timed_out.lock() {
            if timed_out.len() >= MAX_NATIVE_TIMED_OUT_REQUESTS {
                if let Some(oldest) = timed_out.iter().next().cloned() {
                    timed_out.remove(&oldest);
                }
            }
            timed_out.insert(request_id.to_owned());
        }
    }

    fn post(&self, kind: &str, mut fields: Map<String, Value>) -> Result<(), NativeHostError> {
        let _outbound =
            self.shared.outbound.lock().map_err(|_| {
                NativeHostError::Unavailable("outbound state is poisoned".to_owned())
            })?;
        let mut session =
            self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
        if !session.handshake_complete {
            return Err(NativeHostError::Unavailable(
                "Native Messaging handshake is not complete".to_owned(),
            ));
        }
        let broker_epoch = session
            .broker_epoch
            .ok_or_else(|| NativeHostError::Protocol("broker epoch is missing".to_owned()))?;
        let connection_epoch = session
            .connection_epoch
            .ok_or_else(|| NativeHostError::Protocol("connection epoch is missing".to_owned()))?;
        fields.insert("protocol".to_owned(), json!(PROTOCOL_VERSION));
        fields.insert("kind".to_owned(), json!(kind));
        fields.insert("nonce".to_owned(), json!(session.hello.nonce));
        fields.insert("sequence".to_owned(), json!(session.next_outbound_sequence));
        fields.insert("broker_epoch".to_owned(), json!(broker_epoch));
        fields.insert("connection_epoch".to_owned(), json!(connection_epoch));
        fields.insert(
            "worker_instance_epoch".to_owned(),
            json!(session.hello.worker_instance_epoch),
        );
        fields.insert(
            "browser_session_epoch".to_owned(),
            json!(session.hello.browser_session_epoch),
        );
        session.next_outbound_sequence = session
            .next_outbound_sequence
            .checked_add(1)
            .ok_or_else(|| NativeHostError::Protocol("outbound sequence overflow".to_owned()))?;
        drop(session);
        write_envelope_unlocked(&self.shared, &Value::Object(fields))
    }

    fn fence_request(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        let request_id = self.next_request_id();
        let (sender, receiver) = mpsc::sync_channel(1);
        {
            let mut pending = self.shared.pending.lock().map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "pending state is poisoned",
                )
            })?;
            if pending.len() >= MAX_NATIVE_PENDING_REQUESTS {
                return Err(CoreError::new(
                    ErrorCode::MessageTooLarge,
                    "Native Messaging pending request bound reached",
                ));
            }
            pending.insert(request_id.clone(), sender);
        }
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(new_epoch.get()));
        params.insert("fence_epoch".to_owned(), json!(new_epoch.get()));
        params.insert("broker_epoch".to_owned(), json!(broker_epoch.get()));
        let mut fields = Map::new();
        fields.insert("request_id".to_owned(), json!(request_id.clone()));
        fields.insert("space_id".to_owned(), json!(space_id.to_string()));
        fields.insert("lease_epoch".to_owned(), json!(new_epoch.get()));
        fields.insert("fence_epoch".to_owned(), json!(new_epoch.get()));
        fields.insert("broker_epoch".to_owned(), json!(broker_epoch.get()));
        fields.insert("params".to_owned(), Value::Object(params));
        if let Some(old_epoch) = old_epoch {
            fields.insert("old_epoch".to_owned(), json!(old_epoch.get()));
        }
        if let Err(error) = self.post("fence", fields) {
            self.shared
                .pending
                .lock()
                .ok()
                .and_then(|mut pending| pending.remove(&request_id));
            return Err(error.as_core_error());
        }
        let response = match receiver.recv_timeout(self.shared.request_timeout) {
            Ok(response) => response.map_err(|error| error.as_core_error())?,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.shared
                    .pending
                    .lock()
                    .ok()
                    .and_then(|mut pending| pending.remove(&request_id));
                return Err(CoreError::new(
                    ErrorCode::Timeout,
                    "Native Messaging fence acknowledgement timed out",
                ));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.shared
                    .pending
                    .lock()
                    .ok()
                    .and_then(|mut pending| pending.remove(&request_id));
                return Err(CoreError::new(
                    ErrorCode::ExtensionNotConnected,
                    "Native Messaging fence acknowledgement was disconnected",
                ));
            }
        };
        let result = response_result(response)?;
        let acknowledged = result
            .get("fence_epoch")
            .and_then(Value::as_u64)
            .is_some_and(|epoch| epoch == new_epoch.get())
            && result
                .get("space_id")
                .and_then(Value::as_str)
                .is_some_and(|value| value == space_id.as_str());
        Ok(FenceResult { acknowledged })
    }

    fn cleanup_proof(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<Value, CoreError> {
        let inventory = self.live_inventory()?.pages;
        let record = inventory.into_iter().find(|record| {
            record.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
                && record.get("page_id").and_then(Value::as_str) == Some(page_id.as_str())
                && record.get("ownership").and_then(Value::as_str) == Some("agent")
                && record.get("lifecycle").and_then(Value::as_str) == Some("managed")
                && record.get("binding_state").and_then(Value::as_str) == Some("bound")
        });
        let record = record.ok_or_else(|| {
            CoreError::new(
                ErrorCode::PageNotFound,
                "managed page is not present in extension inventory",
            )
        })?;
        let target_generation = record
            .get("target_generation")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                CoreError::new(ErrorCode::TargetReplaced, "page generation is missing")
            })?;
        let tab_hint = record
            .get("tab_hint")
            .and_then(Value::as_str)
            .ok_or_else(|| CoreError::new(ErrorCode::TargetReplaced, "page hint is missing"))?;
        let hello = self.hello().map_err(|error| error.as_core_error())?;
        let profile_instance_id = hello.profile_instance_id;
        let browser_session_epoch = record
            .get("browser_session_epoch")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::TargetReplaced,
                    "browser session epoch is missing",
                )
            })?;
        if browser_session_epoch != hello.browser_session_epoch {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "page belongs to another browser session",
            ));
        }
        if let Some(record_lease_epoch) = record.get("lease_epoch").and_then(Value::as_u64)
            && record_lease_epoch != lease_epoch.get()
        {
            return Err(CoreError::stale_lease(
                record_lease_epoch,
                lease_epoch.get(),
            ));
        }
        Ok(json!({
            "issued_by_host": true,
            "proof_id": self.next_request_id(),
            "kind": "cleanup",
            "purpose": "cleanup",
            "space_id": space_id.to_string(),
            "page_id": page_id.to_string(),
            "lease_epoch": lease_epoch.get(),
            "target_generation": target_generation,
            "tab_hint": tab_hint,
            "profile_instance_id": profile_instance_id,
            "browser_session_epoch": browser_session_epoch,
            "ownership": "agent",
            "expires_at": current_millis().saturating_add(15 * 60 * 1000),
        }))
    }
}

impl Bridge for NativeMessagingBridge {
    fn observe(&self) -> Result<ObservationSnapshot, CoreError> {
        self.live_inventory()
    }

    fn bridge_status(&self) -> Option<BridgeStatus> {
        self.shared.session.lock().ok().map(|session| BridgeStatus {
            profile_instance_id: Some(session.hello.profile_instance_id.clone()),
            extension_version: Some(session.hello.extension_version.clone()),
            worker_instance_epoch: Some(session.hello.worker_instance_epoch),
            browser_session_epoch: Some(session.hello.browser_session_epoch),
        })
    }

    fn create_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        url: Option<&str>,
        title: Option<&str>,
        ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        NativeMessagingBridge::create_page(
            self,
            space_id,
            page_id,
            lease_epoch,
            url,
            title,
            ownership_proof,
        )
    }

    fn rebind_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        target_generation: u64,
        navigation_generation: u64,
        document_generation: u64,
        ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        NativeMessagingBridge::rebind_page(
            self,
            space_id,
            page_id,
            lease_epoch,
            target_generation,
            navigation_generation,
            document_generation,
            ownership_proof,
        )
    }

    fn present_group(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        title: Option<&str>,
    ) -> Result<Value, CoreError> {
        NativeMessagingBridge::present_group(self, space_id, page_id, lease_epoch, title)
    }

    fn capabilities(&self) -> Vec<Capability> {
        self.shared
            .session
            .lock()
            .map(|session| session.capabilities.clone())
            .unwrap_or_default()
    }

    fn extension_epochs(&self) -> Option<ExtensionEpochs> {
        self.shared
            .session
            .lock()
            .ok()
            .map(|session| ExtensionEpochs {
                worker_instance_epoch: session.hello.worker_instance_epoch,
                browser_session_epoch: session.hello.browser_session_epoch,
            })
    }

    fn dispatch(
        &self,
        request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError> {
        let (method, mut params) = action_wire(request.operation);
        params.insert("space_id".to_owned(), json!(request.space_id.to_string()));
        if let Some(page_id) = &request.page_id {
            params.insert("page_id".to_owned(), json!(page_id.to_string()));
        }
        params.insert("lease_epoch".to_owned(), json!(request.lease_epoch.get()));
        params.insert("action_id".to_owned(), json!(request.action_id.to_string()));
        params.insert(
            "request_id".to_owned(),
            json!(request.request_id.to_string()),
        );
        params.insert(
            "payload".to_owned(),
            serde_json::to_value(&request.payload).map_err(|_| {
                CoreError::new(ErrorCode::InvalidJson, "action payload is not JSON")
            })?,
        );
        for (key, value) in &request.payload {
            if !params.contains_key(key) {
                params.insert(key.clone(), Value::String(value.clone()));
            }
        }
        match self.request_value(&method, params, Some(request.action_id.as_str())) {
            Ok(_) => Ok(BridgeDispatchResult::Succeeded),
            Err(error)
                if matches!(
                    error.code,
                    ErrorCode::UnknownOutcome
                        | ErrorCode::NativeHostUnavailable
                        | ErrorCode::Timeout
                ) =>
            {
                Ok(BridgeDispatchResult::Unknown {
                    reason: if error.code == ErrorCode::Timeout {
                        UnknownReason::TimeoutAfterDispatch
                    } else {
                        UnknownReason::BridgeLost
                    },
                })
            }
            Err(error) => Ok(BridgeDispatchResult::Failed {
                code: error.code,
                retryable: error.retryable,
            }),
        }
    }

    fn reconcile(&self, receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError> {
        let mut params = Map::new();
        params.insert("action_id".to_owned(), json!(receipt.action_id.to_string()));
        params.insert("space_id".to_owned(), json!(receipt.space_id.to_string()));
        if let Some(page_id) = &receipt.page_id {
            params.insert("page_id".to_owned(), json!(page_id.to_string()));
        }
        params.insert("lease_epoch".to_owned(), json!(receipt.lease_epoch.get()));
        match self.request_value("action.reconcile", params, None) {
            Ok(result) => match result.get("outcome").and_then(Value::as_str) {
                Some("succeeded") => Ok(BridgeReconcileResult::Succeeded),
                Some("failed") => Ok(BridgeReconcileResult::Failed {
                    code: ErrorCode::InvalidArgument,
                    requires_confirmation: false,
                }),
                _ => Ok(BridgeReconcileResult::StillUnknown),
            },
            Err(error)
                if matches!(
                    error.code,
                    ErrorCode::CapabilityUnavailable | ErrorCode::ExtensionNotConnected
                ) =>
            {
                Ok(BridgeReconcileResult::StillUnknown)
            }
            Err(error) => Err(error),
        }
    }

    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<SnapshotEnvelope, CoreError> {
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("page_id".to_owned(), json!(page_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(lease_epoch.get()));
        let result = self.request_value("snapshot.read", params, None)?;
        serde_json::from_value(result).map_err(|error| {
            CoreError::new(
                ErrorCode::InvalidJson,
                format!("invalid snapshot from extension: {error}"),
            )
        })
    }

    fn fence(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        self.fence_request(space_id, old_epoch, new_epoch, broker_epoch)
    }

    fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError> {
        let proof = self.cleanup_proof(space_id, page_id, lease_epoch)?;
        let mut params = Map::new();
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("page_id".to_owned(), json!(page_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(lease_epoch.get()));
        params.insert("cleanup_proof".to_owned(), proof);
        self.request_value("page.close", params, None).map(|_| ())
    }
}

fn read_loop(
    mut reader: Box<dyn Read + Send>,
    shared: Arc<NativeShared>,
    hello_tx: SyncSender<Result<NativeHello, NativeHostError>>,
) {
    let first = match read_frame(&mut reader) {
        Ok(Some(payload)) => payload,
        Ok(None) => {
            let error = NativeHostError::disconnected();
            let _ = hello_tx.send(Err(error.clone()));
            mark_closed(&shared, error);
            return;
        }
        Err(error) => {
            let _ = hello_tx.send(Err(error.clone()));
            mark_closed(&shared, error);
            return;
        }
    };
    let hello = match parse_hello(&first, &shared) {
        Ok(hello) => hello,
        Err(error) => {
            let _ = hello_tx.send(Err(error.clone()));
            mark_closed(&shared, error);
            return;
        }
    };
    {
        let mut session = match shared.session.lock() {
            Ok(session) => session,
            Err(_) => {
                let error = NativeHostError::Unavailable("session state is poisoned".to_owned());
                let _ = hello_tx.send(Err(error.clone()));
                mark_closed(&shared, error);
                return;
            }
        };
        session.hello = hello.clone();
        session.next_inbound_sequence = 2;
        session.capabilities = map_capabilities(&hello.capabilities);
    }
    if hello_tx.send(Ok(hello)).is_err() {
        mark_closed(
            &shared,
            NativeHostError::Unavailable("hello receiver disappeared".to_owned()),
        );
        return;
    }

    loop {
        let payload = match read_frame(&mut reader) {
            Ok(Some(payload)) => payload,
            Ok(None) => {
                mark_closed(&shared, NativeHostError::disconnected());
                return;
            }
            Err(error) => {
                mark_closed(&shared, error);
                return;
            }
        };
        if let Err(error) = handle_inbound(payload, &shared) {
            mark_closed(&shared, error);
            return;
        }
    }
}

fn parse_hello(payload: &[u8], shared: &NativeShared) -> Result<NativeHello, NativeHostError> {
    let value = parse_bounded_json(payload)?;
    let object = value
        .as_object()
        .ok_or_else(|| NativeHostError::Protocol("hello must be a JSON object".to_owned()))?;
    validate_common(&value, None, 1)?;
    if object.contains_key("origin") {
        return Err(NativeHostError::Protocol(
            "origin is transport metadata and is not accepted in JSON".to_owned(),
        ));
    }
    if object.get("kind").and_then(Value::as_str) != Some("hello") {
        return Err(NativeHostError::Protocol(
            "first Native Messaging message must be hello".to_owned(),
        ));
    }
    let expected_origin = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?
        .expected_origin
        .clone();
    if !is_valid_extension_origin(&expected_origin) {
        return Err(NativeHostError::OriginInvalid);
    }
    let protocol = required_u64(object, "protocol")?;
    let sequence = required_u64(object, "sequence")?;
    if protocol != u64::from(PROTOCOL_VERSION) || sequence != 1 {
        return Err(NativeHostError::Protocol(
            "hello protocol or sequence is invalid".to_owned(),
        ));
    }
    let nonce = required_string(object, "nonce")?;
    let worker_instance_epoch = required_positive_u64(object, "worker_instance_epoch")?;
    let browser_session_epoch = required_positive_u64(object, "browser_session_epoch")?;
    let profile_instance_id = required_string(object, "profile_instance_id")?;
    let extension_version = required_string(object, "extension_version")?;
    let capabilities = object
        .get("capabilities")
        .and_then(Value::as_array)
        .ok_or_else(|| NativeHostError::Protocol("hello capabilities are required".to_owned()))?
        .iter()
        .map(|value| {
            value.as_str().map(str::to_owned).ok_or_else(|| {
                NativeHostError::Protocol("hello capability is not a string".to_owned())
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    if profile_instance_id.len() > 128 || extension_version.len() > 128 {
        return Err(NativeHostError::Protocol(
            "hello metadata is too large".to_owned(),
        ));
    }
    Ok(NativeHello {
        protocol: u16::try_from(protocol)
            .map_err(|_| NativeHostError::Protocol("hello protocol is invalid".to_owned()))?,
        nonce,
        sequence,
        worker_instance_epoch,
        browser_session_epoch,
        profile_instance_id,
        extension_version,
        capabilities,
    })
}

fn handle_inbound(payload: Vec<u8>, shared: &NativeShared) -> Result<(), NativeHostError> {
    let value = parse_bounded_json(&payload)?;
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging envelope is not an object".to_owned())
    })?;
    let expected_sequence = {
        let session = shared
            .session
            .lock()
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
        if !session.handshake_complete {
            return Err(NativeHostError::Protocol(
                "extension sent data before hello_ok".to_owned(),
            ));
        }
        session.next_inbound_sequence
    };
    validate_common(&value, Some(shared), expected_sequence)?;
    if object.contains_key("origin") {
        return Err(NativeHostError::Protocol(
            "origin is transport metadata and is not accepted in JSON".to_owned(),
        ));
    }
    let kind = object
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| NativeHostError::Protocol("Native Messaging kind is missing".to_owned()))?;
    {
        let mut session = shared
            .session
            .lock()
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
        session.next_inbound_sequence = session
            .next_inbound_sequence
            .checked_add(1)
            .ok_or_else(|| NativeHostError::Protocol("inbound sequence overflow".to_owned()))?;
    }
    match kind {
        "response" | "action_result" | "fence_ack" => {
            let request_id = required_string(object, "request_id")?;
            let sender = shared
                .pending
                .lock()
                .map_err(|_| NativeHostError::Unavailable("pending state is poisoned".to_owned()))?
                .remove(&request_id);
            if let Some(sender) = sender {
                let _ = sender.send(Ok(value));
            } else if shared
                .timed_out
                .lock()
                .map_err(|_| NativeHostError::Unavailable("timeout state is poisoned".to_owned()))?
                .remove(&request_id)
            {
                // The response arrived after a bounded timeout. Its sequence
                // was validated above, so ignore only this tombstoned response.
            } else {
                return Err(NativeHostError::Protocol(
                    "response has no pending request".to_owned(),
                ));
            }
        }
        "request" => enqueue_request(shared, value)?,
        "event" => enqueue_event(shared, value)?,
        "inventory" => {
            record_inventory(shared, &value)?;
            enqueue_event(shared, value)?;
        }
        _ => {
            return Err(NativeHostError::Protocol(
                "extension message kind is not accepted by the host".to_owned(),
            ));
        }
    }
    Ok(())
}

fn validate_common(
    value: &Value,
    shared: Option<&NativeShared>,
    expected_sequence: u64,
) -> Result<(), NativeHostError> {
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging envelope is not an object".to_owned())
    })?;
    if object.get("protocol").and_then(Value::as_u64) != Some(u64::from(PROTOCOL_VERSION)) {
        return Err(NativeHostError::Protocol(
            "protocol version is unsupported".to_owned(),
        ));
    }
    if object.get("sequence").and_then(Value::as_u64) != Some(expected_sequence) {
        return Err(NativeHostError::Protocol(
            "Native Messaging sequence is not contiguous".to_owned(),
        ));
    }
    let nonce = required_string(object, "nonce")?;
    if let Some(shared) = shared {
        let session = shared
            .session
            .lock()
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
        if nonce != session.hello.nonce {
            return Err(NativeHostError::Protocol(
                "Native Messaging nonce is stale".to_owned(),
            ));
        }
        if object.get("broker_epoch").and_then(Value::as_u64) != session.broker_epoch
            || object.get("connection_epoch").and_then(Value::as_u64) != session.connection_epoch
            || object.get("worker_instance_epoch").and_then(Value::as_u64)
                != Some(session.hello.worker_instance_epoch)
            || object.get("browser_session_epoch").and_then(Value::as_u64)
                != Some(session.hello.browser_session_epoch)
        {
            return Err(NativeHostError::Protocol(
                "Native Messaging epoch is stale".to_owned(),
            ));
        }
    } else if nonce.len() > 128 {
        return Err(NativeHostError::Protocol(
            "Native Messaging nonce is too large".to_owned(),
        ));
    }
    assert_no_raw_browser_identifiers(value, "")?;
    Ok(())
}

fn enqueue_request(shared: &NativeShared, value: Value) -> Result<(), NativeHostError> {
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging request is not an object".to_owned())
    })?;
    let request_id = required_string(object, "request_id")?;
    if agentyc_core::RequestId::new(request_id.clone()).is_err() {
        return Err(NativeHostError::Protocol(
            "Native Messaging request_id is not a logical request id".to_owned(),
        ));
    }
    let method = required_string(object, "method")?;
    let action_id = object
        .get("action_id")
        .map(|value| {
            let action_id = value.as_str().ok_or_else(|| {
                NativeHostError::Protocol("Native Messaging action_id is invalid".to_owned())
            })?;
            if agentyc_core::ActionId::new(action_id).is_err() {
                return Err(NativeHostError::Protocol(
                    "Native Messaging action_id is not a logical action id".to_owned(),
                ));
            }
            Ok(action_id.to_owned())
        })
        .transpose()?;
    let params = object.get("params").cloned().unwrap_or_else(|| json!({}));
    let request = NativeRequest {
        request_id,
        action_id,
        method,
        params,
    };
    let mut requests = shared
        .requests
        .lock()
        .map_err(|_| NativeHostError::Unavailable("request state is poisoned".to_owned()))?;
    if requests.len() >= MAX_NATIVE_REQUEST_QUEUE {
        return Err(NativeHostError::MessageTooLarge);
    }
    requests.push_back(request);
    Ok(())
}

fn enqueue_event(shared: &NativeShared, value: Value) -> Result<(), NativeHostError> {
    let mut events = shared
        .events
        .lock()
        .map_err(|_| NativeHostError::Unavailable("event state is poisoned".to_owned()))?;
    if events.len() >= MAX_NATIVE_EVENT_QUEUE {
        return Err(NativeHostError::MessageTooLarge);
    }
    events.push_back(value);
    Ok(())
}

fn record_inventory(shared: &NativeShared, value: &Value) -> Result<(), NativeHostError> {
    let payload = value
        .get("payload")
        .and_then(Value::as_object)
        .ok_or_else(|| NativeHostError::Protocol("inventory payload is missing".to_owned()))?;
    let pages = payload
        .get("pages")
        .and_then(Value::as_array)
        .ok_or_else(|| NativeHostError::Protocol("inventory pages are missing".to_owned()))?;
    let groups = payload
        .get("groups")
        .and_then(Value::as_array)
        .ok_or_else(|| NativeHostError::Protocol("inventory groups are missing".to_owned()))?;
    let safety = payload.get("safety").cloned();
    let recovery_observed = payload
        .get("recovery_observed")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let session = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
    let profile_instance_id = payload
        .get("profile_instance_id")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            NativeHostError::Protocol("inventory profile binding is missing".to_owned())
        })?;
    if profile_instance_id != session.hello.profile_instance_id {
        return Err(NativeHostError::Protocol(
            "inventory profile binding does not match the Native Messaging hello".to_owned(),
        ));
    }
    let browser_session_epoch = session.hello.browser_session_epoch;
    drop(session);

    let unknown_action_ids = match payload.get("unknown_action_ids") {
        None => Vec::new(),
        Some(Value::Array(values)) => values
            .iter()
            .map(|value| {
                let action_id = value.as_str().ok_or_else(|| {
                    NativeHostError::Protocol(
                        "inventory unknown action id is not a string".to_owned(),
                    )
                })?;
                if !is_logical_action_id(action_id) {
                    return Err(NativeHostError::Protocol(
                        "inventory unknown action id is invalid".to_owned(),
                    ));
                }
                Ok(action_id.to_owned())
            })
            .collect::<Result<Vec<_>, NativeHostError>>()?,
        Some(_) => {
            return Err(NativeHostError::Protocol(
                "inventory unknown action ids are not an array".to_owned(),
            ));
        }
    };
    if unknown_action_ids.len() > MAX_NATIVE_UNKNOWN_ACTIONS {
        return Err(NativeHostError::MessageTooLarge);
    }
    let unknown_actions_overflow = match payload.get("unknown_actions_overflow") {
        None => false,
        Some(value) => value.as_bool().ok_or_else(|| {
            NativeHostError::Protocol("inventory unknown action overflow is invalid".to_owned())
        })?,
    };

    for page in pages {
        let Some(object) = page.as_object() else {
            return Err(NativeHostError::Protocol(
                "inventory page is not an object".to_owned(),
            ));
        };
        if let Some(page_profile) = object.get("profile_instance_id").and_then(Value::as_str)
            && page_profile != profile_instance_id
        {
            return Err(NativeHostError::Protocol(
                "inventory page profile binding does not match the hello".to_owned(),
            ));
        }
        if let Some(page_epoch) = object.get("browser_session_epoch").and_then(Value::as_u64)
            && page_epoch != browser_session_epoch
        {
            return Err(NativeHostError::Protocol(
                "inventory page browser session epoch is stale".to_owned(),
            ));
        }
    }
    let valid_snapshot = sanitize_observation_snapshot(ObservationSnapshot {
        pages: pages.to_vec(),
        groups: groups.to_vec(),
        safety,
        recovery_observed,
    })
    .map_err(|error| NativeHostError::Protocol(format!("inventory is invalid: {error}")))?;

    let mut inventory = shared
        .inventory
        .lock()
        .map_err(|_| NativeHostError::Unavailable("inventory state is poisoned".to_owned()))?;
    inventory.pages.clear();
    inventory.unmanaged_pages.clear();
    inventory.groups = valid_snapshot.groups;
    inventory.safety = valid_snapshot.safety;
    inventory.recovery_observed = valid_snapshot.recovery_observed;
    inventory.unknown_action_ids.clear();
    inventory.unknown_actions_overflow = unknown_actions_overflow;
    for action_id in unknown_action_ids {
        inventory.unknown_action_ids.insert(action_id);
    }
    for page in valid_snapshot.pages {
        let (Some(space_id), Some(page_id)) = (
            page.get("space_id").and_then(Value::as_str),
            page.get("page_id").and_then(Value::as_str),
        ) else {
            inventory.unmanaged_pages.push(page);
            continue;
        };
        inventory
            .pages
            .insert((space_id.to_owned(), page_id.to_owned()), page);
    }
    Ok(())
}

fn response_result(value: Value) -> Result<Value, CoreError> {
    let object = value.as_object().ok_or_else(|| {
        CoreError::new(
            ErrorCode::InvalidJson,
            "extension response is not an object",
        )
    })?;
    if object.get("ok").and_then(Value::as_bool) == Some(true) {
        return Ok(object.get("result").cloned().unwrap_or_else(|| json!({})));
    }
    let error = object.get("error").and_then(Value::as_object);
    let code = error
        .and_then(|error| error.get("code"))
        .and_then(Value::as_str)
        .map(parse_error_code)
        .unwrap_or(ErrorCode::ExtensionNotConnected);
    let message = error
        .and_then(|error| error.get("message"))
        .and_then(Value::as_str)
        .unwrap_or("extension request failed");
    Err(CoreError::new(code, message.to_owned()))
}

fn parse_error_code(value: &str) -> ErrorCode {
    match value {
        "permission_denied" => ErrorCode::PermissionDenied,
        "user_control_required" => ErrorCode::UserControlRequired,
        "stale_lease" => ErrorCode::StaleLease,
        "page_not_found" => ErrorCode::PageNotFound,
        "capability_unavailable" => ErrorCode::CapabilityUnavailable,
        "native_host_unavailable" => ErrorCode::NativeHostUnavailable,
        "message_too_large" => ErrorCode::MessageTooLarge,
        "timeout" => ErrorCode::Timeout,
        "unknown_outcome" => ErrorCode::UnknownOutcome,
        "stale_generation" => ErrorCode::TargetReplaced,
        "protocol_mismatch" => ErrorCode::ProtocolMismatch,
        _ => ErrorCode::InvalidArgument,
    }
}

fn action_wire(operation: agentyc_core::ActionOperation) -> (String, Map<String, Value>) {
    let mut params = Map::new();
    let method = match operation {
        agentyc_core::ActionOperation::Navigate => {
            params.insert("method".to_owned(), json!("Page.navigate"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Click => {
            params.insert("method".to_owned(), json!("Input.dispatchMouseEvent"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Input => {
            params.insert("method".to_owned(), json!("Input.insertText"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Evaluate => {
            params.insert("method".to_owned(), json!("Runtime.evaluate"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Scroll => {
            params.insert("method".to_owned(), json!("Input.dispatchMouseEvent"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Screenshot => {
            params.insert("method".to_owned(), json!("Page.captureScreenshot"));
            "debugger.command"
        }
        agentyc_core::ActionOperation::Close => return ("page.close".to_owned(), params),
        agentyc_core::ActionOperation::Wait => return ("event.wait".to_owned(), params),
        agentyc_core::ActionOperation::StorageWrite => {
            return ("storage.write".to_owned(), params);
        }
        agentyc_core::ActionOperation::CookieWrite => {
            return ("cookies.write".to_owned(), params);
        }
        agentyc_core::ActionOperation::Upload => return ("page.upload".to_owned(), params),
    };
    (method.to_owned(), params)
}

fn write_envelope(shared: &NativeShared, value: &Value) -> Result<(), NativeHostError> {
    let _outbound = shared
        .outbound
        .lock()
        .map_err(|_| NativeHostError::Unavailable("outbound state is poisoned".to_owned()))?;
    write_envelope_unlocked(shared, value)
}

fn write_envelope_unlocked(shared: &NativeShared, value: &Value) -> Result<(), NativeHostError> {
    let payload = serde_json::to_vec(value).map_err(|_| {
        NativeHostError::Protocol("Native Messaging envelope is not JSON".to_owned())
    })?;
    if payload.len() > MAX_NATIVE_CONTROL_BYTES {
        return Err(NativeHostError::MessageTooLarge);
    }
    let length = u32::try_from(payload.len()).map_err(|_| NativeHostError::MessageTooLarge)?;
    let mut frame = Vec::with_capacity(4 + payload.len());
    // Chrome specifies native byte order for this prefix. The supported
    // Chrome targets use the host's native little-endian representation.
    frame.extend_from_slice(&length.to_ne_bytes());
    frame.extend_from_slice(&payload);
    let mut writer = shared.writer.lock().map_err(|_| {
        NativeHostError::Unavailable("Native Messaging writer is poisoned".to_owned())
    })?;
    writer
        .write_all(&frame)
        .and_then(|_| writer.flush())
        .map_err(|_| NativeHostError::Unavailable("Native Messaging write failed".to_owned()))
}

fn read_frame(reader: &mut dyn Read) -> Result<Option<Vec<u8>>, NativeHostError> {
    let mut prefix = [0_u8; 4];
    let mut read = 0;
    while read < prefix.len() {
        match reader.read(&mut prefix[read..]) {
            Ok(0) if read == 0 => return Ok(None),
            Ok(0) => {
                return Err(NativeHostError::Protocol(
                    "truncated Native Messaging length".to_owned(),
                ));
            }
            Ok(count) => read += count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => {
                return Err(NativeHostError::Unavailable(
                    "Native Messaging read failed".to_owned(),
                ));
            }
        }
    }
    let length = u32::from_ne_bytes(prefix) as usize;
    if length > MAX_NATIVE_CONTROL_BYTES {
        return Err(NativeHostError::MessageTooLarge);
    }
    let mut payload = vec![0_u8; length];
    let mut offset = 0;
    while offset < length {
        let end = (offset + MAX_NATIVE_READ_CHUNK_BYTES).min(length);
        match reader.read(&mut payload[offset..end]) {
            Ok(0) => {
                return Err(NativeHostError::Protocol(
                    "truncated Native Messaging payload".to_owned(),
                ));
            }
            Ok(count) => offset += count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => {
                return Err(NativeHostError::Unavailable(
                    "Native Messaging read failed".to_owned(),
                ));
            }
        }
    }
    Ok(Some(payload))
}

fn parse_bounded_json(payload: &[u8]) -> Result<Value, NativeHostError> {
    if payload.len() > MAX_NATIVE_CONTROL_BYTES {
        return Err(NativeHostError::MessageTooLarge);
    }
    let value: Value = serde_json::from_slice(payload).map_err(|_| {
        NativeHostError::Protocol("Native Messaging payload is invalid JSON".to_owned())
    })?;
    validate_json_budget(&value, 0)?;
    Ok(value)
}

fn validate_json_budget(value: &Value, depth: usize) -> Result<(), NativeHostError> {
    if depth > MAX_NATIVE_DEPTH {
        return Err(NativeHostError::MessageTooLarge);
    }
    match value {
        Value::String(value) if value.len() > MAX_NATIVE_STRING_BYTES => {
            Err(NativeHostError::MessageTooLarge)
        }
        Value::Array(values) => {
            if values.len() > MAX_NATIVE_COLLECTION_ITEMS {
                return Err(NativeHostError::MessageTooLarge);
            }
            for value in values {
                validate_json_budget(value, depth + 1)?;
            }
            Ok(())
        }
        Value::Object(values) => {
            if values.len() > MAX_NATIVE_COLLECTION_ITEMS {
                return Err(NativeHostError::MessageTooLarge);
            }
            for (key, value) in values {
                if matches!(key.as_str(), "__proto__" | "constructor" | "prototype") {
                    return Err(NativeHostError::Protocol("unsafe JSON key".to_owned()));
                }
                validate_json_budget(value, depth + 1)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn assert_no_raw_browser_identifiers(value: &Value, parent: &str) -> Result<(), NativeHostError> {
    let Some(object) = value.as_object() else {
        if let Some(values) = value.as_array() {
            for value in values {
                assert_no_raw_browser_identifiers(value, parent)?;
            }
        }
        return Ok(());
    };
    for (key, child) in object {
        let normalized = key.to_ascii_lowercase().replace('-', "_");
        let raw = matches!(
            normalized.as_str(),
            "tabid"
                | "tab_id"
                | "targetid"
                | "target_id"
                | "sessionid"
                | "session_id"
                | "groupid"
                | "group_id"
                | "windowid"
                | "window_id"
                | "frameid"
                | "frame_id"
                | "backendnodeid"
                | "backend_node_id"
                | "executioncontextid"
                | "execution_context_id"
                | "loaderid"
                | "loader_id"
                | "rawtabid"
                | "raw_tab_id"
                | "rawtargetid"
                | "raw_target_id"
                | "rawsessionid"
                | "raw_session_id"
                | "rawgroupid"
                | "raw_group_id"
                | "rawwindowid"
                | "raw_window_id"
                | "rawframeid"
                | "raw_frame_id"
                | "path"
                | "file_path"
                | "filepath"
                | "profile_path"
                | "user_data_dir"
                | "user_data_directory"
                | "executable_path"
                | "browser_path"
                | "chrome_path"
                | "debugger_url"
                | "websocket_url"
                | "objectid"
                | "object_id"
                | "scriptid"
                | "script_id"
                | "debuggerid"
                | "debugger_id"
                | "nodeid"
                | "node_id"
        ) || (normalized == "id"
            && (parent.starts_with("tab")
                || parent.starts_with("target")
                || parent.starts_with("window")
                || parent.starts_with("group")
                || parent.starts_with("browser")
                || parent.starts_with("session")
                || parent.starts_with("frame")
                || parent.starts_with("execution_context")
                || parent.starts_with("loader")
                || parent.starts_with("backend_node")));
        if raw {
            return Err(NativeHostError::Protocol(
                "raw browser identifiers are extension-internal".to_owned(),
            ));
        }
        assert_no_raw_browser_identifiers(child, &normalized)?;
    }
    Ok(())
}

fn required_string(object: &Map<String, Value>, key: &str) -> Result<String, NativeHostError> {
    let value = object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} is missing or invalid")))?;
    Ok(value.to_owned())
}

fn is_logical_action_id(value: &str) -> bool {
    (8..=128).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
}

fn required_u64(object: &Map<String, Value>, key: &str) -> Result<u64, NativeHostError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} is missing or invalid")))
}

fn required_positive_u64(object: &Map<String, Value>, key: &str) -> Result<u64, NativeHostError> {
    let value = required_u64(object, key)?;
    if value == 0 {
        return Err(NativeHostError::Protocol(format!("{key} must be positive")));
    }
    Ok(value)
}

fn map_capabilities(values: &[String]) -> Vec<Capability> {
    let mut capabilities = Vec::new();
    for value in values {
        let capability = match value.as_str() {
            "logical_tabs" | "debugger_allowlist" => Some(Capability::Action),
            "frame_events" => Some(Capability::Wait),
            "snapshot" => Some(Capability::Snapshot),
            "evaluate" => Some(Capability::Evaluate),
            "reconcile" => Some(Capability::Reconcile),
            // The extension does not advertise the artifact wire protocol yet.
            _ => None,
        };
        if let Some(capability) = capability
            && !capabilities.contains(&capability)
        {
            capabilities.push(capability);
        }
    }
    capabilities
}

fn mark_closed(shared: &NativeShared, error: NativeHostError) {
    let mut closed = shared
        .closed
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if closed.is_none() {
        *closed = Some(error.clone());
        shared.closed_cv.notify_all();
    }
    drop(closed);
    let mut pending = shared
        .pending
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let pending = std::mem::take(&mut *pending);
    for (_, sender) in pending {
        let _ = sender.send(Err(error.clone()));
    }
}

/// Normalize the only representation Chrome uses for an extension origin.
///
/// Chrome supplies a trailing slash in the Native Messaging argv value. The
/// registered allowlist stores the origin without that transport spelling.
pub fn normalize_extension_origin(value: &str) -> Result<String, NativeHostError> {
    let normalized = value.strip_suffix('/').unwrap_or(value);
    if !is_valid_extension_origin(normalized) {
        return Err(NativeHostError::OriginInvalid);
    }
    Ok(normalized.to_owned())
}

fn is_valid_extension_origin(value: &str) -> bool {
    let Some(id) = value.strip_prefix("chrome-extension://") else {
        return false;
    };
    id.len() == 32 && id.bytes().all(|byte| (b'a'..=b'p').contains(&byte))
}

fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| {
            duration.as_millis().min(u128::from(u64::MAX)) as u64
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentyc_core::{ActionId, ActionOperation, ContentHash, IdempotencyKey, RequestId};
    use std::{sync::mpsc::Receiver, time::Instant};

    #[test]
    fn chrome_origin_requires_exact_extension_id_and_normalizes_only_trailing_slash() {
        let origin = format!("chrome-extension://{}/", "a".repeat(32));
        assert_eq!(
            normalize_extension_origin(&origin).expect("origin"),
            origin.trim_end_matches('/')
        );
        assert!(normalize_extension_origin("chrome-extension://*").is_err());
        assert!(
            normalize_extension_origin("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaz")
                .is_err()
        );
    }

    #[test]
    fn native_frame_uses_native_endian_and_rejects_truncation() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&(3_u32).to_ne_bytes());
        bytes.extend_from_slice(b"abc");
        let mut reader = &bytes[..];
        assert_eq!(
            read_frame(&mut reader).expect("frame"),
            Some(b"abc".to_vec())
        );
        let mut truncated = &[3_u8, 0, 0, 0, b'a'][..];
        assert!(read_frame(&mut truncated).is_err());
    }

    #[test]
    fn request_id_is_logical_but_raw_browser_identifiers_are_rejected() {
        let logical = json!({"request_id": "req_native_1", "action_id": "action_1"});
        assert_no_raw_browser_identifiers(&logical, "").expect("logical identifiers");
        assert!(assert_no_raw_browser_identifiers(&json!({"target_id": "raw"}), "").is_err());
        assert!(assert_no_raw_browser_identifiers(&json!({"tab": {"id": 7}}), "").is_err());
        assert!(
            assert_no_raw_browser_identifiers(&json!({"path": "/private/profile"}), "").is_err()
        );
    }

    #[test]
    fn hello_conversion_keeps_profile_binding_and_nonce() {
        let hello = NativeHello {
            protocol: PROTOCOL_VERSION,
            nonce: "nonce_test".to_owned(),
            sequence: 1,
            worker_instance_epoch: 2,
            browser_session_epoch: 3,
            profile_instance_id: "profile_test".to_owned(),
            extension_version: "0.1.0".to_owned(),
            capabilities: vec!["logical_tabs".to_owned()],
        };
        let core = hello
            .to_core_hello(PrincipalId::from_suffix("extension").expect("principal"))
            .expect("core hello");
        assert_eq!(core.protocol, PROTOCOL_VERSION);
        assert_eq!(
            core.client_metadata
                .as_ref()
                .and_then(|metadata| metadata.profile_binding_id.as_ref())
                .map(ProfileBindingId::as_str),
            Some("profile_test")
        );
    }

    #[test]
    fn action_wire_keeps_close_logical_and_maps_navigation_to_allowlisted_debugger_command() {
        let (method, params) = action_wire(ActionOperation::Navigate);
        assert_eq!(method, "debugger.command");
        assert_eq!(params.get("method"), Some(&json!("Page.navigate")));
        let (method, _) = action_wire(ActionOperation::Close);
        assert_eq!(method, "page.close");
    }

    #[test]
    fn bounded_json_rejects_deep_or_oversized_values() {
        let mut value = json!({});
        for _ in 0..(MAX_NATIVE_DEPTH + 2) {
            value = json!([value]);
        }
        assert!(validate_json_budget(&value, 0).is_err());
        assert!(parse_bounded_json(&vec![b'x'; MAX_NATIVE_CONTROL_BYTES + 1]).is_err());
    }

    struct ChannelReader {
        receiver: Receiver<Vec<u8>>,
        buffer: VecDeque<u8>,
    }

    impl Read for ChannelReader {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            if self.buffer.is_empty() {
                match self.receiver.recv() {
                    Ok(bytes) => self.buffer.extend(bytes),
                    Err(_) => return Ok(0),
                }
            }
            let count = output.len().min(self.buffer.len());
            for slot in output.iter_mut().take(count) {
                *slot = self.buffer.pop_front().expect("buffered byte");
            }
            Ok(count)
        }
    }

    #[derive(Clone, Default)]
    struct CaptureWriter(Arc<Mutex<Vec<u8>>>);

    impl Write for CaptureWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0
                .lock()
                .map_err(|_| io::Error::other("capture writer poisoned"))?
                .extend_from_slice(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn frame_json(value: Value) -> Vec<u8> {
        let payload = serde_json::to_vec(&value).expect("json");
        let mut frame = Vec::with_capacity(payload.len() + 4);
        frame.extend_from_slice(&(payload.len() as u32).to_ne_bytes());
        frame.extend_from_slice(&payload);
        frame
    }

    fn captured_frames(capture: &Arc<Mutex<Vec<u8>>>) -> Vec<Value> {
        let bytes = capture.lock().expect("capture").clone();
        let mut offset = 0;
        let mut frames = Vec::new();
        while bytes.len().saturating_sub(offset) >= 4 {
            let length =
                u32::from_ne_bytes(bytes[offset..offset + 4].try_into().expect("length prefix"))
                    as usize;
            if bytes.len().saturating_sub(offset + 4) < length {
                break;
            }
            let payload = &bytes[offset + 4..offset + 4 + length];
            frames.push(serde_json::from_slice(payload).expect("frame json"));
            offset += 4 + length;
        }
        frames
    }

    fn extension_message(hello: &NativeHello, sequence: u64, kind: &str, fields: Value) -> Value {
        let mut object = fields.as_object().cloned().expect("message object");
        object.insert("protocol".to_owned(), json!(PROTOCOL_VERSION));
        object.insert("kind".to_owned(), json!(kind));
        object.insert("nonce".to_owned(), json!(hello.nonce));
        object.insert("sequence".to_owned(), json!(sequence));
        object.insert("broker_epoch".to_owned(), json!(1));
        object.insert("connection_epoch".to_owned(), json!(1));
        object.insert(
            "worker_instance_epoch".to_owned(),
            json!(hello.worker_instance_epoch),
        );
        object.insert(
            "browser_session_epoch".to_owned(),
            json!(hello.browser_session_epoch),
        );
        Value::Object(object)
    }

    #[test]
    fn inbound_requests_are_drained_and_responses_keep_correlation() {
        let (to_host, from_extension) = mpsc::sync_channel(8);
        let capture = Arc::new(Mutex::new(Vec::new()));
        let hello = NativeHello {
            protocol: PROTOCOL_VERSION,
            nonce: "nonce_request_test".to_owned(),
            sequence: 1,
            worker_instance_epoch: 2,
            browser_session_epoch: 3,
            profile_instance_id: "profile_request_test".to_owned(),
            extension_version: "0.1.0".to_owned(),
            capabilities: vec!["logical_tabs".to_owned()],
        };
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": hello.nonce,
                "sequence": 1,
                "worker_instance_epoch": hello.worker_instance_epoch,
                "browser_session_epoch": hello.browser_session_epoch,
                "profile_instance_id": hello.profile_instance_id,
                "extension_version": hello.extension_version,
                "capabilities": hello.capabilities,
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let writer = CaptureWriter(Arc::clone(&capture));
        let config =
            NativeMessagingConfig::new("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1));
        let (accepted, bridge) =
            NativeMessagingBridge::accept(reader, writer, config).expect("accept");
        assert_eq!(accepted, hello);
        assert!(bridge.closed().is_none());
        bridge
            .complete_handshake(
                &hello,
                BrokerEpoch::new(1),
                agentyc_core::ConnectionEpoch::new(1),
                &[Capability::Action],
            )
            .expect("hello_ok");

        to_host
            .send(frame_json(extension_message(
                &hello,
                2,
                "request",
                json!({
                    "request_id": "req_side_panel_1",
                    "action_id": "action_side_panel_1",
                    "method": "space.create",
                    "params": {"label": "panel"}
                }),
            )))
            .expect("request input");
        let deadline = Instant::now() + Duration::from_secs(1);
        let request = loop {
            let mut requests = bridge.drain_requests();
            if let Some(request) = requests.pop() {
                break request;
            }
            assert!(!bridge.is_closed(), "request reader closed unexpectedly");
            assert!(Instant::now() < deadline, "request was not queued");
            thread::sleep(Duration::from_millis(2));
        };
        assert_eq!(request.request_id, "req_side_panel_1");
        assert_eq!(request.action_id.as_deref(), Some("action_side_panel_1"));
        assert_eq!(request.method, "space.create");
        assert_eq!(request.params, json!({"label": "panel"}));
        assert!(bridge.drain_requests().is_empty());

        bridge
            .respond(
                &request,
                Ok(json!({
                    "space_id": "space_panel",
                    "lifecycle": "created"
                })),
            )
            .expect("response");
        let deadline = Instant::now() + Duration::from_secs(1);
        let response = loop {
            if let Some(response) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "response")
            {
                break response;
            }
            assert!(Instant::now() < deadline, "response was not written");
            thread::sleep(Duration::from_millis(2));
        };
        assert_eq!(response["request_id"], json!("req_side_panel_1"));
        assert_eq!(response["action_id"], json!("action_side_panel_1"));
        assert_eq!(response["ok"], json!(true));
        assert_eq!(response["result"]["space_id"], json!("space_panel"));

        drop(to_host);
        assert!(matches!(
            bridge.wait_closed(),
            NativeHostError::Unavailable(_)
        ));
    }

    #[test]
    fn live_bridge_handshakes_correlates_responses_and_acknowledges_fences() {
        let (to_host, from_extension) = mpsc::sync_channel(8);
        let capture = Arc::new(Mutex::new(Vec::new()));
        let hello = NativeHello {
            protocol: PROTOCOL_VERSION,
            nonce: "nonce_test".to_owned(),
            sequence: 1,
            worker_instance_epoch: 2,
            browser_session_epoch: 3,
            profile_instance_id: "profile_test".to_owned(),
            extension_version: "0.1.0".to_owned(),
            capabilities: vec!["logical_tabs".to_owned(), "frame_events".to_owned()],
        };
        let hello_wire = json!({
            "protocol": PROTOCOL_VERSION,
            "kind": "hello",
            "nonce": hello.nonce,
            "sequence": 1,
            "worker_instance_epoch": hello.worker_instance_epoch,
            "browser_session_epoch": hello.browser_session_epoch,
            "profile_instance_id": hello.profile_instance_id,
            "extension_version": hello.extension_version,
            "capabilities": hello.capabilities,
        });
        to_host.send(frame_json(hello_wire)).expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let writer = CaptureWriter(Arc::clone(&capture));
        let config =
            NativeMessagingConfig::new("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1))
                .with_request_timeout(Duration::from_secs(1));
        let (accepted, bridge) =
            NativeMessagingBridge::accept(reader, writer, config).expect("accept");
        assert_eq!(accepted, hello);
        assert_eq!(
            bridge
                .bridge_status()
                .expect("bridge status")
                .profile_instance_id
                .as_deref(),
            Some("profile_test")
        );
        bridge
            .complete_handshake(
                &hello,
                BrokerEpoch::new(1),
                agentyc_core::ConnectionEpoch::new(1),
                &[Capability::Action],
            )
            .expect("hello_ok");
        assert_eq!(captured_frames(&capture)[0]["kind"], "hello_ok");

        let request_bridge = bridge.clone();
        let request_thread = thread::spawn(move || request_bridge.observe());
        let request_id = loop {
            if let Some(request) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "request")
            {
                break request["request_id"]
                    .as_str()
                    .expect("request id")
                    .to_owned();
            }
            thread::sleep(Duration::from_millis(2));
        };
        to_host
            .send(frame_json(extension_message(
                &hello,
                2,
                "response",
                json!({
                    "request_id": request_id,
                    "ok": true,
                    "result": {
                        "pages": [{
                            "ownership": "unmanaged",
                            "lifecycle": "unmanaged",
                            "binding_state": "unbound",
                            "active": true,
                            "tab_hint": "user_hint"
                        }],
                        "groups": [{
                            "space_id": "space_one",
                            "hint": "group_hint",
                            "present": true,
                            "drift": false,
                            "member_count": 1
                        }]
                    }
                }),
            )))
            .expect("response input");
        let observed = request_thread
            .join()
            .expect("request thread")
            .expect("result");
        assert_eq!(observed.pages.len(), 1);
        assert_eq!(observed.pages[0]["ownership"], json!("unmanaged"));
        assert_eq!(observed.groups.len(), 1);
        assert_eq!(observed.groups[0]["space_id"], json!("space_one"));

        let fence_bridge = bridge.clone();
        let fence_thread = thread::spawn(move || {
            fence_bridge.fence_request(
                &SpaceId::from_suffix("one").expect("space"),
                Some(LeaseEpoch::new(1)),
                LeaseEpoch::new(2),
                BrokerEpoch::new(1),
            )
        });
        let fence_request_id = loop {
            if let Some(request) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "fence")
            {
                break request["request_id"]
                    .as_str()
                    .expect("fence request id")
                    .to_owned();
            }
            thread::sleep(Duration::from_millis(2));
        };
        to_host
            .send(frame_json(extension_message(
                &hello,
                3,
                "fence_ack",
                json!({
                    "request_id": fence_request_id,
                    "ok": true,
                    "result": {"space_id": "space_one", "fence_epoch": 2}
                }),
            )))
            .expect("fence input");
        assert!(
            fence_thread
                .join()
                .expect("fence thread")
                .expect("fence result")
                .acknowledged
        );

        to_host
            .send(frame_json(extension_message(
                &hello,
                4,
                "inventory",
                json!({
                    "payload": {
                        "profile_instance_id": "profile_test",
                        "browser_session_epoch": 3,
                        "pages": [{
                            "space_id": "space_one",
                            "page_id": "page_one",
                            "ownership": "agent",
                            "browser_session_epoch": 3,
                            "target_generation": 1,
                            "tab_hint": "hint_test"
                        }],
                        "groups": [],
                        "unknown_action_ids": ["action_unknown"],
                        "unknown_actions_overflow": false
                    }
                }),
            )))
            .expect("inventory input");
        thread::sleep(Duration::from_millis(2));
        assert_eq!(bridge.inventory().len(), 1);
        assert_eq!(
            bridge.inventory_unknown_actions(),
            (vec!["action_unknown".to_owned()], false)
        );

        let close_bridge = bridge.clone();
        let close_thread = thread::spawn(move || {
            let space_id = SpaceId::from_suffix("one").expect("space");
            let page_id = PageId::from_suffix("one").expect("page");
            close_bridge.close_page(&space_id, &page_id, LeaseEpoch::new(1))
        });
        let live_inventory_request_id = loop {
            let requests: Vec<_> = captured_frames(&capture)
                .into_iter()
                .filter(|value| value["kind"] == "request" && value["method"] == "tab.inventory")
                .collect();
            if requests.len() >= 2 {
                break requests
                    .last()
                    .and_then(|request| request["request_id"].as_str())
                    .expect("live inventory request id")
                    .to_owned();
            }
            thread::sleep(Duration::from_millis(2));
        };
        to_host
            .send(frame_json(extension_message(
                &hello,
                5,
                "response",
                json!({
                    "request_id": live_inventory_request_id,
                    "ok": true,
                    "result": {"pages": [], "groups": []}
                }),
            )))
            .expect("live inventory response input");
        let close_error = close_thread
            .join()
            .expect("close thread")
            .expect_err("stale cache must not prove cleanup");
        assert_eq!(close_error.code, ErrorCode::PageNotFound);
        assert!(
            !captured_frames(&capture)
                .iter()
                .any(|value| { value["kind"] == "request" && value["method"] == "page.close" })
        );

        let snapshot_bridge = bridge.clone();
        let snapshot_thread = thread::spawn(move || {
            let space_id = SpaceId::from_suffix("one").expect("space");
            let page_id = PageId::from_suffix("one").expect("page");
            snapshot_bridge.snapshot(&space_id, &page_id, LeaseEpoch::new(7))
        });
        let snapshot_request_id = loop {
            if let Some(request) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "request" && value["method"] == "snapshot.read")
            {
                assert_eq!(request["params"]["lease_epoch"], json!(7));
                break request["request_id"]
                    .as_str()
                    .expect("snapshot request id")
                    .to_owned();
            }
            thread::sleep(Duration::from_millis(2));
        };
        let snapshot = crate::snapshots::empty_snapshot(
            SpaceId::from_suffix("one").expect("space"),
            PageId::from_suffix("one").expect("page"),
        );
        to_host
            .send(frame_json(extension_message(
                &hello,
                6,
                "response",
                json!({
                    "request_id": snapshot_request_id,
                    "ok": true,
                    "result": serde_json::to_value(snapshot).expect("snapshot json")
                }),
            )))
            .expect("snapshot response input");
        snapshot_thread
            .join()
            .expect("snapshot thread")
            .expect("snapshot result");

        drop(to_host);
        assert!(matches!(
            bridge.wait_closed(),
            NativeHostError::Unavailable(_)
        ));
    }

    #[allow(dead_code)]
    fn _keep_types_used(
        _sender: SyncSender<Result<Value, NativeHostError>>,
        _request: ActionRequest<BTreeMap<String, String>>,
        _id: ActionId,
        _request_id: RequestId,
        _hash: ContentHash,
        _key: IdempotencyKey,
    ) {
    }
}
