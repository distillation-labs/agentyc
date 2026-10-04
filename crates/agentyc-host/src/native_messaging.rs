//! Chrome Native Messaging transport and the production extension bridge.
//!
//! This module is intentionally separate from [`crate::protocol`]. Chrome's
//! Native Messaging transport uses a four-byte little-endian length prefix,
//! while the agent/local protocol uses the core codec's four-byte big-endian
//! prefix. Mixing the two transports would make framing and failure handling
//! ambiguous.

#[cfg(unix)]
use std::os::unix::fs::FileTypeExt;

use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    thread,
    time::{Duration, Instant},
};

use agentyc_core::{
    ActionId, ActionReceipt, ActionRequest, ArtifactBeginEnvelope, ArtifactChunkEnvelope,
    ArtifactEndEnvelope, ArtifactKind, ArtifactTransferBudget, BrokerEpoch, Capability, ClientId,
    ClientMetadata, ConnectionEpoch, ConnectionNonce, ContentHash, CoreError, ErrorCode,
    HelloEnvelope, LeaseEpoch, MAX_ARTIFACT_BYTES, MAX_ARTIFACT_CHUNK_BYTES, MAX_ARTIFACT_CHUNKS,
    MAX_CUMULATIVE_ARTIFACT_BYTES, MAX_IN_FLIGHT_ARTIFACT_BYTES, PROTOCOL_VERSION, PageId,
    PrincipalId, ProfileBindingId, ProfileBindingState, ReconcileToken, ResumeResult,
    ResumeWatermark, SnapshotEnvelope, SpaceId, UnknownReason,
};
use serde_json::{Map, Value, json};
use thiserror::Error;

use crate::actions::ArtifactHandle;
use crate::bridge::{
    Bridge, BridgeDispatchResult, BridgeReconcileResult, BridgeStatus, ExtensionEpochs,
    FenceResult, ObservationSnapshot, sanitize_observation_snapshot,
};
use crate::host::{native_forward_socket_path, read_endpoint_metadata};

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
const MAX_NATIVE_ARTIFACT_TRANSFERS: usize = 16;
const MAX_NATIVE_CAPABILITIES: usize = 32;
const MAX_NATIVE_ID_BYTES: usize = 256;
const MAX_NATIVE_DEADLINE_MS: u64 = 24 * 60 * 60 * 1000;
const MAX_NATIVE_WARNING_BYTES: usize = 4 * 1024;
const MAX_NATIVE_WARNINGS: usize = 64;
/// Native Messaging cursors are bounded to the exact integer range JavaScript
/// can round-trip without losing broker position.
pub const MAX_NATIVE_RESUME_CURSOR_VALUE: u64 = 9_007_199_254_740_991;

// Derived from the pinned public key in extension/manifest.json. Do not derive
// the trusted origin from Native Messaging argv: argv is not attestable here.
const ALLOWED_EXTENSION_ORIGINS: &[&str] = &["chrome-extension://jgbllikljnllangilfgkhncepiockppj"];

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

/// One broker-owned Unix endpoint used by duplicate Native Messaging shims.
///
/// Chrome launches a Native Messaging process per connection. The first process
/// owns the ledger/broker; later processes forward their raw Native Messaging
/// stream to this endpoint instead of opening another broker.
#[cfg(unix)]
pub struct NativeForwardServer {
    socket_path: PathBuf,
    stop: Arc<AtomicBool>,
    incoming: Receiver<std::os::unix::net::UnixStream>,
    join: Option<thread::JoinHandle<()>>,
}

#[cfg(unix)]
impl std::fmt::Debug for NativeForwardServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("NativeForwardServer")
            .field("socket_path", &self.socket_path)
            .field("stopping", &self.stop.load(Ordering::Relaxed))
            .finish_non_exhaustive()
    }
}

#[cfg(unix)]
impl NativeForwardServer {
    /// Start the forwarding endpoint owned by the current broker process.
    pub fn start(state_dir: impl AsRef<Path>) -> Result<Self, NativeHostError> {
        use std::os::unix::{fs::PermissionsExt, net::UnixListener};

        let state_dir = state_dir.as_ref();
        let socket_path = forwarding_socket_path(state_dir)?;
        if socket_path.parent().is_none_or(|parent| !parent.is_dir()) {
            return Err(NativeHostError::Unavailable(
                "Native Messaging forwarding directory is unavailable".to_owned(),
            ));
        }
        if fs::symlink_metadata(&socket_path)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            return Err(NativeHostError::Protocol(
                "Native Messaging forwarding socket is a symlink".to_owned(),
            ));
        }
        if let Ok(metadata) = fs::symlink_metadata(&socket_path) {
            if !metadata.file_type().is_socket() {
                return Err(NativeHostError::Protocol(
                    "Native Messaging forwarding path is not a socket".to_owned(),
                ));
            }
            if std::os::unix::net::UnixStream::connect(&socket_path).is_ok() {
                return Err(NativeHostError::Unavailable(
                    "Native Messaging forwarding endpoint is already active".to_owned(),
                ));
            }
            fs::remove_file(&socket_path).map_err(|error| {
                NativeHostError::Unavailable(format!(
                    "stale Native Messaging forwarding socket cannot be removed: {error}"
                ))
            })?;
        }
        let listener = UnixListener::bind(&socket_path).map_err(|error| {
            NativeHostError::Unavailable(format!(
                "Native Messaging forwarding socket bind failed: {error}"
            ))
        })?;
        listener.set_nonblocking(true).map_err(|error| {
            NativeHostError::Unavailable(format!(
                "Native Messaging forwarding socket setup failed: {error}"
            ))
        })?;
        fs::set_permissions(&socket_path, fs::Permissions::from_mode(0o600)).map_err(|error| {
            NativeHostError::Unavailable(format!(
                "Native Messaging forwarding socket permissions failed: {error}"
            ))
        })?;

        let stop = Arc::new(AtomicBool::new(false));
        let stop_for_thread = Arc::clone(&stop);
        let (incoming_tx, incoming_rx) = mpsc::sync_channel(4);
        let join_path = socket_path.clone();
        let join = thread::Builder::new()
            .name("agentyc-native-forward".to_owned())
            .spawn(move || {
                use std::io::ErrorKind;
                while !stop_for_thread.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            if !crate::host::peer_matches_directory_owner(&stream, &join_path) {
                                continue;
                            }
                            if stream.set_nonblocking(false).is_err()
                                || incoming_tx.send(stream).is_err()
                            {
                                break;
                            }
                        }
                        Err(error) if error.kind() == ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(25));
                        }
                        Err(_) => break,
                    }
                }
                drop(listener);
                remove_socket_if_owned(&join_path);
            })
            .map_err(|error| {
                NativeHostError::Unavailable(format!(
                    "Native Messaging forwarding thread failed: {error}"
                ))
            })?;
        Ok(Self {
            socket_path,
            stop,
            incoming: incoming_rx,
            join: Some(join),
        })
    }

    /// Return the endpoint path published to duplicate shims.
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    /// Receive one forwarded Native Messaging connection.
    pub fn accept_forwarded(
        &self,
        timeout: Duration,
    ) -> Result<std::os::unix::net::UnixStream, NativeHostError> {
        self.incoming
            .recv_timeout(timeout)
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => NativeHostError::Timeout,
                mpsc::RecvTimeoutError::Disconnected => NativeHostError::disconnected(),
            })
    }

    /// Stop accepting forwarded shims and remove only this endpoint.
    pub fn stop(mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

#[cfg(unix)]
impl Drop for NativeForwardServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

#[cfg(unix)]
fn remove_socket_if_owned(path: &Path) {
    let removed = fs::symlink_metadata(path)
        .map(|metadata| metadata.file_type().is_socket())
        .unwrap_or(false)
        && std::os::unix::net::UnixStream::connect(path).is_err()
        && fs::remove_file(path).is_ok();
    if removed {
        let Some(parent) = path.parent() else { return };
        let is_short_lived = parent
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with("agentyc-forward-"));
        if is_short_lived {
            let _ = fs::remove_dir(parent);
        }
    }
}

#[cfg(unix)]
fn forwarding_socket_path(state_dir: &Path) -> Result<PathBuf, NativeHostError> {
    let normal = native_forward_socket_path(state_dir);
    if normal.as_os_str().len() <= 80 {
        if normal.parent().is_none_or(|parent| !parent.is_dir()) {
            return Err(NativeHostError::Unavailable(
                "Native Messaging forwarding directory is unavailable".to_owned(),
            ));
        }
        return Ok(normal);
    }
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in state_dir.as_os_str().to_string_lossy().as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    let temp_root = Path::new("/tmp");
    let temp_root = if temp_root.is_dir() {
        temp_root.to_path_buf()
    } else {
        std::env::temp_dir()
    };
    let directory = temp_root.join(format!("agentyc-forward-{hash:016x}"));
    if directory.exists() && !directory.is_dir() {
        return Err(NativeHostError::Unavailable(
            "short Native Messaging forwarding path is not a directory".to_owned(),
        ));
    }
    fs::create_dir_all(&directory).map_err(|error| {
        NativeHostError::Unavailable(format!("short Native Messaging directory failed: {error}"))
    })?;
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).map_err(|error| {
        NativeHostError::Unavailable(format!(
            "short Native Messaging directory permissions failed: {error}"
        ))
    })?;
    Ok(directory.join("native.sock"))
}

#[cfg(unix)]
/// Forward one duplicate Chrome-launched stdio pair to the broker owner.
pub fn forward_stdio_to_owner(
    state_dir: impl AsRef<Path>,
    timeout: Duration,
) -> Result<(), NativeHostError> {
    let deadline = Instant::now() + timeout;
    let endpoint = loop {
        match read_endpoint_metadata(state_dir.as_ref()) {
            Ok(endpoint) => break endpoint,
            Err(error) if Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(25));
                let _ = error;
            }
            Err(error) => return Err(NativeHostError::Unavailable(error.to_string())),
        }
    };
    let path = PathBuf::from(endpoint.native_forward_socket);
    let stream = loop {
        match std::os::unix::net::UnixStream::connect(&path) {
            Ok(stream) => break stream,
            Err(error) if Instant::now() < deadline => {
                if error.kind() != io::ErrorKind::NotFound
                    && error.kind() != io::ErrorKind::ConnectionRefused
                {
                    return Err(NativeHostError::Unavailable(error.to_string()));
                }
                thread::sleep(Duration::from_millis(25));
            }
            Err(error) => return Err(NativeHostError::Unavailable(error.to_string())),
        }
    };
    proxy_stdio(stream)
}

#[cfg(unix)]
fn proxy_stdio(stream: std::os::unix::net::UnixStream) -> Result<(), NativeHostError> {
    let mut to_owner = stream
        .try_clone()
        .map_err(|error| NativeHostError::Unavailable(error.to_string()))?;
    let mut from_owner = stream;
    let writer = thread::Builder::new()
        .name("agentyc-native-forward-stdin".to_owned())
        .spawn(move || io::copy(&mut io::stdin(), &mut to_owner))
        .map_err(|error| NativeHostError::Unavailable(error.to_string()))?;
    let read_result = io::copy(&mut from_owner, &mut io::stdout());
    // Do not join a stdin reader after the owner closes: Chrome may leave the
    // shim's stdin open briefly, and waiting here would turn owner disconnect
    // into an unbounded process hang. Dropping the handle detaches the bounded
    // forwarding worker; process teardown closes its descriptors.
    drop(writer);
    read_result
        .map(|_| ())
        .map_err(|error| NativeHostError::Unavailable(error.to_string()))
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
        let expected_origin = validate_configured_origin(&expected_origin.into())?;
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
    /// Optional broker event cursor retained across reconnects.
    pub resume_from: Option<ResumeWatermark>,
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
            resume_from: self.resume_from,
            client_metadata: Some(ClientMetadata {
                client_id: Some(client_id),
                client_name: Some("agentyc-extension".to_owned()),
                client_version: Some(self.extension_version.clone()),
                connection_nonce: Some(nonce),
                profile_binding_id: Some(profile_binding_id),
            }),
        })
    }

    /// Return the optional broker/source cursor attached to this hello.
    pub fn resume_from(&self) -> Option<ResumeWatermark> {
        self.resume_from
    }

    /// Alias emphasizing that this cursor is not the Native Messaging sequence.
    pub fn resume_cursor(&self) -> Option<ResumeWatermark> {
        self.resume_from()
    }

    /// Attach a bounded broker/source cursor to a hello constructed in code.
    pub fn with_resume_from(
        self,
        cursor: Option<ResumeWatermark>,
    ) -> Result<Self, NativeHostError> {
        if let Some(cursor) = cursor {
            validate_native_resume_cursor(cursor)?;
        }
        Ok(Self {
            resume_from: cursor,
            ..self
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NativeNegotiatedLimits {
    /// Maximum control envelope payload in bytes.
    pub max_control_bytes: usize,
    /// Maximum artifact chunk bytes.
    pub max_artifact_chunk_bytes: usize,
    /// Maximum assembled artifact bytes.
    pub max_artifact_bytes: u64,
    /// Maximum chunks in one artifact.
    pub max_artifact_chunks: u16,
    /// Maximum artifact bytes buffered on one connection.
    pub max_in_flight_artifact_bytes: usize,
    /// Maximum artifact bytes received on one connection.
    pub max_cumulative_artifact_bytes: u64,
}

impl NativeNegotiatedLimits {
    /// Product limits offered by this host.
    pub const fn host_defaults() -> Self {
        Self {
            max_control_bytes: MAX_NATIVE_CONTROL_BYTES,
            max_artifact_chunk_bytes: MAX_ARTIFACT_CHUNK_BYTES,
            max_artifact_bytes: MAX_ARTIFACT_BYTES,
            max_artifact_chunks: MAX_ARTIFACT_CHUNKS,
            max_in_flight_artifact_bytes: MAX_IN_FLIGHT_ARTIFACT_BYTES,
            max_cumulative_artifact_bytes: MAX_CUMULATIVE_ARTIFACT_BYTES,
        }
    }

    fn intersect(self, peer: Self) -> Self {
        Self {
            max_control_bytes: self.max_control_bytes.min(peer.max_control_bytes),
            max_artifact_chunk_bytes: self
                .max_artifact_chunk_bytes
                .min(peer.max_artifact_chunk_bytes),
            max_artifact_bytes: self.max_artifact_bytes.min(peer.max_artifact_bytes),
            max_artifact_chunks: self.max_artifact_chunks.min(peer.max_artifact_chunks),
            max_in_flight_artifact_bytes: self
                .max_in_flight_artifact_bytes
                .min(peer.max_in_flight_artifact_bytes),
            max_cumulative_artifact_bytes: self
                .max_cumulative_artifact_bytes
                .min(peer.max_cumulative_artifact_bytes),
        }
    }

    fn validate(self) -> Result<Self, NativeHostError> {
        if self.max_control_bytes == 0
            || self.max_control_bytes > MAX_NATIVE_CONTROL_BYTES
            || self.max_artifact_chunk_bytes == 0
            || self.max_artifact_chunk_bytes > MAX_ARTIFACT_CHUNK_BYTES
            || self.max_artifact_bytes == 0
            || self.max_artifact_bytes > MAX_ARTIFACT_BYTES
            || self.max_artifact_chunks == 0
            || self.max_artifact_chunks > MAX_ARTIFACT_CHUNKS
            || self.max_in_flight_artifact_bytes == 0
            || self.max_in_flight_artifact_bytes > MAX_IN_FLIGHT_ARTIFACT_BYTES
            || self.max_cumulative_artifact_bytes == 0
            || self.max_cumulative_artifact_bytes > MAX_CUMULATIVE_ARTIFACT_BYTES
        {
            return Err(NativeHostError::Protocol(
                "Native Messaging negotiated limits are outside the product bounds".to_owned(),
            ));
        }
        Ok(self)
    }

    fn to_value(self) -> Value {
        json!({
            "max_control_bytes": self.max_control_bytes,
            "max_artifact_chunk_bytes": self.max_artifact_chunk_bytes,
            "max_artifact_bytes": self.max_artifact_bytes,
            "max_artifact_chunks": self.max_artifact_chunks,
            "max_in_flight_artifact_bytes": self.max_in_flight_artifact_bytes,
            "max_cumulative_artifact_bytes": self.max_cumulative_artifact_bytes,
        })
    }
}

#[derive(Debug)]
struct NativeArtifactTransfer {
    begin: ArtifactBeginEnvelope,
    progress: agentyc_core::ArtifactTransferProgress,
    bytes: Vec<u8>,
}

/// One fully validated inbound artifact retained until its owner consumes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NativeArtifact {
    /// Validated transfer declaration.
    pub begin: ArtifactBeginEnvelope,
    /// Exact assembled bytes after digest and ordering validation.
    pub bytes: Vec<u8>,
}

#[derive(Debug)]
struct SessionMetadata {
    expected_origin: String,
    hello: NativeHello,
    broker_epoch: Option<u64>,
    connection_epoch: Option<u64>,
    resume_from: Option<ResumeWatermark>,
    resume_status: ResumeResult,
    last_accepted_cursor: Option<ResumeWatermark>,
    next_inbound_sequence: u64,
    next_outbound_sequence: u64,
    handshake_complete: bool,
    host_capabilities: Vec<Capability>,
    capabilities: Vec<Capability>,
    negotiated_capability_names: Vec<String>,
    extension_limits: NativeNegotiatedLimits,
    negotiated_limits: NativeNegotiatedLimits,
    profile_state: ProfileBindingState,
    artifact_transfers: BTreeMap<String, NativeArtifactTransfer>,
    completed_artifacts: BTreeMap<String, NativeArtifact>,
    artifacts_by_action: BTreeMap<String, ArtifactHandle>,
    artifact_budget: ArtifactTransferBudget,
    outbound_artifact_transfers: BTreeMap<String, NativeArtifactTransfer>,
    outbound_artifact_budget: ArtifactTransferBudget,
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
    request_fingerprints: Mutex<BTreeMap<String, agentyc_core::ContentHash>>,
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
        // The public fields remain for API compatibility, so revalidate them
        // at the trust boundary before starting a reader thread.
        let expected_origin = validate_configured_origin(&config.expected_origin)?;
        let (hello_tx, hello_rx) = mpsc::sync_channel(1);
        let shared = Arc::new(NativeShared {
            writer: Mutex::new(Box::new(writer)),
            outbound: Mutex::new(()),
            session: Mutex::new(SessionMetadata {
                expected_origin,
                hello: NativeHello {
                    protocol: 0,
                    nonce: String::new(),
                    sequence: 0,
                    worker_instance_epoch: 0,
                    browser_session_epoch: 0,
                    profile_instance_id: String::new(),
                    extension_version: String::new(),
                    capabilities: Vec::new(),
                    resume_from: None,
                },
                broker_epoch: None,
                connection_epoch: None,
                resume_from: None,
                resume_status: ResumeResult::Accepted,
                last_accepted_cursor: None,
                next_inbound_sequence: 1,
                next_outbound_sequence: 1,
                handshake_complete: false,
                host_capabilities: vec![
                    Capability::Snapshot,
                    Capability::Action,
                    Capability::Wait,
                    Capability::Artifact,
                    Capability::Evaluate,
                    Capability::Reconcile,
                ],
                capabilities: Vec::new(),
                negotiated_capability_names: Vec::new(),
                extension_limits: NativeNegotiatedLimits::host_defaults(),
                negotiated_limits: NativeNegotiatedLimits::host_defaults(),
                profile_state: ProfileBindingState::Unbound,
                artifact_transfers: BTreeMap::new(),
                completed_artifacts: BTreeMap::new(),
                artifacts_by_action: BTreeMap::new(),
                artifact_budget: ArtifactTransferBudget::default(),
                outbound_artifact_transfers: BTreeMap::new(),
                outbound_artifact_budget: ArtifactTransferBudget::default(),
            }),
            pending: Mutex::new(BTreeMap::new()),
            timed_out: Mutex::new(BTreeSet::new()),
            events: Mutex::new(VecDeque::new()),
            requests: Mutex::new(VecDeque::new()),
            request_fingerprints: Mutex::new(BTreeMap::new()),
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

    /// Send a host event with a broker-owned cursor kept separate from the
    /// Native Messaging envelope sequence.
    pub fn send_event_with_cursor(
        &self,
        event: &str,
        payload: Value,
        cursor: ResumeWatermark,
    ) -> Result<(), NativeHostError> {
        validate_native_resume_cursor(cursor)?;
        let broker_epoch = self
            .shared
            .session
            .lock()
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?
            .broker_epoch
            .ok_or_else(|| NativeHostError::Protocol("broker epoch is missing".to_owned()))?;
        if cursor.broker_epoch.get() != broker_epoch {
            return Err(NativeHostError::Protocol(
                "event cursor belongs to another broker epoch".to_owned(),
            ));
        }
        let mut fields = Map::new();
        fields.insert("event".to_owned(), Value::String(event.to_owned()));
        fields.insert("payload".to_owned(), payload);
        fields.insert("cursor".to_owned(), native_cursor_value(cursor));
        self.post("event", fields)
    }

    /// Send one explicit artifact transfer declaration to the extension.
    pub fn send_artifact_begin(
        &self,
        begin: &ArtifactBeginEnvelope,
    ) -> Result<(), NativeHostError> {
        begin
            .validate()
            .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
        self.validate_outbound_artifact_limits(
            begin.total_bytes,
            begin.chunk_size,
            begin.chunk_count,
        )?;
        {
            let mut session = self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
            let key = begin.artifact_id.to_string();
            if session.outbound_artifact_transfers.len() >= MAX_NATIVE_ARTIFACT_TRANSFERS
                || session.outbound_artifact_transfers.contains_key(&key)
            {
                return Err(NativeHostError::Protocol(
                    "outbound artifact transfer is duplicated or exceeds the connection bound"
                        .to_owned(),
                ));
            }
            let connection_epoch =
                ConnectionEpoch::new(session.connection_epoch.ok_or_else(|| {
                    NativeHostError::Protocol("artifact connection epoch is missing".to_owned())
                })?);
            let progress = agentyc_core::ArtifactTransferProgress::begin(begin, connection_epoch)
                .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
            session.outbound_artifact_transfers.insert(
                key,
                NativeArtifactTransfer {
                    begin: begin.clone(),
                    progress,
                    bytes: Vec::new(),
                },
            );
        }
        let mut fields = serde_json::to_value(begin)
            .map_err(|_| NativeHostError::Protocol("artifact_begin is not JSON".to_owned()))?
            .as_object()
            .cloned()
            .ok_or_else(|| {
                NativeHostError::Protocol("artifact_begin is not an object".to_owned())
            })?;
        let result = self.post("artifact_begin", std::mem::take(&mut fields));
        if result.is_err()
            && let Ok(mut session) = self.shared.session.lock()
        {
            session
                .outbound_artifact_transfers
                .remove(&begin.artifact_id.to_string());
        }
        result
    }

    /// Send one ordered artifact chunk to the extension.
    pub fn send_artifact_chunk(
        &self,
        chunk: &ArtifactChunkEnvelope,
    ) -> Result<(), NativeHostError> {
        chunk
            .validate()
            .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
        if chunk.bytes.len() > MAX_ARTIFACT_CHUNK_BYTES {
            return Err(NativeHostError::MessageTooLarge);
        }
        {
            let mut session = self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
            if session
                .outbound_artifact_budget
                .in_flight_bytes()
                .saturating_add(chunk.bytes.len())
                > session.negotiated_limits.max_in_flight_artifact_bytes
                || session
                    .outbound_artifact_budget
                    .cumulative_bytes()
                    .saturating_add(chunk.bytes.len() as u64)
                    > session.negotiated_limits.max_cumulative_artifact_bytes
            {
                return Err(NativeHostError::MessageTooLarge);
            }
            session
                .outbound_artifact_budget
                .receive(chunk.bytes.len())
                .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
            let key = chunk.artifact_id.to_string();
            let Some(transfer) = session.outbound_artifact_transfers.get_mut(&key) else {
                let _ = session.outbound_artifact_budget.release(chunk.bytes.len());
                return Err(NativeHostError::Protocol(
                    "outbound artifact chunk has no active begin".to_owned(),
                ));
            };
            if let Err(error) = transfer.progress.accept_chunk(chunk) {
                let _ = session.outbound_artifact_budget.release(chunk.bytes.len());
                return Err(NativeHostError::Protocol(error.to_string()));
            }
            transfer.bytes.extend_from_slice(&chunk.bytes);
        }
        let fields = serde_json::to_value(chunk)
            .map_err(|_| NativeHostError::Protocol("artifact_chunk is not JSON".to_owned()))?
            .as_object()
            .cloned()
            .ok_or_else(|| {
                NativeHostError::Protocol("artifact_chunk is not an object".to_owned())
            })?;
        self.post("artifact_chunk", fields)
    }

    /// Send completion metadata for an explicit artifact transfer.
    pub fn send_artifact_end(&self, end: &ArtifactEndEnvelope) -> Result<(), NativeHostError> {
        end.validate()
            .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
        let key = end.artifact_id.to_string();
        let mut session =
            self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
        let Some(transfer) = session.outbound_artifact_transfers.remove(&key) else {
            return Err(NativeHostError::Protocol(
                "outbound artifact end has no active begin".to_owned(),
            ));
        };
        let byte_count = transfer.bytes.len();
        let result = transfer
            .progress
            .validate_complete()
            .and_then(|_| end.validate_against(&transfer.begin, &transfer.bytes));
        let release = session.outbound_artifact_budget.release(byte_count);
        drop(session);
        result
            .and(release)
            .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
        let fields = serde_json::to_value(end)
            .map_err(|_| NativeHostError::Protocol("artifact_end is not JSON".to_owned()))?
            .as_object()
            .cloned()
            .ok_or_else(|| NativeHostError::Protocol("artifact_end is not an object".to_owned()))?;
        self.post("artifact_end", fields)
    }

    fn validate_outbound_artifact_limits(
        &self,
        total_bytes: u64,
        chunk_size: u32,
        chunk_count: u16,
    ) -> Result<(), NativeHostError> {
        let session =
            self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
        let limits = session.negotiated_limits;
        if total_bytes > limits.max_artifact_bytes
            || u64::from(chunk_size) > limits.max_artifact_chunk_bytes as u64
            || chunk_count > limits.max_artifact_chunks
        {
            return Err(NativeHostError::MessageTooLarge);
        }
        Ok(())
    }

    /// Send the host handshake acknowledgement after the broker admits the peer.
    ///
    /// This compatibility entry point can determine a broker-epoch mismatch,
    /// but the broker's retention decision is available only to the caller that
    /// performed admission. Call [`Self::complete_handshake_with_resume`] when
    /// that decision is available.
    pub fn complete_handshake(
        &self,
        hello: &NativeHello,
        broker_epoch: BrokerEpoch,
        connection_epoch: agentyc_core::ConnectionEpoch,
        capabilities: &[Capability],
    ) -> Result<(), NativeHostError> {
        let resume = match hello.resume_from {
            Some(cursor) if cursor.broker_epoch != broker_epoch => ResumeResult::ResyncRequired,
            _ => ResumeResult::Accepted,
        };
        self.complete_handshake_with_resume(
            hello,
            broker_epoch,
            connection_epoch,
            capabilities,
            resume,
        )
    }

    /// Send the host handshake acknowledgement with the broker's explicit
    /// accepted/resync decision.
    pub fn complete_handshake_with_resume(
        &self,
        hello: &NativeHello,
        broker_epoch: BrokerEpoch,
        connection_epoch: agentyc_core::ConnectionEpoch,
        capabilities: &[Capability],
        resume: ResumeResult,
    ) -> Result<(), NativeHostError> {
        let resume_from = hello.resume_from;
        if let Some(cursor) = resume_from {
            validate_native_resume_cursor(cursor)?;
            if resume == ResumeResult::Accepted && cursor.broker_epoch != broker_epoch {
                return Err(NativeHostError::Protocol(
                    "accepted resume cursor belongs to another broker epoch".to_owned(),
                ));
            }
        } else if resume == ResumeResult::ResyncRequired {
            return Err(NativeHostError::Protocol(
                "resync status requires a resume cursor".to_owned(),
            ));
        }
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
        session.resume_status = resume;
        session.resume_from = resume_from;
        session.last_accepted_cursor = match resume {
            ResumeResult::Accepted => resume_from,
            ResumeResult::ResyncRequired => None,
        };
        session.capabilities = intersect_capabilities(&hello.capabilities, capabilities);
        session.negotiated_capability_names =
            intersect_capability_names(&hello.capabilities, capabilities);
        session.negotiated_limits = NativeNegotiatedLimits::host_defaults()
            .intersect(session.extension_limits)
            .validate()?;
        session.profile_state = ProfileBindingState::Bound;
        let envelope = json!({
            "protocol": PROTOCOL_VERSION,
            "kind": "hello_ok",
            "nonce": hello.nonce,
            "sequence": session.next_outbound_sequence,
            "broker_epoch": broker_epoch.get(),
            "connection_epoch": connection_epoch.get(),
            "worker_instance_epoch": hello.worker_instance_epoch,
            "browser_session_epoch": hello.browser_session_epoch,
            "capabilities": session.negotiated_capability_names,
            "limits": session.negotiated_limits.to_value(),
            "profile_instance_id": hello.profile_instance_id,
            "profile_state": "bound",
            "resume": resume_result_wire(resume),
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

    /// Return the resume decision recorded for this connection.
    pub fn resume_status(&self) -> Result<ResumeResult, NativeHostError> {
        self.shared
            .session
            .lock()
            .map(|session| session.resume_status)
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))
    }

    /// Return the last broker/source cursor accepted on this connection.
    pub fn last_accepted_cursor(&self) -> Result<Option<ResumeWatermark>, NativeHostError> {
        self.shared
            .session
            .lock()
            .map(|session| session.last_accepted_cursor)
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))
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

    /// Take one fully validated inbound artifact by its logical transfer handle.
    pub fn take_completed_artifact(
        &self,
        artifact_id: &str,
    ) -> Result<Option<NativeArtifact>, NativeHostError> {
        let mut session =
            self.shared.session.lock().map_err(|_| {
                NativeHostError::Unavailable("session state is poisoned".to_owned())
            })?;
        let artifact = session.completed_artifacts.remove(artifact_id);
        if artifact.is_some() {
            let action_id = session
                .artifacts_by_action
                .iter()
                .find_map(|(action_id, handle)| {
                    (handle.artifact_id.as_str() == artifact_id).then_some(action_id.clone())
                });
            if let Some(action_id) = action_id {
                session.artifacts_by_action.remove(&action_id);
            }
        }
        Ok(artifact)
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

    /// Acknowledge host processing of extension-reported unknown action IDs.
    pub fn acknowledge_inventory_unknown_actions(&self, action_ids: &[String], overflow: bool) {
        if let Ok(mut inventory) = self.shared.inventory.lock() {
            for action_id in action_ids {
                inventory.unknown_action_ids.remove(action_id);
            }
            if overflow {
                inventory.unknown_actions_overflow = false;
            }
        }
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
    #[allow(clippy::too_many_arguments)]
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
        let deadline_ms = self
            .shared
            .request_timeout
            .as_millis()
            .min(u128::from(MAX_NATIVE_DEADLINE_MS)) as u64;
        fields.insert("deadline_ms".to_owned(), json!(deadline_ms.max(1)));
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
            if timed_out.len() >= MAX_NATIVE_TIMED_OUT_REQUESTS
                && let Some(oldest) = timed_out.iter().next().cloned()
            {
                timed_out.remove(&oldest);
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
        request_token: &ReconcileToken,
    ) -> Result<FenceResult, CoreError> {
        let (session_broker_epoch, connection_epoch) = {
            let session = self.shared.session.lock().map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "session state is poisoned",
                )
            })?;
            (
                session.broker_epoch.ok_or_else(|| {
                    CoreError::new(ErrorCode::NativeHostUnavailable, "broker epoch is missing")
                })?,
                session.connection_epoch.ok_or_else(|| {
                    CoreError::new(
                        ErrorCode::NativeHostUnavailable,
                        "connection epoch is missing",
                    )
                })?,
            )
        };
        if session_broker_epoch != broker_epoch.get() {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                "fence broker epoch does not match the Native Messaging session",
            ));
        }
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
        params.insert("request_token".to_owned(), json!(request_token.as_str()));
        params.insert("space_id".to_owned(), json!(space_id.to_string()));
        params.insert("lease_epoch".to_owned(), json!(new_epoch.get()));
        params.insert("fence_epoch".to_owned(), json!(new_epoch.get()));
        params.insert("broker_epoch".to_owned(), json!(broker_epoch.get()));
        params.insert("connection_epoch".to_owned(), json!(connection_epoch));
        params.insert("durable".to_owned(), json!(true));
        let mut fields = Map::new();
        fields.insert("request_id".to_owned(), json!(request_id.clone()));
        fields.insert("request_token".to_owned(), json!(request_token.as_str()));
        fields.insert("space_id".to_owned(), json!(space_id.to_string()));
        fields.insert("lease_epoch".to_owned(), json!(new_epoch.get()));
        fields.insert("fence_epoch".to_owned(), json!(new_epoch.get()));
        fields.insert("broker_epoch".to_owned(), json!(broker_epoch.get()));
        fields.insert("connection_epoch".to_owned(), json!(connection_epoch));
        fields.insert("durable".to_owned(), json!(true));
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
        let is_fence_ack = response.get("kind").and_then(Value::as_str) == Some("fence_ack");
        let result = response_result(response)?;
        let acknowledged = is_fence_ack
            && result
                .get("request_token")
                .and_then(Value::as_str)
                .is_some_and(|value| value == request_token.as_str())
            && result
                .get("space_id")
                .and_then(Value::as_str)
                .is_some_and(|value| value == space_id.as_str())
            && result
                .get("fence_epoch")
                .and_then(Value::as_u64)
                .is_some_and(|epoch| epoch == new_epoch.get())
            && result
                .get("broker_epoch")
                .and_then(Value::as_u64)
                .is_some_and(|epoch| epoch == broker_epoch.get())
            && result
                .get("connection_epoch")
                .and_then(Value::as_u64)
                .is_some_and(|epoch| epoch == connection_epoch)
            && result.get("durable").and_then(Value::as_bool) == Some(true);
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
            .map(|session| {
                if session.handshake_complete {
                    session.capabilities.clone()
                } else {
                    session.host_capabilities.clone()
                }
            })
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
        if let Some(page_id) = &request.page_id {
            let inventory = self.inventory_snapshot();
            if let Some(page) = inventory.pages.iter().find(|page| {
                page.get("space_id").and_then(Value::as_str) == Some(request.space_id.as_str())
                    && page.get("page_id").and_then(Value::as_str) == Some(page_id.as_str())
            }) {
                for (source, destination) in [
                    ("target_generation", "expected_target_generation"),
                    ("navigation_generation", "expected_navigation_generation"),
                    ("document_generation", "expected_document_generation"),
                ] {
                    if let Some(value) = page.get(source).and_then(Value::as_u64) {
                        params.insert(destination.to_owned(), json!(value));
                    }
                }
            }
        }
        if let Some(postcondition) = &request.postcondition {
            params.insert(
                "postcondition".to_owned(),
                serde_json::to_value(postcondition).map_err(|_| {
                    CoreError::new(ErrorCode::InvalidJson, "action postcondition is not JSON")
                })?,
            );
        }
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
        if request.operation == agentyc_core::ActionOperation::Close {
            let page_id = request.page_id.as_ref().ok_or_else(|| {
                CoreError::new(ErrorCode::PageNotFound, "close action requires a page")
            })?;
            params.insert(
                "cleanup_proof".to_owned(),
                self.cleanup_proof(&request.space_id, page_id, request.lease_epoch)?,
            );
        }
        for (key, value) in &request.payload {
            if !params.contains_key(key) {
                params.insert(key.clone(), Value::String(value.clone()));
            }
        }
        match self.request_value(&method, params, Some(request.action_id.as_str())) {
            Ok(result) => Ok(native_action_result(&self.shared, request, result)),
            Err(error) if error.code == ErrorCode::UnknownOutcome => {
                Ok(BridgeDispatchResult::Unknown {
                    reason: UnknownReason::BridgeLost,
                })
            }
            // A write failure while posting the request is before the extension
            // dispatch boundary. It is safe to retry only after the caller has
            // refreshed the live page/ref/generation context.
            Err(error) => Ok(BridgeDispatchResult::Failed {
                code: error.code,
                retryable: error.retryable,
            }),
        }
    }

    fn artifact_for_action(
        &self,
        action_id: &ActionId,
    ) -> Result<Option<ArtifactHandle>, CoreError> {
        self.shared
            .session
            .lock()
            .map(|session| session.artifacts_by_action.get(action_id.as_str()).cloned())
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "artifact state is poisoned",
                )
            })
    }

    fn take_artifact(&self, handle: &ArtifactHandle) -> Result<Option<Vec<u8>>, CoreError> {
        let mut session = self.shared.session.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::NativeHostUnavailable,
                "artifact state is poisoned",
            )
        })?;
        let Some(owned) = session.artifacts_by_action.get(handle.action_id.as_str()) else {
            return Ok(None);
        };
        if owned != handle {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "artifact handle is not the retained action artifact",
            ));
        }
        let Some(artifact) = session.completed_artifacts.get(handle.artifact_id.as_str()) else {
            return Ok(None);
        };
        if artifact.begin.request_id.as_ref() != Some(&handle.request_id)
            || artifact.begin.artifact_kind != handle.artifact_kind
            || artifact.begin.total_bytes != handle.total_bytes
            || artifact.begin.chunk_count != handle.chunk_count
            || artifact.begin.digest != handle.digest
            || artifact.begin.redacted != handle.redacted
        {
            return Err(CoreError::new(
                ErrorCode::InvalidJson,
                "retained artifact metadata does not match its logical handle",
            ));
        }
        if ContentHash::from_bytes(&artifact.bytes) != handle.digest {
            return Err(CoreError::new(
                ErrorCode::InvalidJson,
                "retained artifact digest does not match its logical handle",
            ));
        }
        let artifact = session
            .completed_artifacts
            .remove(handle.artifact_id.as_str())
            .expect("artifact was present during validation");
        session
            .artifacts_by_action
            .remove(handle.action_id.as_str());
        Ok(Some(artifact.bytes))
    }

    fn reconcile(&self, receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError> {
        let mut params = Map::new();
        params.insert("action_id".to_owned(), json!(receipt.action_id.to_string()));
        params.insert("space_id".to_owned(), json!(receipt.space_id.to_string()));
        if let Some(page_id) = &receipt.page_id {
            params.insert("page_id".to_owned(), json!(page_id.to_string()));
        }
        params.insert("lease_epoch".to_owned(), json!(receipt.lease_epoch.get()));
        params.insert("operation".to_owned(), json!(receipt.operation));
        if let Some(token) = &receipt.reconcile_token {
            params.insert("reconcile_token".to_owned(), json!(token.to_string()));
        }
        if let Some(postcondition) = &receipt.postcondition {
            params.insert(
                "postcondition".to_owned(),
                serde_json::to_value(postcondition).map_err(|_| {
                    CoreError::new(ErrorCode::InvalidJson, "action postcondition is not JSON")
                })?,
            );
        }
        match self.request_value("action.reconcile", params, None) {
            Ok(result) => match result.get("outcome").and_then(Value::as_str) {
                Some("succeeded") => Ok(BridgeReconcileResult::Succeeded),
                Some("failed") => Ok(BridgeReconcileResult::Failed {
                    code: result
                        .get("code")
                        .and_then(Value::as_str)
                        .map(parse_error_code)
                        .unwrap_or(ErrorCode::InvalidArgument),
                    requires_confirmation: result
                        .get("requires_confirmation")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
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
        _space_id: &SpaceId,
        _old_epoch: Option<LeaseEpoch>,
        _new_epoch: LeaseEpoch,
        _broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        Ok(FenceResult {
            acknowledged: false,
        })
    }

    fn fence_with_token(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        broker_epoch: BrokerEpoch,
        request_token: &ReconcileToken,
    ) -> Result<FenceResult, CoreError> {
        self.fence_request(space_id, old_epoch, new_epoch, broker_epoch, request_token)
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
    let parsed_hello = match parse_hello(&first, &shared) {
        Ok(hello) => hello,
        Err(error) => {
            let _ = hello_tx.send(Err(error.clone()));
            mark_closed(&shared, error);
            return;
        }
    };
    let hello = parsed_hello.hello.clone();
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
        session.resume_from = parsed_hello.resume_from;
        session.next_inbound_sequence = 2;
        session.extension_limits = parsed_hello.limits;
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

#[derive(Debug)]
struct ParsedNativeHello {
    hello: NativeHello,
    resume_from: Option<ResumeWatermark>,
    limits: NativeNegotiatedLimits,
}

fn parse_hello(
    payload: &[u8],
    shared: &NativeShared,
) -> Result<ParsedNativeHello, NativeHostError> {
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
    validate_native_shape(&value, "hello")?;
    let expected_origin = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?
        .expected_origin
        .clone();
    if !is_allowed_extension_origin(&expected_origin) {
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
    let limits = parse_limits(object.get("limits"))?;
    let resume_from = parse_native_resume_cursor(
        object
            .get("resume_from")
            .or_else(|| object.get("resume_cursor")),
    )?;
    if let Some(profile_state) = object.get("profile_state").and_then(Value::as_str)
        && profile_state != "bound"
    {
        return Err(NativeHostError::Protocol(
            "Native Messaging hello requires a bound profile".to_owned(),
        ));
    }
    Ok(ParsedNativeHello {
        hello: NativeHello {
            protocol: u16::try_from(protocol)
                .map_err(|_| NativeHostError::Protocol("hello protocol is invalid".to_owned()))?,
            nonce,
            sequence,
            worker_instance_epoch,
            browser_session_epoch,
            profile_instance_id,
            extension_version,
            capabilities,
            resume_from,
        },
        resume_from,
        limits,
    })
}

fn handle_inbound(payload: Vec<u8>, shared: &NativeShared) -> Result<(), NativeHostError> {
    {
        let session = shared
            .session
            .lock()
            .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
        if session.handshake_complete && payload.len() > session.negotiated_limits.max_control_bytes
        {
            return Err(NativeHostError::MessageTooLarge);
        }
    }
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
    validate_native_shape(&value, kind)?;
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
        "artifact_begin" => handle_native_artifact_begin(shared, value)?,
        "artifact_chunk" => handle_native_artifact_chunk(shared, value)?,
        "artifact_end" => handle_native_artifact_end(shared, value)?,
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

fn validate_native_shape(value: &Value, kind: &str) -> Result<(), NativeHostError> {
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging envelope is not an object".to_owned())
    })?;
    let mut allowed = vec!["protocol", "kind", "nonce", "sequence"];
    let hello = kind == "hello";
    if hello {
        allowed.extend([
            "worker_instance_epoch",
            "browser_session_epoch",
            "profile_instance_id",
            "profile_state",
            "extension_version",
            "capabilities",
            "limits",
            "resume_from",
            "resume_cursor",
        ]);
    } else {
        allowed.extend([
            "broker_epoch",
            "connection_epoch",
            "worker_instance_epoch",
            "browser_session_epoch",
        ]);
    }
    let kind_fields: &[&str] = match kind {
        "hello" => &[],
        "hello_ok" => &[
            "capabilities",
            "limits",
            "profile_instance_id",
            "profile_state",
            "resume",
            "cursor",
        ],
        "request" => &[
            "request_id",
            "action_id",
            "method",
            "params",
            "deadline_ms",
            "request_hash",
            "context",
        ],
        "response" | "action_result" => &[
            "request_id",
            "action_id",
            "ok",
            "result",
            "error",
            "warnings",
        ],
        "event" => &["event", "payload", "space_id", "page_id", "cursor"],
        "inventory" => &["payload"],
        "fence" => &[
            "request_id",
            "request_token",
            "space_id",
            "old_epoch",
            "lease_epoch",
            "fence_epoch",
            "broker_epoch",
            "connection_epoch",
            "durable",
            "params",
        ],
        "fence_ack" => &[
            "request_id",
            "request_token",
            "space_id",
            "old_epoch",
            "lease_epoch",
            "fence_epoch",
            "broker_epoch",
            "connection_epoch",
            "durable",
            "ok",
            "result",
            "error",
            "warnings",
        ],
        "cancel" => &["request_id", "reason"],
        "error" => &["error"],
        "artifact_begin" => &[
            "artifact_id",
            "request_id",
            "artifact_kind",
            "total_bytes",
            "chunk_size",
            "chunk_count",
            "digest_algorithm",
            "digest",
            "redacted",
        ],
        "artifact_chunk" => &["artifact_id", "connection_epoch", "chunk_sequence", "bytes"],
        "artifact_end" => &[
            "artifact_id",
            "total_bytes",
            "chunk_count",
            "digest_algorithm",
            "digest",
        ],
        _ => {
            return Err(NativeHostError::Protocol(
                "Native Messaging kind is unsupported".to_owned(),
            ));
        }
    };
    allowed.extend(kind_fields);
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(NativeHostError::Protocol(format!(
            "unknown Native Messaging field {key}"
        )));
    }

    if hello {
        require_positive(object, "worker_instance_epoch")?;
        require_positive(object, "browser_session_epoch")?;
        require_text(object, "profile_instance_id", 128)?;
        require_text(object, "extension_version", 128)?;
        require_string_array(object, "capabilities", MAX_NATIVE_CAPABILITIES)?;
        if let Some(profile_state) = object.get("profile_state")
            && profile_state.as_str() != Some("bound")
        {
            return Err(NativeHostError::Protocol(
                "hello profile_state must be bound".to_owned(),
            ));
        }
        if object.contains_key("limits") {
            let _ = parse_limits(object.get("limits"))?;
        }
        if object.contains_key("resume_from") && object.contains_key("resume_cursor") {
            return Err(NativeHostError::Protocol(
                "hello cannot contain both resume_from and resume_cursor".to_owned(),
            ));
        }
        let _ = parse_native_resume_cursor(
            object
                .get("resume_from")
                .or_else(|| object.get("resume_cursor")),
        )?;
        return Ok(());
    }

    for key in [
        "broker_epoch",
        "connection_epoch",
        "worker_instance_epoch",
        "browser_session_epoch",
    ] {
        require_positive(object, key)?;
    }
    match kind {
        "hello_ok" => {
            require_string_array(object, "capabilities", MAX_NATIVE_CAPABILITIES)?;
            let limits = parse_limits(object.get("limits"))?;
            limits.validate()?;
            require_text(object, "profile_instance_id", 128)?;
            if object.get("profile_state").and_then(Value::as_str) != Some("bound") {
                return Err(NativeHostError::Protocol(
                    "hello_ok profile_state must be bound".to_owned(),
                ));
            }
            if let Some(resume) = object.get("resume") {
                let _ = parse_native_resume_result(resume)?;
            }
            let _ = parse_native_resume_cursor(object.get("cursor"))?;
        }
        "request" => {
            require_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
            let method = require_text(object, "method", 128)?;
            if !valid_native_method(&method) {
                return Err(NativeHostError::Protocol(
                    "request method is invalid".to_owned(),
                ));
            }
            let params = object
                .get("params")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    NativeHostError::Protocol("request params are required".to_owned())
                })?;
            if params.len() > MAX_NATIVE_COLLECTION_ITEMS {
                return Err(NativeHostError::MessageTooLarge);
            }
            if let Some(deadline) = object.get("deadline_ms")
                && !deadline.is_null()
                && (deadline
                    .as_u64()
                    .is_none_or(|value| value == 0 || value > MAX_NATIVE_DEADLINE_MS))
            {
                return Err(NativeHostError::Protocol(
                    "request deadline is invalid".to_owned(),
                ));
            }
            optional_text(object, "action_id", MAX_NATIVE_ID_BYTES)?;
            optional_text(object, "request_hash", 128)?;
            optional_text(object, "context", MAX_NATIVE_STRING_BYTES)?;
        }
        "response" | "action_result" => validate_native_response(object)?,
        "event" => {
            require_text(object, "event", 128)?;
            object
                .get("payload")
                .and_then(Value::as_object)
                .ok_or_else(|| NativeHostError::Protocol("event payload is required".to_owned()))?;
            optional_text(object, "space_id", MAX_NATIVE_ID_BYTES)?;
            optional_text(object, "page_id", MAX_NATIVE_ID_BYTES)?;
            let _ = parse_native_resume_cursor(object.get("cursor"))?;
        }
        "inventory" => {
            object
                .get("payload")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    NativeHostError::Protocol("inventory payload is required".to_owned())
                })?;
        }
        "fence" => {
            require_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
            require_text(object, "request_token", MAX_NATIVE_ID_BYTES)?;
            require_text(object, "space_id", MAX_NATIVE_ID_BYTES)?;
            require_positive(object, "lease_epoch")?;
            require_positive(object, "fence_epoch")?;
            if object.get("durable").and_then(Value::as_bool) != Some(true) {
                return Err(NativeHostError::Protocol(
                    "fence must be durable".to_owned(),
                ));
            }
            object
                .get("params")
                .and_then(Value::as_object)
                .ok_or_else(|| NativeHostError::Protocol("fence params are required".to_owned()))?;
        }
        "fence_ack" => {
            require_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
            validate_native_response(object)?;
        }
        "cancel" => {
            require_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
            optional_text(object, "reason", 256)?;
        }
        "error" => {
            object
                .get("error")
                .and_then(Value::as_object)
                .ok_or_else(|| NativeHostError::Protocol("error payload is required".to_owned()))?;
        }
        "artifact_begin" => validate_native_artifact_begin(object)?,
        "artifact_chunk" => {
            require_text(object, "artifact_id", MAX_NATIVE_ID_BYTES)?;
            if object
                .get("connection_epoch")
                .and_then(Value::as_u64)
                .is_none()
            {
                return Err(NativeHostError::Protocol(
                    "artifact connection_epoch is invalid".to_owned(),
                ));
            }
            require_u64(object, "chunk_sequence")?;
            validate_native_bytes(object, "bytes", MAX_ARTIFACT_CHUNK_BYTES)?;
        }
        "artifact_end" => {
            require_text(object, "artifact_id", MAX_NATIVE_ID_BYTES)?;
            require_u64(object, "total_bytes")?;
            require_u64(object, "chunk_count")?;
            require_text(object, "digest_algorithm", 32)?;
            require_text(object, "digest", 128)?;
        }
        _ => unreachable!(),
    }
    Ok(())
}

fn validate_native_response(object: &Map<String, Value>) -> Result<(), NativeHostError> {
    require_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
    let ok = object
        .get("ok")
        .and_then(Value::as_bool)
        .ok_or_else(|| NativeHostError::Protocol("response ok is required".to_owned()))?;
    let result = object.get("result");
    let error = object.get("error");
    if ok != result.is_some_and(|value| !value.is_null())
        || ok == error.is_some_and(|value| !value.is_null())
    {
        return Err(NativeHostError::Protocol(
            "response result/error fields do not match ok".to_owned(),
        ));
    }
    if let Some(warnings) = object.get("warnings") {
        let warnings = warnings
            .as_array()
            .ok_or_else(|| NativeHostError::Protocol("response warnings are invalid".to_owned()))?;
        if warnings.len() > MAX_NATIVE_WARNINGS
            || warnings.iter().any(|value| {
                value
                    .as_str()
                    .is_none_or(|text| text.is_empty() || text.len() > MAX_NATIVE_WARNING_BYTES)
            })
        {
            return Err(NativeHostError::MessageTooLarge);
        }
    }
    Ok(())
}

fn validate_native_artifact_begin(object: &Map<String, Value>) -> Result<(), NativeHostError> {
    require_text(object, "artifact_id", MAX_NATIVE_ID_BYTES)?;
    optional_text(object, "request_id", MAX_NATIVE_ID_BYTES)?;
    require_text(object, "artifact_kind", 32)?;
    let total_bytes = require_u64(object, "total_bytes")?;
    let chunk_size = require_u64(object, "chunk_size")?;
    let chunk_count = require_u64(object, "chunk_count")?;
    if total_bytes > MAX_ARTIFACT_BYTES
        || chunk_size == 0
        || chunk_size > MAX_ARTIFACT_CHUNK_BYTES as u64
        || chunk_count > u64::from(MAX_ARTIFACT_CHUNKS)
    {
        return Err(NativeHostError::MessageTooLarge);
    }
    require_text(object, "digest_algorithm", 32)?;
    require_text(object, "digest", 128)?;
    object
        .get("redacted")
        .and_then(Value::as_bool)
        .ok_or_else(|| NativeHostError::Protocol("artifact redacted is invalid".to_owned()))?;
    Ok(())
}

fn validate_native_bytes(
    object: &Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<(), NativeHostError> {
    let bytes = object
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} must be a byte array")))?;
    if bytes.len() > max {
        return Err(NativeHostError::MessageTooLarge);
    }
    if bytes
        .iter()
        .any(|value| value.as_u64().is_none_or(|byte| byte > u64::from(u8::MAX)))
    {
        return Err(NativeHostError::Protocol(format!(
            "{key} contains an invalid byte"
        )));
    }
    Ok(())
}

fn require_text(
    object: &Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<String, NativeHostError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= max)
        .map(str::to_owned)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} is missing or invalid")))
}

fn optional_text(
    object: &Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<(), NativeHostError> {
    if let Some(value) = object.get(key)
        && !value.is_null()
        && value
            .as_str()
            .is_none_or(|text| text.is_empty() || text.len() > max)
    {
        return Err(NativeHostError::Protocol(format!("{key} is invalid")));
    }
    Ok(())
}

fn require_u64(object: &Map<String, Value>, key: &str) -> Result<u64, NativeHostError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} is missing or invalid")))
}

fn require_positive(object: &Map<String, Value>, key: &str) -> Result<u64, NativeHostError> {
    let value = require_u64(object, key)?;
    if value == 0 {
        return Err(NativeHostError::Protocol(format!("{key} must be positive")));
    }
    Ok(value)
}

fn require_string_array(
    object: &Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<(), NativeHostError> {
    let values = object
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| NativeHostError::Protocol(format!("{key} must be an array")))?;
    if values.len() > max
        || values.iter().any(|value| {
            value
                .as_str()
                .is_none_or(|text| text.is_empty() || text.len() > 128)
        })
    {
        return Err(NativeHostError::Protocol(format!("{key} is invalid")));
    }
    Ok(())
}

fn parse_limits(value: Option<&Value>) -> Result<NativeNegotiatedLimits, NativeHostError> {
    let Some(value) = value else {
        return Ok(NativeNegotiatedLimits::host_defaults());
    };
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging limits are invalid".to_owned())
    })?;
    let allowed = [
        "max_control_bytes",
        "max_artifact_chunk_bytes",
        "max_artifact_bytes",
        "max_artifact_chunks",
        "max_in_flight_artifact_bytes",
        "max_cumulative_artifact_bytes",
    ];
    if object.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(NativeHostError::Protocol(
            "Native Messaging limits contain an unknown field".to_owned(),
        ));
    }
    let limits = NativeNegotiatedLimits {
        max_control_bytes: usize::try_from(require_u64(object, "max_control_bytes")?)
            .map_err(|_| NativeHostError::MessageTooLarge)?,
        max_artifact_chunk_bytes: usize::try_from(require_u64(object, "max_artifact_chunk_bytes")?)
            .map_err(|_| NativeHostError::MessageTooLarge)?,
        max_artifact_bytes: require_u64(object, "max_artifact_bytes")?,
        max_artifact_chunks: u16::try_from(require_u64(object, "max_artifact_chunks")?)
            .map_err(|_| NativeHostError::MessageTooLarge)?,
        max_in_flight_artifact_bytes: usize::try_from(require_u64(
            object,
            "max_in_flight_artifact_bytes",
        )?)
        .map_err(|_| NativeHostError::MessageTooLarge)?,
        max_cumulative_artifact_bytes: require_u64(object, "max_cumulative_artifact_bytes")?,
    };
    limits.validate()
}

fn valid_native_method(method: &str) -> bool {
    let mut parts = method.split('.');
    !method.is_empty()
        && method.len() <= 128
        && parts.all(|part| {
            !part.is_empty()
                && part.bytes().all(|byte| {
                    byte.is_ascii_lowercase()
                        || byte.is_ascii_digit()
                        || byte == b'_'
                        || byte == b'-'
                })
        })
}

fn handle_native_artifact_begin(
    shared: &NativeShared,
    value: Value,
) -> Result<(), NativeHostError> {
    let begin: ArtifactBeginEnvelope = serde_json::from_value(value).map_err(|error| {
        NativeHostError::Protocol(format!("artifact_begin is invalid: {error}"))
    })?;
    begin
        .validate()
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    let key = begin.artifact_id.to_string();
    let mut session = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
    if session.artifact_transfers.len() >= MAX_NATIVE_ARTIFACT_TRANSFERS
        || session.artifact_transfers.contains_key(&key)
    {
        return Err(NativeHostError::Protocol(
            "artifact transfer is duplicated or exceeds the connection bound".to_owned(),
        ));
    }
    let limits = session.negotiated_limits;
    if begin.total_bytes > limits.max_artifact_bytes
        || u64::from(begin.chunk_size) > limits.max_artifact_chunk_bytes as u64
        || begin.chunk_count > limits.max_artifact_chunks
    {
        return Err(NativeHostError::MessageTooLarge);
    }
    let connection_epoch = ConnectionEpoch::new(session.connection_epoch.ok_or_else(|| {
        NativeHostError::Protocol("artifact connection epoch is missing".to_owned())
    })?);
    let progress = agentyc_core::ArtifactTransferProgress::begin(&begin, connection_epoch)
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    session.artifact_transfers.insert(
        key,
        NativeArtifactTransfer {
            begin,
            progress,
            bytes: Vec::new(),
        },
    );
    Ok(())
}

fn handle_native_artifact_chunk(
    shared: &NativeShared,
    value: Value,
) -> Result<(), NativeHostError> {
    let chunk: ArtifactChunkEnvelope = serde_json::from_value(value).map_err(|error| {
        NativeHostError::Protocol(format!("artifact_chunk is invalid: {error}"))
    })?;
    chunk
        .validate()
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    let key = chunk.artifact_id.to_string();
    let mut session = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
    let limits = session.negotiated_limits;
    if chunk.bytes.len() > limits.max_artifact_chunk_bytes {
        return Err(NativeHostError::MessageTooLarge);
    }
    if session
        .artifact_budget
        .in_flight_bytes()
        .saturating_add(chunk.bytes.len())
        > limits.max_in_flight_artifact_bytes
    {
        return Err(NativeHostError::MessageTooLarge);
    }
    session
        .artifact_budget
        .receive(chunk.bytes.len())
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    let Some(transfer) = session.artifact_transfers.get_mut(&key) else {
        let _ = session.artifact_budget.release(chunk.bytes.len());
        return Err(NativeHostError::Protocol(
            "artifact chunk has no active begin".to_owned(),
        ));
    };
    if let Err(error) = transfer.progress.accept_chunk(&chunk) {
        let _ = session.artifact_budget.release(chunk.bytes.len());
        return Err(NativeHostError::Protocol(error.to_string()));
    }
    transfer.bytes.extend_from_slice(&chunk.bytes);
    Ok(())
}

fn handle_native_artifact_end(shared: &NativeShared, value: Value) -> Result<(), NativeHostError> {
    let end: ArtifactEndEnvelope = serde_json::from_value(value)
        .map_err(|error| NativeHostError::Protocol(format!("artifact_end is invalid: {error}")))?;
    end.validate()
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    let key = end.artifact_id.to_string();
    let mut session = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
    let Some(transfer) = session.artifact_transfers.remove(&key) else {
        return Err(NativeHostError::Protocol(
            "artifact end has no active begin".to_owned(),
        ));
    };
    let byte_count = transfer.bytes.len();
    let result = transfer
        .progress
        .validate_complete()
        .and_then(|_| end.validate_against(&transfer.begin, &transfer.bytes));
    let release = session.artifact_budget.release(byte_count);
    result
        .and(release)
        .map_err(|error| NativeHostError::Protocol(error.to_string()))?;
    if session.completed_artifacts.len() >= MAX_NATIVE_ARTIFACT_TRANSFERS {
        return Err(NativeHostError::MessageTooLarge);
    }
    session.completed_artifacts.insert(
        key,
        NativeArtifact {
            begin: transfer.begin,
            bytes: transfer.bytes,
        },
    );
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
    let mut logical = object.clone();
    for key in [
        "protocol",
        "nonce",
        "sequence",
        "broker_epoch",
        "connection_epoch",
        "worker_instance_epoch",
        "browser_session_epoch",
    ] {
        logical.remove(key);
    }
    let fingerprint =
        agentyc_core::ContentHash::from_bytes(&serde_json::to_vec(&logical).map_err(|_| {
            NativeHostError::Protocol("Native Messaging request cannot be fingerprinted".to_owned())
        })?);
    let mut fingerprints = shared.request_fingerprints.lock().map_err(|_| {
        NativeHostError::Unavailable("request fingerprint state is poisoned".to_owned())
    })?;
    if let Some(existing) = fingerprints.get(&request_id) {
        return Err(if existing == &fingerprint {
            NativeHostError::Protocol("duplicate Native Messaging request_id".to_owned())
        } else {
            NativeHostError::Protocol(
                "Native Messaging request_id conflicts with a different hash or context".to_owned(),
            )
        });
    }
    if fingerprints.len() >= MAX_NATIVE_PENDING_REQUESTS * 16
        && let Some(oldest) = fingerprints.keys().next().cloned()
    {
        fingerprints.remove(&oldest);
    }
    fingerprints.insert(request_id.clone(), fingerprint);
    drop(fingerprints);
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
    accept_inbound_cursor(shared, &value)?;
    let mut events = shared
        .events
        .lock()
        .map_err(|_| NativeHostError::Unavailable("event state is poisoned".to_owned()))?;
    if events.len() >= MAX_NATIVE_EVENT_QUEUE {
        // Preserve an explicit broker-visible loss marker instead of tearing
        // down Native Messaging. The host must invalidate affected state and
        // require a fresh inventory/snapshot after this bounded overflow.
        events.clear();
        let mut marker = value;
        if let Some(object) = marker.as_object_mut() {
            object.insert(
                "event".to_owned(),
                Value::String("browser.event_gap".to_owned()),
            );
            object.remove("space_id");
            object.remove("page_id");
            object.insert(
                "payload".to_owned(),
                serde_json::json!({
                    "reason": "native_event_queue_overflow",
                    "resync_required": true,
                }),
            );
        }
        events.push_back(marker);
        return Ok(());
    }
    events.push_back(value);
    Ok(())
}

fn accept_inbound_cursor(shared: &NativeShared, value: &Value) -> Result<(), NativeHostError> {
    let Some(cursor) = parse_native_resume_cursor(value.get("cursor"))? else {
        return Ok(());
    };
    let mut session = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?;
    let broker_epoch = session
        .broker_epoch
        .ok_or_else(|| NativeHostError::Protocol("broker epoch is missing".to_owned()))?;
    if cursor.broker_epoch.get() != broker_epoch {
        return Err(NativeHostError::Protocol(
            "Native Messaging broker cursor epoch is stale".to_owned(),
        ));
    }
    if session
        .last_accepted_cursor
        .is_some_and(|previous| cursor.sequence.get() < previous.sequence.get())
    {
        return Err(NativeHostError::Protocol(
            "Native Messaging broker cursor is stale".to_owned(),
        ));
    }
    session.last_accepted_cursor = Some(cursor);
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

fn native_action_result(
    shared: &NativeShared,
    request: &ActionRequest<BTreeMap<String, String>>,
    result: Value,
) -> BridgeDispatchResult {
    let outcome = native_action_outcome(&result);
    if !matches!(outcome, BridgeDispatchResult::Succeeded)
        || request.operation != agentyc_core::ActionOperation::Screenshot
    {
        return outcome;
    }
    let handle = match artifact_handle_from_result(request, &result) {
        Ok(Some(handle)) => handle,
        Ok(None) | Err(_) => {
            return BridgeDispatchResult::Failed {
                code: ErrorCode::InvalidJson,
                retryable: false,
            };
        }
    };
    let retained = shared.session.lock().ok().is_some_and(|mut session| {
        if !session
            .completed_artifacts
            .contains_key(handle.artifact_id.as_str())
        {
            return false;
        }
        session
            .artifacts_by_action
            .insert(handle.action_id.to_string(), handle);
        true
    });
    if retained {
        BridgeDispatchResult::Succeeded
    } else {
        BridgeDispatchResult::Failed {
            code: ErrorCode::InvalidJson,
            retryable: false,
        }
    }
}

fn native_action_outcome(result: &Value) -> BridgeDispatchResult {
    let receipt = result.get("receipt").and_then(Value::as_object);
    let proof = result.get("action_proof").and_then(Value::as_object);
    if proof
        .and_then(|proof| proof.get("outcome"))
        .and_then(Value::as_str)
        == Some("unknown")
    {
        return BridgeDispatchResult::Unknown {
            reason: UnknownReason::BridgeLost,
        };
    }
    if proof
        .and_then(|proof| proof.get("outcome"))
        .and_then(Value::as_str)
        == Some("confirmation_required")
    {
        return BridgeDispatchResult::Failed {
            code: ErrorCode::PermissionDenied,
            retryable: false,
        };
    }
    if proof
        .and_then(|proof| proof.get("outcome"))
        .and_then(Value::as_str)
        == Some("failed")
    {
        return BridgeDispatchResult::Failed {
            code: proof
                .and_then(|proof| proof.get("code"))
                .and_then(Value::as_str)
                .map(parse_error_code)
                .unwrap_or(ErrorCode::InvalidArgument),
            retryable: false,
        };
    }
    if receipt
        .and_then(|receipt| receipt.get("outcome"))
        .and_then(Value::as_str)
        == Some("unknown")
    {
        return BridgeDispatchResult::Unknown {
            reason: UnknownReason::BridgeLost,
        };
    }
    if receipt
        .and_then(|receipt| receipt.get("postcondition_satisfied"))
        .and_then(Value::as_bool)
        == Some(false)
    {
        return BridgeDispatchResult::Failed {
            code: ErrorCode::TargetReplaced,
            retryable: false,
        };
    }
    if receipt
        .and_then(|receipt| receipt.get("outcome"))
        .and_then(Value::as_str)
        == Some("failed")
    {
        return BridgeDispatchResult::Failed {
            code: receipt
                .and_then(|receipt| receipt.get("code"))
                .and_then(Value::as_str)
                .map(parse_error_code)
                .unwrap_or(ErrorCode::InvalidArgument),
            retryable: false,
        };
    }
    BridgeDispatchResult::Succeeded
}

fn artifact_handle_from_result(
    request: &ActionRequest<BTreeMap<String, String>>,
    result: &Value,
) -> Result<Option<ArtifactHandle>, CoreError> {
    let Some(artifact) = result.get("result").and_then(Value::as_object) else {
        return Ok(None);
    };
    let Some(artifact_id) = artifact.get("artifact_handle").and_then(Value::as_str) else {
        return Ok(None);
    };
    let artifact_id = artifact_id
        .parse::<agentyc_core::ArtifactId>()
        .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    let artifact_kind = serde_json::from_value::<ArtifactKind>(
        artifact
            .get("artifact_kind")
            .cloned()
            .ok_or_else(|| CoreError::invalid_argument("artifact_kind is required"))?,
    )
    .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    let total_bytes = artifact
        .get("total_bytes")
        .and_then(Value::as_u64)
        .ok_or_else(|| CoreError::invalid_argument("artifact total_bytes is required"))?;
    let chunk_count = artifact
        .get("chunk_count")
        .and_then(Value::as_u64)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or_else(|| CoreError::invalid_argument("artifact chunk_count is invalid"))?;
    let digest = artifact
        .get("digest")
        .and_then(Value::as_str)
        .ok_or_else(|| CoreError::invalid_argument("artifact digest is required"))?
        .parse::<ContentHash>()
        .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    let redacted = artifact
        .get("redacted")
        .and_then(Value::as_bool)
        .ok_or_else(|| CoreError::invalid_argument("artifact redacted flag is required"))?;
    Ok(Some(ArtifactHandle {
        artifact_id,
        request_id: request.request_id.clone(),
        action_id: request.action_id.clone(),
        space_id: request.space_id.clone(),
        page_id: request.page_id.clone(),
        artifact_kind,
        total_bytes,
        chunk_count,
        digest,
        redacted,
    }))
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
        "permission_denied" | "evaluate_denied" | "user_confirmation_required" => {
            ErrorCode::PermissionDenied
        }
        "user_control_required" => ErrorCode::UserControlRequired,
        "stale_ref" => ErrorCode::StaleRef,
        "target_replaced" => ErrorCode::TargetReplaced,
        "stale_lease" => ErrorCode::StaleLease,
        "page_not_found" => ErrorCode::PageNotFound,
        "element_not_found" => ErrorCode::StaleRef,
        "capability_unavailable" | "upload_denied" => ErrorCode::CapabilityUnavailable,
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
    let kind = value
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| NativeHostError::Protocol("Native Messaging kind is missing".to_owned()))?;
    validate_native_shape(value, kind)?;
    assert_no_raw_browser_identifiers(value, "")?;
    let payload = serde_json::to_vec(value).map_err(|_| {
        NativeHostError::Protocol("Native Messaging envelope is not JSON".to_owned())
    })?;
    let max_control_bytes = shared
        .session
        .lock()
        .map_err(|_| NativeHostError::Unavailable("session state is poisoned".to_owned()))?
        .negotiated_limits
        .max_control_bytes;
    if payload.len() > max_control_bytes {
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
    validate_json_budget(&value, 0, None)?;
    Ok(value)
}

fn validate_json_budget(
    value: &Value,
    depth: usize,
    parent_key: Option<&str>,
) -> Result<(), NativeHostError> {
    if depth > MAX_NATIVE_DEPTH {
        return Err(NativeHostError::MessageTooLarge);
    }
    match value {
        Value::String(value) if value.len() > MAX_NATIVE_STRING_BYTES => {
            Err(NativeHostError::MessageTooLarge)
        }
        Value::Array(values) => {
            let max_items = if parent_key == Some("bytes") {
                MAX_ARTIFACT_CHUNK_BYTES
            } else {
                MAX_NATIVE_COLLECTION_ITEMS
            };
            if values.len() > max_items {
                return Err(NativeHostError::MessageTooLarge);
            }
            for value in values {
                validate_json_budget(value, depth + 1, parent_key)?;
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
                validate_json_budget(value, depth + 1, Some(key))?;
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

fn parse_native_resume_cursor(
    value: Option<&Value>,
) -> Result<Option<ResumeWatermark>, NativeHostError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let object = value.as_object().ok_or_else(|| {
        NativeHostError::Protocol("Native Messaging resume cursor must be an object".to_owned())
    })?;
    if object.len() != 2
        || object
            .keys()
            .any(|key| !matches!(key.as_str(), "broker_epoch" | "sequence"))
    {
        return Err(NativeHostError::Protocol(
            "Native Messaging resume cursor has unknown fields".to_owned(),
        ));
    }
    let broker_epoch = object
        .get("broker_epoch")
        .and_then(Value::as_u64)
        .ok_or_else(|| {
            NativeHostError::Protocol(
                "Native Messaging resume cursor broker_epoch is invalid".to_owned(),
            )
        })?;
    let sequence = object
        .get("sequence")
        .and_then(Value::as_u64)
        .ok_or_else(|| {
            NativeHostError::Protocol(
                "Native Messaging resume cursor sequence is invalid".to_owned(),
            )
        })?;
    let cursor = ResumeWatermark {
        broker_epoch: BrokerEpoch::new(broker_epoch),
        sequence: agentyc_core::EventSequence::new(sequence),
    };
    validate_native_resume_cursor(cursor)?;
    Ok(Some(cursor))
}

fn validate_native_resume_cursor(cursor: ResumeWatermark) -> Result<(), NativeHostError> {
    if cursor.broker_epoch.get() == 0
        || cursor.broker_epoch.get() > MAX_NATIVE_RESUME_CURSOR_VALUE
        || cursor.sequence.get() > MAX_NATIVE_RESUME_CURSOR_VALUE
    {
        return Err(NativeHostError::Protocol(
            "Native Messaging resume cursor is outside its bound".to_owned(),
        ));
    }
    Ok(())
}

fn native_cursor_value(cursor: ResumeWatermark) -> Value {
    json!({
        "broker_epoch": cursor.broker_epoch.get(),
        "sequence": cursor.sequence.get(),
    })
}

fn parse_native_resume_result(value: &Value) -> Result<ResumeResult, NativeHostError> {
    let status = value
        .as_str()
        .map(str::to_owned)
        .or_else(|| {
            value
                .as_object()
                .filter(|object| object.len() == 1)
                .and_then(|object| object.get("kind"))
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .ok_or_else(|| {
            NativeHostError::Protocol("Native Messaging resume status is invalid".to_owned())
        })?;
    match status.as_str() {
        "accepted" => Ok(ResumeResult::Accepted),
        "resync_required" => Ok(ResumeResult::ResyncRequired),
        _ => Err(NativeHostError::Protocol(
            "Native Messaging resume status is unsupported".to_owned(),
        )),
    }
}

fn resume_result_wire(result: ResumeResult) -> &'static str {
    match result {
        ResumeResult::Accepted => "accepted",
        ResumeResult::ResyncRequired => "resync_required",
    }
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

fn capability_for_name(value: &str) -> Option<Capability> {
    match value {
        "logical_tabs" | "debugger_allowlist" | "visual_groups" => Some(Capability::Action),
        "frame_events" => Some(Capability::Wait),
        "snapshot" => Some(Capability::Snapshot),
        "evaluate" => Some(Capability::Evaluate),
        "reconcile" => Some(Capability::Reconcile),
        "artifact_transfer" | "artifact" => Some(Capability::Artifact),
        _ => None,
    }
}

fn map_capabilities(values: &[String]) -> Vec<Capability> {
    let mut capabilities = Vec::new();
    for value in values {
        if let Some(capability) = capability_for_name(value)
            && !capabilities.contains(&capability)
        {
            capabilities.push(capability);
        }
    }
    capabilities
}

fn intersect_capabilities(extension: &[String], host: &[Capability]) -> Vec<Capability> {
    host.iter()
        .copied()
        .filter(|capability| {
            extension
                .iter()
                .filter_map(|name| capability_for_name(name))
                .any(|advertised| advertised == *capability)
        })
        .fold(Vec::new(), |mut result, capability| {
            if !result.contains(&capability) {
                result.push(capability);
            }
            result
        })
}

fn intersect_capability_names(extension: &[String], host: &[Capability]) -> Vec<String> {
    extension
        .iter()
        .filter(|name| {
            capability_for_name(name).is_some_and(|capability| host.contains(&capability))
        })
        .fold(Vec::new(), |mut result, name| {
            if !result.contains(name) {
                result.push(name.clone());
            }
            result
        })
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
    if let Ok(mut session) = shared.session.lock() {
        session.artifact_transfers.clear();
        session.completed_artifacts.clear();
        session.artifacts_by_action.clear();
        session.artifact_budget = ArtifactTransferBudget::default();
        session.outbound_artifact_transfers.clear();
        session.outbound_artifact_budget = ArtifactTransferBudget::default();
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

fn validate_configured_origin(value: &str) -> Result<String, NativeHostError> {
    let normalized = normalize_extension_origin(value)?;
    if !is_allowed_extension_origin(&normalized) {
        return Err(NativeHostError::OriginInvalid);
    }
    Ok(normalized)
}

fn is_allowed_extension_origin(value: &str) -> bool {
    ALLOWED_EXTENSION_ORIGINS.contains(&value)
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

    #[cfg(unix)]
    #[test]
    fn duplicate_native_shim_connections_are_received_by_one_owner_endpoint() {
        use std::os::unix::net::UnixStream;

        let directory = tempfile::tempdir_in("/tmp").expect("state directory");
        let server = NativeForwardServer::start(directory.path()).expect("forward server");
        let mut client = UnixStream::connect(server.socket_path()).expect("forward client");
        client
            .write_all(b"native-frame")
            .expect("write forward bytes");
        let mut forwarded = server
            .accept_forwarded(Duration::from_secs(1))
            .expect("forwarded connection");
        let mut bytes = [0_u8; 12];
        forwarded
            .read_exact(&mut bytes)
            .expect("read forward bytes");
        assert_eq!(&bytes, b"native-frame");
        server.stop();
        assert!(!native_forward_socket_path(directory.path()).exists());
    }

    #[test]
    fn chrome_origin_requires_exact_extension_id_and_normalizes_only_trailing_slash() {
        let origin = format!("{}/", ALLOWED_EXTENSION_ORIGINS[0]);
        assert_eq!(
            normalize_extension_origin(&origin).expect("origin"),
            origin.trim_end_matches('/')
        );
        assert_eq!(
            NativeMessagingConfig::new(&origin)
                .expect("allowlisted origin")
                .expected_origin,
            ALLOWED_EXTENSION_ORIGINS[0]
        );
        assert!(normalize_extension_origin("chrome-extension://*").is_err());
        assert!(
            normalize_extension_origin("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaz")
                .is_err()
        );
        assert!(
            NativeMessagingConfig::new("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                .is_err()
        );
        for unsafe_origin in [
            "chrome-extension://jgbllikljnllangilfgkhncepiockppj.evil",
            "chrome-extension://jgbllikljnllangilfgkhncepiockppj/path",
            "chrome-extension://jgbllikljnllangilfgkhncepiockppj?origin=evil",
            "chrome-extension://JGBLLIKLJNICALLANGILFGKHNCEPIOCKPPJ",
        ] {
            assert!(
                NativeMessagingConfig::new(unsafe_origin).is_err(),
                "{unsafe_origin}"
            );
        }
    }

    #[test]
    fn accept_rejects_mutated_or_non_allowlisted_configuration_before_reading() {
        let mut config =
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0]).expect("allowlisted origin");
        config.expected_origin = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_owned();
        let result = NativeMessagingBridge::accept(io::empty(), io::sink(), config);
        assert!(matches!(result, Err(NativeHostError::OriginInvalid)));
    }

    #[test]
    fn hello_rejects_forged_json_origin_even_when_it_matches_allowlist() {
        let (to_host, from_extension) = mpsc::sync_channel(1);
        let hello = json!({
            "protocol": PROTOCOL_VERSION,
            "kind": "hello",
            "nonce": "nonce_forged_origin",
            "sequence": 1,
            "worker_instance_epoch": 2,
            "browser_session_epoch": 3,
            "profile_instance_id": "profile_forged_origin",
            "extension_version": "0.1.0",
            "capabilities": [],
            "origin": ALLOWED_EXTENSION_ORIGINS[0],
        });
        to_host.send(frame_json(hello)).expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let result = NativeMessagingBridge::accept(
            reader,
            io::sink(),
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                .expect("allowlisted origin")
                .with_handshake_timeout(Duration::from_secs(1)),
        );
        assert!(matches!(
            result,
            Err(NativeHostError::Protocol(message)) if message.contains("origin is transport metadata")
        ));
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
    fn native_hello_resume_cursor_is_bounded_and_maps_to_core_hello() {
        let (to_host, from_extension) = mpsc::sync_channel(1);
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": "nonce_resume_cursor",
                "sequence": 1,
                "worker_instance_epoch": 2,
                "browser_session_epoch": 3,
                "profile_instance_id": "profile_resume_cursor",
                "extension_version": "0.1.0",
                "capabilities": [],
                "resume_from": {"broker_epoch": 7, "sequence": 11}
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let (hello, bridge) = NativeMessagingBridge::accept(
            reader,
            io::sink(),
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1)),
        )
        .expect("accept");
        let cursor = hello.resume_cursor().expect("resume cursor");
        assert_eq!(cursor.broker_epoch.get(), 7);
        assert_eq!(cursor.sequence.get(), 11);
        assert_eq!(
            hello
                .to_core_hello(PrincipalId::from_suffix("extension").expect("principal"))
                .expect("core hello")
                .resume_from,
            Some(cursor)
        );
        bridge
            .complete_handshake_with_resume(
                &hello,
                BrokerEpoch::new(7),
                agentyc_core::ConnectionEpoch::new(1),
                &[Capability::Action],
                ResumeResult::Accepted,
            )
            .expect("accepted resume handshake");
        assert_eq!(
            bridge.last_accepted_cursor().expect("cursor state"),
            Some(cursor)
        );
        drop(to_host);
        assert!(matches!(
            bridge.wait_closed(),
            NativeHostError::Unavailable(_)
        ));

        let (to_host, from_extension) = mpsc::sync_channel(1);
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": "nonce_resume_cursor_bound",
                "sequence": 1,
                "worker_instance_epoch": 2,
                "browser_session_epoch": 3,
                "profile_instance_id": "profile_resume_cursor_bound",
                "extension_version": "0.1.0",
                "capabilities": [],
                "resume_from": {"broker_epoch": 7, "sequence": MAX_NATIVE_RESUME_CURSOR_VALUE + 1}
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        assert!(matches!(
            NativeMessagingBridge::accept(
                reader,
                io::sink(),
                NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                    .expect("origin")
                    .with_handshake_timeout(Duration::from_secs(1)),
            ),
            Err(NativeHostError::Protocol(message)) if message.contains("resume cursor")
        ));
    }

    #[test]
    fn native_resume_status_handles_broker_epoch_and_retention_resync() {
        let (to_host, from_extension) = mpsc::sync_channel(1);
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": "nonce_resume_status_epoch",
                "sequence": 1,
                "worker_instance_epoch": 2,
                "browser_session_epoch": 3,
                "profile_instance_id": "profile_resume_status_epoch",
                "extension_version": "0.1.0",
                "capabilities": [],
                "resume_from": {"broker_epoch": 7, "sequence": 11}
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let capture = Arc::new(Mutex::new(Vec::new()));
        let (hello, bridge) = NativeMessagingBridge::accept(
            reader,
            CaptureWriter(Arc::clone(&capture)),
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1)),
        )
        .expect("accept");
        bridge
            .complete_handshake(
                &hello,
                BrokerEpoch::new(8),
                agentyc_core::ConnectionEpoch::new(2),
                &[Capability::Action],
            )
            .expect("epoch resync handshake");
        assert_eq!(
            captured_frames(&capture)[0]["resume"],
            json!("resync_required")
        );
        assert!(
            bridge
                .last_accepted_cursor()
                .expect("cursor state")
                .is_none()
        );
        drop(to_host);

        let (to_host, from_extension) = mpsc::sync_channel(1);
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": "nonce_resume_status_retention",
                "sequence": 1,
                "worker_instance_epoch": 2,
                "browser_session_epoch": 3,
                "profile_instance_id": "profile_resume_status_retention",
                "extension_version": "0.1.0",
                "capabilities": [],
                "resume_from": {"broker_epoch": 8, "sequence": 11}
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let capture = Arc::new(Mutex::new(Vec::new()));
        let (hello, bridge) = NativeMessagingBridge::accept(
            reader,
            CaptureWriter(Arc::clone(&capture)),
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1)),
        )
        .expect("accept");
        bridge
            .complete_handshake_with_resume(
                &hello,
                BrokerEpoch::new(8),
                agentyc_core::ConnectionEpoch::new(3),
                &[Capability::Action],
                ResumeResult::ResyncRequired,
            )
            .expect("retention resync handshake");
        assert_eq!(
            captured_frames(&capture)[0]["resume"],
            json!("resync_required")
        );
        assert!(
            bridge
                .last_accepted_cursor()
                .expect("cursor state")
                .is_none()
        );
        drop(to_host);
    }

    #[test]
    fn native_broker_cursor_is_not_the_native_envelope_sequence() {
        let (to_host, from_extension) = mpsc::sync_channel(1);
        to_host
            .send(frame_json(json!({
                "protocol": PROTOCOL_VERSION,
                "kind": "hello",
                "nonce": "nonce_cursor_separation",
                "sequence": 1,
                "worker_instance_epoch": 2,
                "browser_session_epoch": 3,
                "profile_instance_id": "profile_cursor_separation",
                "extension_version": "0.1.0",
                "capabilities": []
            })))
            .expect("hello input");
        let reader = ChannelReader {
            receiver: from_extension,
            buffer: VecDeque::new(),
        };
        let capture = Arc::new(Mutex::new(Vec::new()));
        let (hello, bridge) = NativeMessagingBridge::accept(
            reader,
            CaptureWriter(Arc::clone(&capture)),
            NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
                .expect("origin")
                .with_handshake_timeout(Duration::from_secs(1)),
        )
        .expect("accept");
        bridge
            .complete_handshake(
                &hello,
                BrokerEpoch::new(8),
                agentyc_core::ConnectionEpoch::new(4),
                &[Capability::Action],
            )
            .expect("handshake");
        bridge
            .send_event_with_cursor(
                "host.cursor_test",
                json!({"ok": true}),
                ResumeWatermark {
                    broker_epoch: BrokerEpoch::new(8),
                    sequence: agentyc_core::EventSequence::new(41),
                },
            )
            .expect("cursor event");
        let event = captured_frames(&capture)
            .into_iter()
            .find(|frame| frame["kind"] == "event")
            .expect("event frame");
        assert_eq!(event["sequence"], json!(2));
        assert_eq!(event["cursor"], json!({"broker_epoch": 8, "sequence": 41}));
        assert_ne!(event["sequence"], event["cursor"]["sequence"]);
        drop(to_host);
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
            resume_from: None,
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
        assert!(validate_json_budget(&value, 0, None).is_err());
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

    fn run_fence_ack(result: Value, nonce_suffix: &str) -> bool {
        let (to_host, from_extension) = mpsc::sync_channel(8);
        let capture = Arc::new(Mutex::new(Vec::new()));
        let hello = NativeHello {
            protocol: PROTOCOL_VERSION,
            nonce: format!("nonce_{nonce_suffix}"),
            sequence: 1,
            worker_instance_epoch: 2,
            browser_session_epoch: 3,
            profile_instance_id: format!("profile_{nonce_suffix}"),
            extension_version: "0.1.0".to_owned(),
            capabilities: vec!["logical_tabs".to_owned()],
            resume_from: None,
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
        let config = NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
            .expect("origin")
            .with_handshake_timeout(Duration::from_secs(1))
            .with_request_timeout(Duration::from_secs(1));
        let (accepted, bridge) =
            NativeMessagingBridge::accept(reader, writer, config).expect("accept");
        bridge
            .complete_handshake(
                &accepted,
                BrokerEpoch::new(1),
                agentyc_core::ConnectionEpoch::new(1),
                &[Capability::Action],
            )
            .expect("hello_ok");

        let space_id = SpaceId::from_suffix("fence_validation").expect("space");
        let request_token = ReconcileToken::from_suffix("fence-validation").expect("token");
        let fence_bridge = bridge.clone();
        let fence_space = space_id.clone();
        let fence_token = request_token.clone();
        let fence_thread = thread::spawn(move || {
            fence_bridge.fence_request(
                &fence_space,
                Some(LeaseEpoch::new(1)),
                LeaseEpoch::new(2),
                BrokerEpoch::new(1),
                &fence_token,
            )
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        let request_id = loop {
            if let Some(request) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "fence")
            {
                break request["request_id"]
                    .as_str()
                    .expect("fence request id")
                    .to_owned();
            }
            assert!(Instant::now() < deadline, "fence request was not written");
            thread::sleep(Duration::from_millis(2));
        };
        to_host
            .send(frame_json(extension_message(
                &hello,
                2,
                "fence_ack",
                json!({
                    "request_id": request_id,
                    "ok": true,
                    "result": result
                }),
            )))
            .expect("fence input");
        let acknowledged = fence_thread
            .join()
            .expect("fence thread")
            .expect("fence result")
            .acknowledged;
        drop(to_host);
        let _ = bridge.wait_closed();
        acknowledged
    }

    #[test]
    fn fence_ack_with_mismatched_token_stays_unacknowledged() {
        assert!(!run_fence_ack(
            json!({
                "request_token": "reconcile_other-fence",
                "space_id": "space_fence_validation",
                "fence_epoch": 2,
                "broker_epoch": 1,
                "connection_epoch": 1,
                "durable": true
            }),
            "mismatched-token"
        ));
    }

    #[test]
    fn fence_ack_with_durable_false_stays_unacknowledged() {
        assert!(!run_fence_ack(
            json!({
                "request_token": "reconcile_fence-validation",
                "space_id": "space_fence_validation",
                "fence_epoch": 2,
                "broker_epoch": 1,
                "connection_epoch": 1,
                "durable": false
            }),
            "durable-false"
        ));
    }

    #[test]
    fn fence_ack_with_epoch_mismatch_stays_unacknowledged() {
        assert!(!run_fence_ack(
            json!({
                "request_token": "reconcile_fence-validation",
                "space_id": "space_fence_validation",
                "fence_epoch": 2,
                "broker_epoch": 2,
                "connection_epoch": 1,
                "durable": true
            }),
            "epoch-mismatch"
        ));
    }

    #[test]
    fn current_durable_fence_ack_is_acknowledged() {
        assert!(run_fence_ack(
            json!({
                "request_token": "reconcile_fence-validation",
                "space_id": "space_fence_validation",
                "fence_epoch": 2,
                "broker_epoch": 1,
                "connection_epoch": 1,
                "durable": true
            }),
            "current-success"
        ));
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
            resume_from: None,
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
        let config = NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
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
            resume_from: None,
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
        let config = NativeMessagingConfig::new(ALLOWED_EXTENSION_ORIGINS[0])
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

        let fence_token = ReconcileToken::from_suffix("native-fence").expect("fence token");
        let fence_bridge = bridge.clone();
        let fence_token_for_thread = fence_token.clone();
        let fence_thread = thread::spawn(move || {
            fence_bridge.fence_request(
                &SpaceId::from_suffix("one").expect("space"),
                Some(LeaseEpoch::new(1)),
                LeaseEpoch::new(2),
                BrokerEpoch::new(1),
                &fence_token_for_thread,
            )
        });
        let fence_request_id = loop {
            if let Some(request) = captured_frames(&capture)
                .into_iter()
                .find(|value| value["kind"] == "fence")
            {
                assert_eq!(request["request_token"], json!(fence_token.as_str()));
                assert_eq!(request["space_id"], json!("space_one"));
                assert_eq!(request["fence_epoch"], json!(2));
                assert_eq!(request["broker_epoch"], json!(1));
                assert_eq!(request["connection_epoch"], json!(1));
                assert_eq!(request["durable"], json!(true));
                assert_eq!(
                    request["params"]["request_token"],
                    json!(fence_token.as_str())
                );
                assert_eq!(request["params"]["connection_epoch"], json!(1));
                assert_eq!(request["params"]["durable"], json!(true));
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
                    "result": {
                        "request_token": fence_token.as_str(),
                        "space_id": "space_one",
                        "fence_epoch": 2,
                        "broker_epoch": 1,
                        "connection_epoch": 1,
                        "durable": true
                    }
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
