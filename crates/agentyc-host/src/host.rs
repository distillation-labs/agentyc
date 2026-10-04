//! Host lifecycle and endpoint metadata owned by the broker process.

use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};

use crate::{HostError, LedgerError};

pub use crate::broker::{Broker as Host, Connection, HostDegradedReason, HostLifecycle};

/// Version of the broker endpoint metadata file.
pub const ENDPOINT_METADATA_SCHEMA_VERSION: u16 = 1;
/// File published while one host owns a profile-scoped broker lock.
pub const ENDPOINT_METADATA_FILENAME: &str = "broker.endpoint.json";
/// Unix socket used by Native Messaging shims to forward to the broker owner.
pub const NATIVE_FORWARD_SOCKET_FILENAME: &str = "native-forward.sock";

const MAX_ENDPOINT_BYTES: usize = 16 * 1024;
static ENDPOINT_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(1);

/// Durable discovery metadata for one running broker instance.
///
/// The file contains endpoint locations and the broker epoch only. It contains
/// no profile secrets, cookies, browser handles, or client-supplied identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EndpointMetadata {
    /// Endpoint metadata schema version.
    pub schema_version: u16,
    /// Broker epoch held by the owner process.
    pub broker_epoch: u64,
    /// Owner process identifier, used only for diagnostics and stale cleanup.
    pub owner_pid: u32,
    /// Absolute or installation-scoped local IPC path.
    pub local_socket: String,
    /// Absolute or installation-scoped Native Messaging forwarding path.
    pub native_forward_socket: String,
    /// Unix millisecond timestamp at publication.
    pub published_at_ms: u64,
    /// Selected topology; currently the single-owner shim-forwarding design.
    pub topology: String,
}

impl EndpointMetadata {
    /// Construct metadata for a broker that already owns its ledger lock.
    pub fn new(
        broker_epoch: u64,
        owner_pid: u32,
        local_socket: impl Into<String>,
        native_forward_socket: impl Into<String>,
    ) -> Self {
        Self {
            schema_version: ENDPOINT_METADATA_SCHEMA_VERSION,
            broker_epoch,
            owner_pid,
            local_socket: local_socket.into(),
            native_forward_socket: native_forward_socket.into(),
            published_at_ms: current_millis(),
            topology: "single_broker_native_shim_forwarding".to_owned(),
        }
    }

    fn validate(&self) -> Result<(), HostError> {
        if self.schema_version != ENDPOINT_METADATA_SCHEMA_VERSION
            || self.broker_epoch == 0
            || self.owner_pid == 0
            || self.local_socket.is_empty()
            || self.native_forward_socket.is_empty()
            || self.topology != "single_broker_native_shim_forwarding"
            || self.local_socket.len() > 4096
            || self.native_forward_socket.len() > 4096
        {
            return Err(HostError::Invariant(
                "broker endpoint metadata is invalid".to_owned(),
            ));
        }
        Ok(())
    }
}

/// Return the endpoint metadata path inside a profile-scoped state directory.
pub fn endpoint_metadata_path(state_dir: impl AsRef<Path>) -> PathBuf {
    state_dir.as_ref().join(ENDPOINT_METADATA_FILENAME)
}

/// Return the Native Messaging forwarding socket path inside a state directory.
pub fn native_forward_socket_path(state_dir: impl AsRef<Path>) -> PathBuf {
    state_dir.as_ref().join(NATIVE_FORWARD_SOCKET_FILENAME)
}

/// Atomically publish owner-readable broker endpoint metadata.
pub fn publish_endpoint_metadata(
    state_dir: impl AsRef<Path>,
    metadata: &EndpointMetadata,
) -> Result<PathBuf, HostError> {
    metadata.validate()?;
    let state_dir = state_dir.as_ref();
    ensure_private_directory(state_dir)?;
    let path = endpoint_metadata_path(state_dir);
    reject_symlink(&path)?;
    let bytes = serde_json::to_vec_pretty(metadata).map_err(HostError::Json)?;
    if bytes.len() > MAX_ENDPOINT_BYTES {
        return Err(HostError::Invariant(
            "broker endpoint metadata exceeds its bound".to_owned(),
        ));
    }
    let sequence = ENDPOINT_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = state_dir.join(format!("{ENDPOINT_METADATA_FILENAME}.tmp-{sequence}"));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(HostError::Transport)?;
        set_private_file(&file)?;
        file.write_all(&bytes).map_err(HostError::Transport)?;
        file.sync_all().map_err(HostError::Transport)?;
        reject_symlink(&path)?;
        fs::rename(&temporary, &path).map_err(HostError::Transport)?;
        File::open(state_dir)
            .map_err(HostError::Transport)?
            .sync_all()
            .map_err(HostError::Transport)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map(|()| path)
}

/// Read and validate endpoint metadata without following a metadata symlink.
pub fn read_endpoint_metadata(state_dir: impl AsRef<Path>) -> Result<EndpointMetadata, HostError> {
    let path = endpoint_metadata_path(state_dir);
    reject_symlink(&path)?;
    let metadata = fs::metadata(&path).map_err(HostError::Transport)?;
    if !metadata.is_file() {
        return Err(HostError::Invariant(
            "broker endpoint metadata is not a regular file".to_owned(),
        ));
    }
    ensure_private_file_metadata(&metadata)?;
    let bytes = fs::read(&path).map_err(HostError::Transport)?;
    if bytes.len() > MAX_ENDPOINT_BYTES {
        return Err(HostError::Invariant(
            "broker endpoint metadata exceeds its bound".to_owned(),
        ));
    }
    let metadata: EndpointMetadata = serde_json::from_slice(&bytes).map_err(HostError::Json)?;
    metadata.validate()?;
    Ok(metadata)
}

/// Remove metadata only when it still belongs to the current broker owner.
pub fn remove_endpoint_metadata_if_owner(
    state_dir: impl AsRef<Path>,
    broker_epoch: u64,
    owner_pid: u32,
) -> Result<(), HostError> {
    let path = endpoint_metadata_path(state_dir);
    if fs::symlink_metadata(&path).is_err() {
        return Ok(());
    }
    let metadata = read_endpoint_metadata(path.parent().unwrap_or_else(|| Path::new(".")))?;
    if metadata.broker_epoch == broker_epoch && metadata.owner_pid == owner_pid {
        fs::remove_file(path).map_err(HostError::Transport)?;
    }
    Ok(())
}

fn ensure_private_directory(path: &Path) -> Result<(), HostError> {
    fs::create_dir_all(path).map_err(HostError::Transport)?;
    reject_symlink(path)?;
    let metadata = fs::metadata(path).map_err(HostError::Transport)?;
    if !metadata.is_dir() {
        return Err(HostError::Invariant(
            "broker state path is not a directory".to_owned(),
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(HostError::Transport)?;
    }
    Ok(())
}

fn ensure_private_file_metadata(metadata: &fs::Metadata) -> Result<(), HostError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(HostError::Invariant(
                "broker endpoint metadata permissions are not private".to_owned(),
            ));
        }
    }
    Ok(())
}

fn set_private_file(file: &File) -> Result<(), HostError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(HostError::Transport)?;
    }
    Ok(())
}

fn reject_symlink(path: &Path) -> Result<(), HostError> {
    if fs::symlink_metadata(path)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(HostError::Ledger(LedgerError::Ownership(
            "broker endpoint path is a symlink".to_owned(),
        )));
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn peer_matches_directory_owner(
    stream: &std::os::unix::net::UnixStream,
    owner_path: &Path,
) -> bool {
    use std::os::unix::fs::MetadataExt;

    let Ok(owner_uid) = fs::metadata(owner_path).map(|metadata| metadata.uid()) else {
        return false;
    };
    let Ok(probe) = stream.try_clone() else {
        return false;
    };
    if probe.set_nonblocking(true).is_err() {
        return false;
    }
    let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .build()
    else {
        return false;
    };
    let _entered = runtime.enter();
    let Ok(peer) = tokio::net::UnixStream::from_std(probe) else {
        return false;
    };
    peer.peer_cred()
        .map_or(u32::MAX, |credentials| credentials.uid())
        == owner_uid
}

fn current_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| {
            duration.as_millis().min(u128::from(u64::MAX)) as u64
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn endpoint_metadata_round_trips_atomically_and_is_private() {
        let directory = tempdir().expect("state directory");
        let metadata = EndpointMetadata::new(
            3,
            42,
            directory.path().join("host.sock").display().to_string(),
            directory.path().join("native.sock").display().to_string(),
        );
        let path = publish_endpoint_metadata(directory.path(), &metadata).expect("publish");
        assert_eq!(
            read_endpoint_metadata(directory.path()).expect("read"),
            metadata
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(path).expect("metadata").permissions().mode() & 0o777,
                0o600
            );
        }
    }

    #[test]
    fn endpoint_metadata_replacement_and_owner_cleanup_are_fenced() {
        let directory = tempdir().expect("state directory");
        let first = EndpointMetadata::new(1, 10, "host-a", "native-a");
        let second = EndpointMetadata::new(2, 11, "host-b", "native-b");
        publish_endpoint_metadata(directory.path(), &first).expect("first");
        publish_endpoint_metadata(directory.path(), &second).expect("second");
        remove_endpoint_metadata_if_owner(directory.path(), 1, 10).expect("stale cleanup");
        assert_eq!(
            read_endpoint_metadata(directory.path()).expect("current"),
            second
        );
        remove_endpoint_metadata_if_owner(directory.path(), 2, 11).expect("owner cleanup");
        assert!(!endpoint_metadata_path(directory.path()).exists());
    }
}
