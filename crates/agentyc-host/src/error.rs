//! Host-owned error types and stable mappings to the core protocol.

use std::io;

use agentyc_core::{ActionTransitionError, CoreError, ErrorCode, FrameError};
use thiserror::Error;

/// Errors raised by the host broker.
#[derive(Debug, Error)]
pub enum HostError {
    /// A durable ledger operation failed.
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    /// A core contract rejected a request.
    #[error(transparent)]
    Core(#[from] CoreError),
    /// A durable core action receipt transition was invalid.
    #[error(transparent)]
    Action(#[from] ActionTransitionError),
    /// A bridge operation failed before a definitive browser-side outcome.
    #[error("bridge error: {0}")]
    Bridge(CoreError),
    /// JSON encoding or decoding failed at the local protocol boundary.
    #[error("invalid json: {0}")]
    Json(#[from] serde_json::Error),
    /// Length-delimited framing failed at the local protocol boundary.
    #[error(transparent)]
    Frame(#[from] FrameError),
    /// The broker mutex was poisoned by a prior panic.
    #[error("broker state lock is poisoned")]
    StatePoisoned,
    /// A bounded host operation could not complete without violating an invariant.
    #[error("host invariant failed: {0}")]
    Invariant(String),
}

impl HostError {
    /// Convert a host failure into the stable error carried by a response envelope.
    pub fn as_core_error(&self) -> CoreError {
        match self {
            Self::Core(error) | Self::Bridge(error) => error.clone(),
            Self::Action(error) => CoreError::new(ErrorCode::InvalidArgument, error.to_string()),
            Self::Ledger(error) => error.as_core_error(),
            Self::Json(error) => CoreError::new(ErrorCode::InvalidJson, error.to_string()),
            Self::Frame(error) => CoreError::new(error.code(), error.to_string()),
            Self::StatePoisoned => CoreError::new(
                ErrorCode::LedgerIncompatible,
                "broker state lock is poisoned",
            ),
            Self::Invariant(message) => CoreError::invalid_argument(message.clone()),
        }
    }
}

/// Failures raised while opening, validating, or atomically persisting the ledger.
#[derive(Debug, Error)]
pub enum LedgerError {
    /// The lock is already held by another broker instance.
    #[error("host ledger is already owned")]
    AlreadyOwned,
    /// The lock or ledger path was replaced or is not a regular file/directory.
    #[error("ledger ownership or path check failed: {0}")]
    Ownership(String),
    /// The ledger JSON is malformed or cannot be trusted.
    #[error("ledger is corrupt: {0}")]
    Corrupt(String),
    /// The ledger schema is not understood by this host.
    #[error("ledger schema is incompatible: {0}")]
    Incompatible(String),
    /// A configured or persisted bound would be exceeded.
    #[error("ledger bound exceeded: {0}")]
    BoundExceeded(String),
    /// A filesystem operation failed.
    #[error("ledger io error: {0}")]
    Io(#[source] io::Error),
    /// Serialization failed before an atomic replacement could be made.
    #[error("ledger serialization failed: {0}")]
    Serialization(#[source] serde_json::Error),
}

impl LedgerError {
    /// Map a ledger failure to the stable core error namespace.
    pub fn as_core_error(&self) -> CoreError {
        match self {
            Self::AlreadyOwned | Self::Ownership(_) => {
                CoreError::new(ErrorCode::PermissionDenied, self.to_string())
            }
            Self::Corrupt(_) | Self::Incompatible(_) | Self::Serialization(_) => {
                CoreError::new(ErrorCode::LedgerIncompatible, self.to_string())
            }
            Self::BoundExceeded(_) => CoreError::new(ErrorCode::MessageTooLarge, self.to_string()),
            Self::Io(_) => CoreError::new(ErrorCode::LedgerIncompatible, self.to_string()),
        }
    }
}

impl From<io::Error> for LedgerError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
