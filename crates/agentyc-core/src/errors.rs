//! Stable errors shared by every transport adapter.

use std::fmt;

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Stable machine-readable error codes for core and local protocol failures.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// The extension bridge is not connected.
    ExtensionNotConnected,
    /// The requested profile binding does not exist.
    ProfileNotFound,
    /// The operation requires a task space.
    SpaceRequired,
    /// The logical space does not exist.
    SpaceNotFound,
    /// The principal is not allowed to use the space.
    SpaceForbidden,
    /// User control currently fences agent mutations.
    UserControlRequired,
    /// The lease expiry has passed.
    LeaseExpired,
    /// The presented lease epoch is stale.
    StaleLease,
    /// The logical page does not exist.
    PageNotFound,
    /// The principal does not own the page.
    PageNotOwned,
    /// The page is not managed by the host.
    UnmanagedPage,
    /// A ref does not match current snapshot provenance.
    StaleRef,
    /// The live binding was replaced.
    TargetReplaced,
    /// The requested event watermark is no longer retained.
    EventLagged,
    /// Dispatch occurred but the outcome is not known.
    UnknownOutcome,
    /// Reconciliation is required before another mutation.
    ReconciliationRequired,
    /// The requested capability is unavailable.
    CapabilityUnavailable,
    /// Policy or user authority denied the operation.
    PermissionDenied,
    /// The Native Messaging host is unavailable.
    NativeHostUnavailable,
    /// Protocol versions do not overlap.
    ProtocolMismatch,
    /// A bounded message or artifact exceeds its limit.
    MessageTooLarge,
    /// The request violates a value or state invariant.
    InvalidArgument,
    /// The operation exceeded its deadline.
    Timeout,
    /// The operation was cancelled.
    Cancelled,
    /// The host is draining and admits no new work.
    HostDraining,
    /// Durable state cannot be used by this implementation.
    LedgerIncompatible,
    /// A frame ended before its declared payload.
    TruncatedFrame,
    /// A frame payload is not valid UTF-8.
    InvalidUtf8,
    /// A serialized envelope is not valid JSON.
    InvalidJson,
}

impl ErrorCode {
    /// Return whether a caller may retry without changing authority or input.
    pub const fn retryable(self) -> bool {
        matches!(
            self,
            Self::ExtensionNotConnected
                | Self::NativeHostUnavailable
                | Self::EventLagged
                | Self::Timeout
                | Self::HostDraining
        )
    }

    /// Return stable client guidance for this code.
    pub const fn guidance(self) -> ErrorGuidance {
        match self {
            Self::StaleLease | Self::LeaseExpired => ErrorGuidance::RefreshLease,
            Self::StaleRef | Self::TargetReplaced | Self::EventLagged => ErrorGuidance::Resync,
            Self::UnknownOutcome | Self::ReconciliationRequired => ErrorGuidance::Reconcile,
            Self::UserControlRequired => ErrorGuidance::AwaitUserControl,
            Self::UnmanagedPage | Self::PageNotOwned => ErrorGuidance::Claim,
            code if code.retryable() => ErrorGuidance::Retry,
            _ => ErrorGuidance::None,
        }
    }

    /// Return the stable snake-case code string.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ExtensionNotConnected => "extension_not_connected",
            Self::ProfileNotFound => "profile_not_found",
            Self::SpaceRequired => "space_required",
            Self::SpaceNotFound => "space_not_found",
            Self::SpaceForbidden => "space_forbidden",
            Self::UserControlRequired => "user_control_required",
            Self::LeaseExpired => "lease_expired",
            Self::StaleLease => "stale_lease",
            Self::PageNotFound => "page_not_found",
            Self::PageNotOwned => "page_not_owned",
            Self::UnmanagedPage => "unmanaged_page",
            Self::StaleRef => "stale_ref",
            Self::TargetReplaced => "target_replaced",
            Self::EventLagged => "event_lagged",
            Self::UnknownOutcome => "unknown_outcome",
            Self::ReconciliationRequired => "reconciliation_required",
            Self::CapabilityUnavailable => "capability_unavailable",
            Self::PermissionDenied => "permission_denied",
            Self::NativeHostUnavailable => "native_host_unavailable",
            Self::ProtocolMismatch => "protocol_mismatch",
            Self::MessageTooLarge => "message_too_large",
            Self::InvalidArgument => "invalid_argument",
            Self::Timeout => "timeout",
            Self::Cancelled => "cancelled",
            Self::HostDraining => "host_draining",
            Self::LedgerIncompatible => "ledger_incompatible",
            Self::TruncatedFrame => "truncated_frame",
            Self::InvalidUtf8 => "invalid_utf8",
            Self::InvalidJson => "invalid_json",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Stable next-step guidance attached to a [`CoreError`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorGuidance {
    /// No general next step is prescribed.
    None,
    /// Retry the same request after the transient failure clears.
    Retry,
    /// Reconcile a previously dispatched action.
    Reconcile,
    /// Acquire or renew a current lease.
    RefreshLease,
    /// Request a fresh snapshot or event watermark.
    Resync,
    /// Ask the authority holder to claim or adopt the resource.
    Claim,
    /// Wait for an explicit user transition.
    AwaitUserControl,
}

/// Structured, serializable error returned by a core contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Error)]
#[error("{code}: {message}")]
pub struct CoreError {
    /// Stable machine-readable code.
    pub code: ErrorCode,
    /// Whether a retry is safe without reconciliation or authority changes.
    pub retryable: bool,
    /// Stable guidance for the next operation.
    pub guidance: ErrorGuidance,
    /// Bounded human-readable detail.
    pub message: String,
}

impl CoreError {
    /// Construct an error using the code's default retry and guidance policy.
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            retryable: code.retryable(),
            guidance: code.guidance(),
            code,
            message: message.into(),
        }
    }

    /// Construct a stable invalid-argument error.
    pub fn invalid_argument(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::InvalidArgument, message)
    }

    /// Construct a stale-lease error without exposing any browser handle.
    pub fn stale_lease(expected: u64, actual: u64) -> Self {
        Self::new(
            ErrorCode::StaleLease,
            format!("lease epoch {actual} is stale; expected {expected}"),
        )
    }

    /// Construct a stale-ref error.
    pub fn stale_ref(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::StaleRef, message)
    }

    /// Return the stable wire code string.
    pub const fn code_str(&self) -> &'static str {
        self.code.as_str()
    }
}

/// Errors raised by the bounded four-byte length-delimited frame codec.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum FrameError {
    /// The declared payload exceeds the configured bound.
    #[error("message too large: {length} bytes exceeds {max} bytes")]
    MessageTooLarge {
        /// Declared payload size.
        length: usize,
        /// Configured maximum payload size.
        max: usize,
    },
    /// A frame is shorter than its four-byte prefix or declared payload.
    #[error("truncated frame: expected {expected} bytes, got {actual} bytes")]
    Truncated {
        /// Minimum bytes needed to finish the frame.
        expected: usize,
        /// Bytes available to the decoder.
        actual: usize,
    },
    /// A complete frame contains bytes after its declared payload.
    #[error("frame contains trailing bytes")]
    TrailingBytes,
    /// A decoder was used after a fatal frame error.
    #[error("frame decoder is poisoned after a previous error")]
    DecoderPoisoned,
    /// A configured bound cannot be represented by the four-byte prefix.
    #[error("frame limit exceeds the four-byte length prefix")]
    InvalidLimit,
}

impl FrameError {
    /// Map this codec error to its stable protocol code.
    pub const fn code(&self) -> ErrorCode {
        match self {
            Self::MessageTooLarge { .. } | Self::InvalidLimit => ErrorCode::MessageTooLarge,
            Self::Truncated { .. } => ErrorCode::TruncatedFrame,
            Self::TrailingBytes | Self::DecoderPoisoned => ErrorCode::InvalidArgument,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_error_codes_keep_stable_wire_names() {
        assert_eq!(ErrorCode::StaleLease.as_str(), "stale_lease");
        assert_eq!(ErrorCode::UnknownOutcome.as_str(), "unknown_outcome");
        assert_eq!(ErrorCode::MessageTooLarge.as_str(), "message_too_large");
        assert_eq!(ErrorCode::StaleRef.guidance(), ErrorGuidance::Resync);
    }
}
