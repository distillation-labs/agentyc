//! Owner-only local IPC server for agent and MCP connections.
//!
//! Local agent traffic uses the core protocol's four-byte big-endian framing.
//! This transport is deliberately separate from Chrome Native Messaging, which
//! uses four-byte little-endian framing and remains owned by `native_messaging`.
//!
//! The private state directory and `0600` socket provide the local OS-user
//! boundary. A principal supplied in the logical hello is a routing identity,
//! not proof against another process running as the same OS user.

use std::{
    collections::{BTreeMap, VecDeque},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
    thread,
    time::Duration,
};

use agentyc_core::{
    DEFAULT_MAX_FRAME_PAYLOAD_BYTES, Envelope, FrameDecoder, HelloEnvelope, HelloOkEnvelope,
    PROTOCOL_VERSION, RequestEnvelope, RequestId, encode_frame,
};

use crate::{Broker, HostError, ProtocolServer};

/// Default socket filename inside the private host state directory.
pub const DEFAULT_LOCAL_SOCKET_FILENAME: &str = "host.sock";
const READ_BUFFER_BYTES: usize = 64 * 1024;
const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(25);
const MAX_LOCAL_CLIENTS: usize = 64;
const LOCAL_CLIENT_READ_TIMEOUT: Duration = Duration::from_secs(5 * 60);

/// A running owner-only local host server.
#[cfg(unix)]
pub struct LocalHostServer {
    socket_path: PathBuf,
    stop: Arc<AtomicBool>,
    join: Option<thread::JoinHandle<()>>,
}

#[cfg(unix)]
impl std::fmt::Debug for LocalHostServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LocalHostServer")
            .field("socket_path", &self.socket_path)
            .field("stopping", &self.stop.load(Ordering::Relaxed))
            .finish_non_exhaustive()
    }
}

#[cfg(unix)]
impl LocalHostServer {
    /// Start a local server around one already-opened broker.
    pub fn start(broker: Broker, socket_path: impl AsRef<Path>) -> Result<Self, HostError> {
        use std::os::unix::{
            fs::{FileTypeExt, PermissionsExt},
            net::UnixListener,
        };

        let socket_path = validate_socket_path(socket_path.as_ref())?;

        if let Ok(metadata) = std::fs::symlink_metadata(&socket_path) {
            if !metadata.file_type().is_socket() {
                return Err(HostError::Invariant(
                    "local host socket path is not a Unix socket".to_owned(),
                ));
            }
            if std::os::unix::net::UnixStream::connect(&socket_path).is_ok() {
                return Err(HostError::Invariant(
                    "local host socket is already active".to_owned(),
                ));
            }
            std::fs::remove_file(&socket_path).map_err(|error| {
                HostError::Invariant(format!(
                    "stale local host socket cannot be removed: {error}"
                ))
            })?;
        }

        let listener = UnixListener::bind(&socket_path).map_err(|error| {
            HostError::Invariant(format!("local host socket bind failed: {error}"))
        })?;
        listener.set_nonblocking(true).map_err(|error| {
            HostError::Invariant(format!("local host socket setup failed: {error}"))
        })?;
        std::fs::set_permissions(&socket_path, std::fs::Permissions::from_mode(0o600)).map_err(
            |error| HostError::Invariant(format!("local host socket permissions failed: {error}")),
        )?;

        let stop = Arc::new(AtomicBool::new(false));
        let clients = Arc::new(AtomicUsize::new(0));
        let stop_for_thread = Arc::clone(&stop);
        let clients_for_thread = Arc::clone(&clients);
        let join_path = socket_path.clone();
        let join = thread::Builder::new()
            .name("agentyc-local-host".to_owned())
            .spawn(move || {
                while !stop_for_thread.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            debug_local_log("local client accepted");
                            if !crate::host::peer_matches_directory_owner(&stream, &join_path) {
                                if let Some(path) = std::env::var_os("AGENTYC_DEBUG_LOG") {
                                    use std::io::Write as _;
                                    if let Ok(mut file) = std::fs::OpenOptions::new()
                                        .create(true)
                                        .append(true)
                                        .open(path)
                                    {
                                        let _ = writeln!(file, "local peer rejected");
                                    }
                                }
                                continue;
                            }
                            if stream.set_nonblocking(false).is_err()
                                || stream
                                    .set_read_timeout(Some(LOCAL_CLIENT_READ_TIMEOUT))
                                    .is_err()
                            {
                                continue;
                            }
                            let previous = clients_for_thread.fetch_add(1, Ordering::AcqRel);
                            if previous >= MAX_LOCAL_CLIENTS {
                                clients_for_thread.fetch_sub(1, Ordering::AcqRel);
                                continue;
                            }
                            let broker = broker.clone();
                            let clients = Arc::clone(&clients_for_thread);
                            if thread::Builder::new()
                                .name("agentyc-local-client".to_owned())
                                .spawn(move || {
                                    let _permit = ClientPermit(clients);
                                    serve_client(stream, broker);
                                })
                                .is_err()
                            {
                                clients_for_thread.fetch_sub(1, Ordering::AcqRel);
                            }
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(ACCEPT_POLL_INTERVAL);
                        }
                        Err(_) => break,
                    }
                }
                drop(listener);
                remove_socket_if_owned(&join_path);
            })
            .map_err(|error| {
                HostError::Invariant(format!("local host server thread failed: {error}"))
            })?;

        Ok(Self {
            socket_path,
            stop,
            join: Some(join),
        })
    }

    /// Return the logical filesystem endpoint used by local clients.
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    /// Stop accepting clients and remove only this server's socket.
    pub fn stop(mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

#[cfg(unix)]
impl Drop for LocalHostServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

#[cfg(not(unix))]
#[derive(Debug)]
pub struct LocalHostServer;

#[cfg(not(unix))]
impl LocalHostServer {
    /// Local IPC is not implemented on this platform yet.
    pub fn start(_broker: Broker, _socket_path: impl AsRef<Path>) -> Result<Self, HostError> {
        Err(HostError::Invariant(
            "local host IPC is unsupported on this platform".to_owned(),
        ))
    }

    /// Return the endpoint when a platform implementation exists.
    pub fn socket_path(&self) -> &Path {
        Path::new("")
    }

    /// Stop the server.
    pub fn stop(self) {}
}

/// Resolve the configured local socket without accepting a browser endpoint.
pub fn configured_socket_path(state_dir: impl AsRef<Path>) -> PathBuf {
    if let Some(configured) = std::env::var_os("AGENTYC_HOST_SOCKET")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    {
        return configured;
    }
    let configured = state_dir.as_ref().join(DEFAULT_LOCAL_SOCKET_FILENAME);
    if configured.as_os_str().len() <= 80 {
        return configured;
    }
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in configured.as_os_str().to_string_lossy().as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    let root = Path::new("/tmp");
    let root = if root.is_dir() {
        root.to_path_buf()
    } else {
        std::env::temp_dir()
    };
    root.join(format!("agentyc-host-{hash:016x}.sock"))
}

/// A persistent client for the owner-only local host socket.
///
/// The client performs one core hello per connection and serializes request/
/// response frames so multiple threads cannot interleave a frame or correlate a
/// response with the wrong logical request.
#[cfg(unix)]
pub struct LocalSocketClient {
    stream: Mutex<LocalClientStream>,
    hello_ok: HelloOkEnvelope,
    request_counter: AtomicU64,
    max_payload_bytes: usize,
}

#[cfg(unix)]
impl std::fmt::Debug for LocalSocketClient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LocalSocketClient")
            .field("broker_epoch", &self.hello_ok.broker_epoch)
            .field("connection_epoch", &self.hello_ok.connection_epoch)
            .field("max_payload_bytes", &self.max_payload_bytes)
            .finish_non_exhaustive()
    }
}

#[cfg(unix)]
impl LocalSocketClient {
    /// Connect to a running native host and complete the logical core hello.
    pub fn connect(socket_path: impl AsRef<Path>, hello: HelloEnvelope) -> Result<Self, HostError> {
        let stream =
            std::os::unix::net::UnixStream::connect(socket_path).map_err(HostError::Transport)?;
        stream
            .set_read_timeout(Some(Duration::from_secs(30)))
            .map_err(HostError::Transport)?;
        stream
            .set_write_timeout(Some(Duration::from_secs(30)))
            .map_err(HostError::Transport)?;
        let max_payload_bytes = DEFAULT_MAX_FRAME_PAYLOAD_BYTES;
        let mut io = LocalClientStream::new(stream, max_payload_bytes);
        let hello_envelope = Envelope::<BTreeMap<String, String>>::Hello(hello.clone());
        crate::protocol::validate_typed_envelope(&hello_envelope)?;
        let hello_frame = encode_frame(&serde_json::to_vec(&hello_envelope)?, max_payload_bytes)?;
        io.stream
            .write_all(&hello_frame)
            .map_err(HostError::Transport)?;
        io.stream.flush().map_err(HostError::Transport)?;
        let response = read_local_frame(&mut io)?;
        let value: serde_json::Value = serde_json::from_slice(&response)?;
        crate::protocol::validate_wire_envelope(&value)?;
        let envelope: Envelope<BTreeMap<String, String>> = serde_json::from_value(value)?;
        let Envelope::HelloOk(hello_ok) = envelope else {
            return Err(HostError::Core(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::ProtocolMismatch,
                "local host did not return hello_ok",
            )));
        };
        hello_ok.validate_against(&hello)?;
        Ok(Self {
            stream: Mutex::new(io),
            hello_ok,
            request_counter: AtomicU64::new(1),
            max_payload_bytes,
        })
    }

    /// Return the host handshake metadata for diagnostics and client identity.
    pub fn hello_ok(&self) -> &HelloOkEnvelope {
        &self.hello_ok
    }

    /// Send one bounded logical request and return its typed string map.
    pub fn request(
        &self,
        method: impl Into<String>,
        params: BTreeMap<String, String>,
    ) -> Result<BTreeMap<String, String>, HostError> {
        let request_id = RequestId::from_suffix(format!(
            "local-{}",
            self.request_counter.fetch_add(1, Ordering::Relaxed)
        ))
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
        let request = Envelope::Request(RequestEnvelope {
            protocol: PROTOCOL_VERSION,
            request_id: request_id.clone(),
            method: method.into(),
            params,
            deadline_ms: None,
            idempotency_key: None,
        });
        crate::protocol::validate_typed_envelope(&request)?;
        let frame = encode_frame(&serde_json::to_vec(&request)?, self.max_payload_bytes)?;
        let mut io = self.stream.lock().map_err(|_| HostError::StatePoisoned)?;
        io.stream.write_all(&frame).map_err(HostError::Transport)?;
        io.stream.flush().map_err(HostError::Transport)?;
        let response = read_local_frame(&mut io)?;
        let value: serde_json::Value = serde_json::from_slice(&response)?;
        crate::protocol::validate_wire_envelope(&value)?;
        let value = crate::protocol::legacy_wire_value(value)?;
        let Envelope::Response(response): Envelope = serde_json::from_value(value)? else {
            return Err(HostError::Core(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::ProtocolMismatch,
                "local host returned a non-response envelope",
            )));
        };
        if response.protocol != PROTOCOL_VERSION || response.request_id != request_id {
            return Err(HostError::Core(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::ProtocolMismatch,
                "local host response correlation is invalid",
            )));
        }
        if response.ok {
            Ok(response.result.unwrap_or_default())
        } else {
            Err(HostError::Core(response.error.unwrap_or_else(|| {
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::InvalidJson,
                    "local host returned an empty error",
                )
            })))
        }
    }
}

#[cfg(not(unix))]
#[derive(Debug)]
pub struct LocalSocketClient;

#[cfg(not(unix))]
impl LocalSocketClient {
    /// Local IPC is currently implemented only for Unix owner sockets.
    pub fn connect(
        _socket_path: impl AsRef<Path>,
        _hello: HelloEnvelope,
    ) -> Result<Self, HostError> {
        Err(HostError::Invariant(
            "local host IPC is unsupported on this platform".to_owned(),
        ))
    }

    /// No handshake exists on unsupported platforms.
    pub fn hello_ok(&self) -> &HelloOkEnvelope {
        unreachable!("unsupported local IPC platform")
    }

    /// No request transport exists on unsupported platforms.
    pub fn request(
        &self,
        _method: impl Into<String>,
        _params: BTreeMap<String, String>,
    ) -> Result<BTreeMap<String, String>, HostError> {
        Err(HostError::Invariant(
            "local host IPC is unsupported on this platform".to_owned(),
        ))
    }
}

#[cfg(unix)]
struct LocalClientStream {
    stream: std::os::unix::net::UnixStream,
    decoder: FrameDecoder,
    queued: VecDeque<Vec<u8>>,
}

#[cfg(unix)]
impl LocalClientStream {
    fn new(stream: std::os::unix::net::UnixStream, max_payload_bytes: usize) -> Self {
        Self {
            stream,
            decoder: FrameDecoder::new(max_payload_bytes),
            queued: VecDeque::new(),
        }
    }
}

#[cfg(unix)]
fn read_local_frame(client_io: &mut LocalClientStream) -> Result<Vec<u8>, HostError> {
    if let Some(frame) = client_io.queued.pop_front() {
        return Ok(frame);
    }
    let mut buffer = [0_u8; READ_BUFFER_BYTES];
    loop {
        let count = match client_io.stream.read(&mut buffer) {
            Ok(count) => count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(HostError::Transport(error)),
        };
        if count == 0 {
            return match client_io.decoder.finish() {
                Ok(()) => Err(HostError::Transport(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "local host closed cleanly before the next response",
                ))),
                Err(error) => Err(HostError::Frame(error)),
            };
        }
        let frames = client_io.decoder.feed(&buffer[..count])?;
        if let Some((first, rest)) = frames.split_first() {
            client_io.queued.extend(rest.iter().cloned());
            return Ok(first.clone());
        }
    }
}

#[cfg(unix)]
fn validate_socket_path(path: &Path) -> Result<PathBuf, HostError> {
    if path.as_os_str().is_empty() || path.is_absolute() && path.parent().is_none() {
        return Err(HostError::Invariant(
            "local host socket path is invalid".to_owned(),
        ));
    }
    let file_name = path
        .file_name()
        .ok_or_else(|| HostError::Invariant("local host socket path is invalid".to_owned()))?;
    let parent = path.parent().ok_or_else(|| {
        HostError::Invariant("local host socket must have a parent directory".to_owned())
    })?;
    if !parent.is_dir() {
        return Err(HostError::Invariant(
            "local host socket parent must be a real directory".to_owned(),
        ));
    }
    let mut current = parent;
    loop {
        if std::fs::symlink_metadata(current)
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
            && !is_allowed_system_path_alias(current)
        {
            return Err(HostError::Invariant(
                "local host socket path contains a symlink".to_owned(),
            ));
        }
        let Some(next) = current.parent() else { break };
        if next == current {
            break;
        }
        current = next;
    }
    let canonical_parent = parent.canonicalize().map_err(|_| {
        HostError::Invariant("local host socket parent must be a real directory".to_owned())
    })?;
    Ok(canonical_parent.join(file_name))
}

#[cfg(target_os = "macos")]
fn is_allowed_system_path_alias(path: &Path) -> bool {
    let expected_target = match path {
        path if path == Path::new("/var") => Path::new("/private/var"),
        path if path == Path::new("/tmp") => Path::new("/private/tmp"),
        path if path == Path::new("/etc") => Path::new("/private/etc"),
        _ => return false,
    };
    path.canonicalize()
        .is_ok_and(|target| target == expected_target)
}

#[cfg(not(target_os = "macos"))]
fn is_allowed_system_path_alias(_path: &Path) -> bool {
    false
}

#[cfg(unix)]
struct ClientPermit(Arc<AtomicUsize>);

#[cfg(unix)]
impl Drop for ClientPermit {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

#[cfg(unix)]
fn serve_client(mut stream: std::os::unix::net::UnixStream, broker: Broker) {
    let mut decoder = FrameDecoder::new(DEFAULT_MAX_FRAME_PAYLOAD_BYTES);
    let mut server = ProtocolServer::new(broker);
    let mut buffer = vec![0_u8; READ_BUFFER_BYTES];

    loop {
        let count = match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        };
        let payloads = match decoder.feed(&buffer[..count]) {
            Ok(payloads) => payloads,
            Err(_) => break,
        };
        for payload in payloads {
            let output = match server.handle_payload(&payload) {
                Ok(output) => output,
                Err(error) => {
                    eprintln!("agentyc local protocol error: {error}");
                    if let Some(path) = std::env::var_os("AGENTYC_DEBUG_LOG") {
                        use std::io::Write as _;
                        if let Ok(mut file) = std::fs::OpenOptions::new()
                            .create(true)
                            .append(true)
                            .open(path)
                        {
                            let _ = writeln!(file, "local protocol error: {error}");
                        }
                    }
                    let _ = server.close();
                    return;
                }
            };
            if stream.write_all(&output).is_err() || stream.flush().is_err() {
                let _ = server.close();
                return;
            }
        }
    }
    if decoder.finish().is_err() {
        let _ = server.close();
        return;
    }
    let _ = server.close();
}

#[cfg(unix)]
fn debug_local_log(message: &str) {
    if let Some(path) = std::env::var_os("AGENTYC_DEBUG_LOG") {
        use std::io::Write as _;
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            let _ = writeln!(file, "{message}");
        }
    }
}

#[cfg(unix)]
fn remove_socket_if_owned(path: &Path) {
    use std::os::unix::fs::FileTypeExt;
    if let Ok(metadata) = std::fs::symlink_metadata(path)
        && metadata.file_type().is_socket()
    {
        let _ = std::fs::remove_file(path);
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::FakeBridge;
    use agentyc_core::{
        ClientMetadata, ConnectionNonce, Envelope, HelloEnvelope, PROTOCOL_VERSION, PrincipalId,
        RequestEnvelope, RequestId,
    };
    use std::{
        collections::BTreeMap,
        io::{Read, Write},
        os::unix::net::UnixStream,
        time::Duration,
    };
    use tempfile::tempdir;

    fn hello(suffix: &str) -> Envelope {
        Envelope::Hello(HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(suffix).expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("local-ipc-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(ConnectionNonce::from_suffix(suffix).expect("nonce")),
                profile_binding_id: None,
            }),
        })
    }

    fn read_frame(stream: &mut UnixStream) -> Vec<u8> {
        let mut prefix = [0_u8; 4];
        stream.read_exact(&mut prefix).expect("response prefix");
        let length = u32::from_be_bytes(prefix) as usize;
        let mut payload = vec![0_u8; length];
        stream.read_exact(&mut payload).expect("response payload");
        payload
    }

    #[test]
    fn socket_path_validation_rejects_non_system_symlink_ancestors() {
        let directory = tempdir().expect("directory");
        let target = directory.path().join("target");
        let alias = directory.path().join("alias");
        std::fs::create_dir(&target).expect("target directory");
        std::os::unix::fs::symlink(&target, &alias).expect("directory symlink");

        assert!(validate_socket_path(&alias.join("host.sock")).is_err());
    }

    #[test]
    fn two_local_clients_share_one_broker_and_disconnect_independently() {
        let directory = tempdir().expect("directory");
        let socket_path = directory.path().join("host.sock");
        let broker =
            Broker::open(directory.path().join("state"), FakeBridge::new()).expect("broker");
        let server = LocalHostServer::start(broker.clone(), &socket_path).expect("server");

        let mut first = UnixStream::connect(server.socket_path()).expect("first client");
        let mut second = UnixStream::connect(server.socket_path()).expect("second client");
        first
            .write_all(
                &agentyc_core::encode_frame(
                    &serde_json::to_vec(&hello("one")).expect("hello"),
                    DEFAULT_MAX_FRAME_PAYLOAD_BYTES,
                )
                .expect("frame"),
            )
            .expect("first hello");
        second
            .write_all(
                &agentyc_core::encode_frame(
                    &serde_json::to_vec(&hello("two")).expect("hello"),
                    DEFAULT_MAX_FRAME_PAYLOAD_BYTES,
                )
                .expect("frame"),
            )
            .expect("second hello");

        first
            .set_read_timeout(Some(Duration::from_secs(2)))
            .expect("timeout");
        second
            .set_read_timeout(Some(Duration::from_secs(2)))
            .expect("timeout");
        assert!(!read_frame(&mut first).is_empty());
        assert!(!read_frame(&mut second).is_empty());

        let request_frame = |request_id: &str| {
            let request = RequestEnvelope::<BTreeMap<String, String>> {
                protocol: PROTOCOL_VERSION,
                request_id: RequestId::from_suffix(request_id).expect("request"),
                method: "space.list".to_owned(),
                params: BTreeMap::new(),
                deadline_ms: None,
                idempotency_key: None,
            };
            agentyc_core::encode_frame(
                &serde_json::to_vec(&Envelope::Request(request)).expect("request"),
                DEFAULT_MAX_FRAME_PAYLOAD_BYTES,
            )
            .expect("frame")
        };
        let frame = request_frame("status");
        first.write_all(&frame).expect("first request");
        second.write_all(&frame).expect("second request");
        assert!(!read_frame(&mut first).is_empty());
        assert!(!read_frame(&mut second).is_empty());

        drop(first);
        thread::sleep(Duration::from_millis(50));
        second
            .write_all(&request_frame("status-two"))
            .expect("second client survives first disconnect");
        assert!(!read_frame(&mut second).is_empty());
        drop(second);
        server.stop();
    }
}
