//! Scoped network-mock policy and failure handling.
//!
//! Mock specifications are logical and bounded. Response bodies are accepted
//! only as transient input and are never retained or returned by this module.
//! Any underlying interception failure is surfaced as a typed capability error.

use std::collections::BTreeMap;

use agentyc_core::{CoreError, ErrorCode, Timestamp, UserIntentTicket};
use serde::{Deserialize, Serialize};

use crate::{
    HostError,
    observability::{ObservationScope, redact_headers, redact_text},
    trace_policy::{IntentBinding, IntentTicketStore},
};

/// Network mock action.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MockAction {
    /// Fulfill the request with a synthetic response.
    Fulfill,
    /// Abort the request with a policy-selected failure.
    Abort,
    /// Leave the request untouched.
    Continue,
}

/// Optional authorization for a mock that changes browser behavior.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MockAuthorization {
    /// Complete host policy binding.
    pub binding: IntentBinding,
    /// Matching one-use ticket, unless the space is paused.
    pub ticket: Option<UserIntentTicket>,
}

/// Host input for one mock.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MockRequest {
    /// Logical page scope.
    pub scope: ObservationScope,
    /// Bounded URL substring/pattern treated as opaque data.
    pub url_pattern: String,
    /// Optional HTTP method filter.
    pub method: Option<String>,
    /// Optional resource type filter.
    pub resource_type: Option<String>,
    /// Interception action.
    pub action: MockAction,
    /// Synthetic response status.
    pub status: u16,
    /// Synthetic response headers, redacted before retention.
    pub response_headers: BTreeMap<String, String>,
    /// Transient response body; never retained.
    pub response_body: Option<String>,
    /// Optional policy authorization for the mutation.
    pub authorization: Option<MockAuthorization>,
}

/// Logical retained mock record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MockRecord {
    /// Host-local logical identity.
    pub mock_id: u64,
    /// Logical page scope.
    pub scope: ObservationScope,
    /// Redacted URL pattern.
    pub url_pattern: String,
    /// Optional uppercase method filter.
    pub method: Option<String>,
    /// Optional resource type filter.
    pub resource_type: Option<String>,
    /// Interception action.
    pub action: MockAction,
    /// Synthetic response status.
    pub status: u16,
    /// Redacted response headers.
    pub response_headers: BTreeMap<String, String>,
    /// Whether a body was supplied but intentionally discarded.
    pub body_redacted: bool,
    /// Number of logical matches.
    pub match_count: u64,
}

/// Typed failures from the mock capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MockFailure {
    /// Network interception is unavailable.
    CapabilityUnavailable,
    /// Policy or browser permissions denied interception.
    PermissionDenied,
    /// The requested pattern is invalid or outside bounds.
    InvalidPattern,
    /// Dispatch crossed the browser boundary without a result.
    UnknownOutcome,
}

impl MockFailure {
    fn code(self) -> ErrorCode {
        match self {
            Self::CapabilityUnavailable => ErrorCode::CapabilityUnavailable,
            Self::PermissionDenied => ErrorCode::PermissionDenied,
            Self::InvalidPattern => ErrorCode::InvalidArgument,
            Self::UnknownOutcome => ErrorCode::UnknownOutcome,
        }
    }
}

/// Bounded logical mock registry partitioned by page scope.
#[derive(Debug)]
pub struct MockManager {
    available: bool,
    next_id: u64,
    records: BTreeMap<ObservationScope, Vec<MockRecord>>,
}

impl Default for MockManager {
    fn default() -> Self {
        Self::new(true)
    }
}

impl MockManager {
    /// Construct a manager with an explicit interception capability state.
    pub fn new(available: bool) -> Self {
        Self {
            available,
            next_id: 0,
            records: BTreeMap::new(),
        }
    }

    /// Set whether network interception is available.
    pub fn set_available(&mut self, available: bool) {
        self.available = available;
    }

    /// Add one logical mock.
    pub fn add(
        &mut self,
        request: MockRequest,
        policy: &mut IntentTicketStore,
        now: Timestamp,
    ) -> Result<MockRecord, HostError> {
        if !self.available {
            return Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "network mock capability is unavailable",
            )
            .into());
        }
        request.scope.validate()?;
        if request.url_pattern.is_empty() || request.url_pattern.len() > 2_048 {
            return Err(CoreError::new(
                ErrorCode::InvalidArgument,
                "network mock pattern is empty or too large",
            )
            .into());
        }
        if !(100..=599).contains(&request.status) {
            return Err(CoreError::invalid_argument("network mock status is invalid").into());
        }
        if let Some(authorization) = &request.authorization {
            if authorization.binding.scope != request.scope {
                return Err(CoreError::new(
                    ErrorCode::PermissionDenied,
                    "network mock authorization is not scoped to the page",
                )
                .into());
            }
            policy.authorize(&authorization.binding, authorization.ticket.as_ref(), now)?;
        }
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or_else(|| CoreError::invalid_argument("mock identity sequence overflow"))?;
        let record = MockRecord {
            mock_id: self.next_id,
            scope: request.scope.clone(),
            url_pattern: redact_text(&request.url_pattern),
            method: request
                .method
                .as_deref()
                .map(|method| redact_text(method).to_ascii_uppercase()),
            resource_type: request.resource_type.as_deref().map(redact_text),
            action: request.action,
            status: request.status,
            response_headers: redact_headers(&request.response_headers),
            body_redacted: request.response_body.is_some(),
            match_count: 0,
        };
        self.records
            .entry(record.scope.clone())
            .or_default()
            .push(record.clone());
        Ok(record)
    }

    /// Remove one mock from its exact logical scope.
    pub fn remove(&mut self, scope: &ObservationScope, mock_id: u64) -> Result<(), HostError> {
        scope.validate()?;
        let records = self.records.get_mut(scope).ok_or_else(|| {
            CoreError::new(
                ErrorCode::PermissionDenied,
                "mock is not in the logical scope",
            )
        })?;
        let before = records.len();
        records.retain(|record| record.mock_id != mock_id);
        if records.len() == before {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "mock is not in the logical scope",
            )
            .into());
        }
        Ok(())
    }

    /// List mocks visible to one scope.
    pub fn list(&self, requested: &ObservationScope) -> Result<Vec<MockRecord>, HostError> {
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

    /// Match one request within a logical page and increment only that record.
    pub fn match_request(
        &mut self,
        scope: &ObservationScope,
        url: &str,
        method: &str,
        resource_type: &str,
    ) -> Result<Option<MockRecord>, HostError> {
        scope.validate()?;
        let records = self.records.get_mut(scope).ok_or_else(|| {
            CoreError::new(
                ErrorCode::PermissionDenied,
                "mock is not in the logical scope",
            )
        })?;
        let matched = records.iter_mut().find(|record| {
            url.contains(&record.url_pattern)
                && record
                    .method
                    .as_deref()
                    .is_none_or(|expected| expected.eq_ignore_ascii_case(method))
                && record
                    .resource_type
                    .as_deref()
                    .is_none_or(|expected| expected.eq_ignore_ascii_case(resource_type))
        });
        Ok(matched.map(|record| {
            record.match_count = record.match_count.saturating_add(1);
            record.clone()
        }))
    }

    /// Convert an underlying mock failure into a stable host failure.
    pub fn failure(&self, failure: MockFailure) -> HostError {
        CoreError::new(failure.code(), mock_failure_message(failure)).into()
    }
}

fn mock_failure_message(failure: MockFailure) -> &'static str {
    match failure {
        MockFailure::CapabilityUnavailable => "network mock capability is unavailable",
        MockFailure::PermissionDenied => "network mock was denied by policy",
        MockFailure::InvalidPattern => "network mock pattern is invalid",
        MockFailure::UnknownOutcome => "network mock outcome is unknown",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentyc_core::{PageId, SpaceId};

    fn scope(space: &str, page: &str) -> ObservationScope {
        ObservationScope::page(
            SpaceId::from_suffix(space).expect("space"),
            PageId::from_suffix(page).expect("page"),
        )
    }

    fn request(space: &str, page: &str) -> MockRequest {
        MockRequest {
            scope: scope(space, page),
            url_pattern: "example.test".to_owned(),
            method: Some("get".to_owned()),
            resource_type: Some("document".to_owned()),
            action: MockAction::Fulfill,
            status: 200,
            response_headers: BTreeMap::from([("Set-Cookie".to_owned(), "secret=one".to_owned())]),
            response_body: Some("secret body".to_owned()),
            authorization: None,
        }
    }

    #[test]
    fn mocks_are_isolated_and_never_retain_secret_bodies() {
        let mut manager = MockManager::default();
        let mut policy = IntentTicketStore::default();
        let first = manager
            .add(request("one", "main"), &mut policy, Timestamp::new(1))
            .expect("mock");
        manager
            .add(request("two", "main"), &mut policy, Timestamp::new(1))
            .expect("mock");
        assert_eq!(manager.list(&first.scope).expect("list").len(), 1);
        assert!(manager.list(&first.scope).expect("list")[0].body_redacted);
        assert!(
            !serde_json::to_string(&first)
                .expect("json")
                .contains("secret body")
        );
        let matched = manager
            .match_request(&first.scope, "https://example.test/", "GET", "document")
            .expect("match")
            .expect("record");
        assert_eq!(matched.match_count, 1);
    }

    #[test]
    fn mock_failures_are_typed() {
        let manager = MockManager::new(false);
        assert!(matches!(
            manager.failure(MockFailure::CapabilityUnavailable),
            HostError::Core(CoreError {
                code: ErrorCode::CapabilityUnavailable,
                ..
            })
        ));
    }
}
