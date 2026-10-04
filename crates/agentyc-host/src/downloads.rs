//! Scoped download admission and bounded state.
//!
//! Download records contain only logical host identities and a sanitized
//! filename. Browser download IDs, paths, headers, and response bodies never
//! cross this module's public boundary.

use std::collections::BTreeMap;

use agentyc_core::{ArtifactId, CoreError, ErrorCode, Timestamp, UserIntentTicket};
use serde::{Deserialize, Serialize};

use crate::{
    HostError,
    observability::{ObservationScope, redact_text},
    trace_policy::{IntentBinding, IntentTicketStore},
};

/// State of one logical download.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum DownloadStatus {
    /// The host admitted the download and awaits a terminal observation.
    Pending,
    /// The download completed with a bounded byte count.
    Completed { bytes: u64 },
    /// The browser/extension reported a stable failure code.
    Failed { code: ErrorCode },
    /// The user or host cancelled the download.
    Cancelled,
}

/// Logical download record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DownloadRecord {
    /// Host-issued logical identity, not a Chrome download ID.
    pub download_id: ArtifactId,
    /// Logical space/page scope.
    pub scope: ObservationScope,
    /// Sanitized basename only.
    pub filename: String,
    /// Bounded content type label.
    pub content_type: String,
    /// Current state.
    pub status: DownloadStatus,
}

/// Host admission request for a download.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadRequest {
    /// Complete policy binding for this operation.
    pub binding: IntentBinding,
    /// Optional matching host-issued ticket; pause state may make it unnecessary.
    pub ticket: Option<UserIntentTicket>,
    /// Browser-provided filename, reduced to a safe basename.
    pub filename: String,
    /// Bounded content type.
    pub content_type: String,
}

/// Typed download failures.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DownloadFailure {
    /// Download API is not present or was disabled.
    CapabilityUnavailable,
    /// Browser or enterprise policy denied the download.
    PermissionDenied,
    /// User cancelled the download.
    Cancelled,
    /// Dispatch crossed the browser boundary without a terminal outcome.
    UnknownOutcome,
}

impl DownloadFailure {
    fn code(self) -> ErrorCode {
        match self {
            Self::CapabilityUnavailable => ErrorCode::CapabilityUnavailable,
            Self::PermissionDenied => ErrorCode::PermissionDenied,
            Self::Cancelled => ErrorCode::Cancelled,
            Self::UnknownOutcome => ErrorCode::UnknownOutcome,
        }
    }
}

/// Bounded, scope-partitioned download state.
#[derive(Debug)]
pub struct DownloadManager {
    available: bool,
    next_id: u64,
    records: BTreeMap<ObservationScope, Vec<DownloadRecord>>,
}

impl Default for DownloadManager {
    fn default() -> Self {
        Self::new(true)
    }
}

impl DownloadManager {
    /// Construct a manager with an explicit browser capability state.
    pub fn new(available: bool) -> Self {
        Self {
            available,
            next_id: 0,
            records: BTreeMap::new(),
        }
    }

    /// Set whether the underlying download capability is available.
    pub fn set_available(&mut self, available: bool) {
        self.available = available;
    }

    /// Admit one scoped download through pause or a single-use ticket.
    pub fn start(
        &mut self,
        request: DownloadRequest,
        policy: &mut IntentTicketStore,
        now: Timestamp,
    ) -> Result<DownloadRecord, HostError> {
        if !self.available {
            return Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "download capability is unavailable",
            )
            .into());
        }
        request.binding.validate()?;
        policy.authorize(&request.binding, request.ticket.as_ref(), now)?;
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or_else(|| CoreError::invalid_argument("download identity sequence overflow"))?;
        let download_id = ArtifactId::from_suffix(format!("download-{}", self.next_id))
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
        let record = DownloadRecord {
            download_id,
            scope: request.binding.scope.clone(),
            filename: safe_filename(&request.filename),
            content_type: bounded_content_type(&request.content_type),
            status: DownloadStatus::Pending,
        };
        self.records
            .entry(record.scope.clone())
            .or_default()
            .push(record.clone());
        Ok(record)
    }

    /// Mark a pending download complete under its original scope.
    pub fn complete(
        &mut self,
        scope: &ObservationScope,
        download_id: &ArtifactId,
        bytes: u64,
    ) -> Result<DownloadRecord, HostError> {
        let record = self.find_mut(scope, download_id)?;
        if !matches!(record.status, DownloadStatus::Pending) {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "download is already terminal",
            )
            .into());
        }
        record.status = DownloadStatus::Completed { bytes };
        Ok(record.clone())
    }

    /// Record a typed browser/extension failure.
    pub fn fail(
        &mut self,
        scope: &ObservationScope,
        download_id: &ArtifactId,
        failure: DownloadFailure,
    ) -> Result<DownloadRecord, HostError> {
        let record = self.find_mut(scope, download_id)?;
        if !matches!(record.status, DownloadStatus::Pending) {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "download is already terminal",
            )
            .into());
        }
        record.status = if failure == DownloadFailure::Cancelled {
            DownloadStatus::Cancelled
        } else {
            DownloadStatus::Failed {
                code: failure.code(),
            }
        };
        Ok(record.clone())
    }

    /// List records visible to a requested space/page. No other space is read.
    pub fn list(&self, requested: &ObservationScope) -> Result<Vec<DownloadRecord>, HostError> {
        requested.validate()?;
        Ok(self
            .records
            .iter()
            .filter(|(scope, _)| {
                scope.space_id == requested.space_id
                    && requested
                        .page_id
                        .as_ref()
                        .is_none_or(|page| scope.page_id.as_ref() == Some(page))
            })
            .flat_map(|(_, records)| records.iter().cloned())
            .collect())
    }

    fn find_mut(
        &mut self,
        scope: &ObservationScope,
        download_id: &ArtifactId,
    ) -> Result<&mut DownloadRecord, HostError> {
        scope.validate()?;
        self.records
            .get_mut(scope)
            .and_then(|records| {
                records
                    .iter_mut()
                    .find(|record| &record.download_id == download_id)
            })
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::PermissionDenied,
                    "download does not belong to the requested logical scope",
                )
                .into()
            })
    }
}

fn safe_filename(value: &str) -> String {
    let basename = value.rsplit(['/', '\\']).next().unwrap_or_default();
    let sanitized = redact_text(basename)
        .chars()
        .filter(|character| !character.is_control())
        .collect::<String>();
    if sanitized.is_empty() {
        "download.bin".to_owned()
    } else {
        sanitized.chars().take(256).collect()
    }
}

fn bounded_content_type(value: &str) -> String {
    let sanitized = value
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '/' | '-' | '+' | '.' | ' ')
        })
        .take(128)
        .collect::<String>();
    if sanitized.is_empty() {
        "application/octet-stream".to_owned()
    } else {
        sanitized
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::trace_policy::{HostConfirmation, PolicyScope, SensitiveBoundary};
    use agentyc_core::{
        ConnectionEpoch, ConnectionNonce, ContentHash, Generation, LeaseEpoch, PageId,
        ProfileBindingId, SpaceId,
    };

    fn request(space: &str, page: &str) -> DownloadRequest {
        DownloadRequest {
            binding: IntentBinding {
                boundary: SensitiveBoundary::Permission,
                scope: PolicyScope::page(
                    SpaceId::from_suffix(space).expect("space"),
                    PageId::from_suffix(page).expect("page"),
                ),
                profile_binding_id: ProfileBindingId::from_suffix("profile").expect("profile"),
                document_generation: Some(Generation::new(1)),
                lease_epoch: LeaseEpoch::new(1),
                action_hash: ContentHash::from_bytes(space.as_bytes()),
                connection_epoch: ConnectionEpoch::new(1),
                connection_nonce: ConnectionNonce::from_suffix("nonce").expect("nonce"),
            },
            ticket: None,
            filename: "/private/secret.csv".to_owned(),
            content_type: "text/csv".to_owned(),
        }
    }

    #[test]
    fn downloads_are_scoped_and_secret_paths_are_not_retained() {
        let mut policy = IntentTicketStore::default();
        let mut manager = DownloadManager::default();
        let first_request = request("one", "main");
        let first_ticket = policy
            .issue(
                first_request.binding.clone(),
                HostConfirmation::SidePanel,
                Timestamp::new(1),
                10,
            )
            .expect("ticket");
        let first = manager
            .start(
                DownloadRequest {
                    ticket: Some(first_ticket),
                    ..first_request
                },
                &mut policy,
                Timestamp::new(2),
            )
            .expect("first download");
        let second_request = request("two", "main");
        let second_ticket = policy
            .issue(
                second_request.binding.clone(),
                HostConfirmation::SidePanel,
                Timestamp::new(1),
                10,
            )
            .expect("ticket");
        manager
            .start(
                DownloadRequest {
                    ticket: Some(second_ticket),
                    ..second_request
                },
                &mut policy,
                Timestamp::new(2),
            )
            .expect("second download");
        let first_scope = first.scope.clone();
        assert_eq!(manager.list(&first_scope).expect("list").len(), 1);
        assert!(
            !manager.list(&first_scope).expect("list")[0]
                .filename
                .contains("/")
        );
        assert!(matches!(
            manager.fail(
                &first_scope,
                &first.download_id,
                DownloadFailure::PermissionDenied
            ),
            Ok(DownloadRecord {
                status: DownloadStatus::Failed {
                    code: ErrorCode::PermissionDenied
                },
                ..
            })
        ));
    }

    #[test]
    fn unavailable_downloads_return_a_typed_capability_failure() {
        let mut manager = DownloadManager::new(false);
        let mut policy = IntentTicketStore::default();
        let request = request("unavailable", "main");
        assert!(matches!(
            manager.start(request, &mut policy, Timestamp::new(1)),
            Err(HostError::Core(CoreError {
                code: ErrorCode::CapabilityUnavailable,
                ..
            }))
        ));
    }
}
