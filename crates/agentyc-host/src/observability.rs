//! Bounded, logical observability records.
//!
//! Logs, network metadata, and traces are retained only under a logical
//! space/page scope. Browser request, target, session, and execution IDs never
//! enter these records. Headers and text are redacted at admission and request
//! or response bodies are represented only by a redaction marker.

use std::collections::{BTreeMap, VecDeque};

use agentyc_core::{CoreError, ErrorCode, Timestamp};
use serde::{Deserialize, Serialize};

use crate::{HostError, trace_policy::PolicyScope};

/// Public alias used by observability consumers for a logical scope.
pub type ObservationScope = PolicyScope;
/// Compatibility alias for callers that use the shorter scope name.
pub type LogicalScope = PolicyScope;

/// Maximum text retained in one observability field.
pub const MAX_OBSERVABILITY_TEXT_BYTES: usize = 4 * 1024;
/// Maximum records retained per logical scope and record kind.
pub const MAX_RECORDS_PER_SCOPE: usize = 256;

/// Structured log severity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LogLevel {
    /// Diagnostic detail.
    Debug,
    /// Normal informational record.
    Info,
    /// Potentially actionable warning.
    Warn,
    /// Failure or unexpected condition.
    Error,
}

impl LogLevel {
    /// Parse an allowlisted severity.
    pub fn parse(value: &str) -> Option<Self> {
        Some(match value.to_ascii_lowercase().as_str() {
            "debug" => Self::Debug,
            "info" | "log" => Self::Info,
            "warn" | "warning" => Self::Warn,
            "error" | "err" => Self::Error,
            _ => return None,
        })
    }
}

/// One redacted logical console/host log entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LogEntry {
    /// Logical space/page that emitted the record.
    pub scope: ObservationScope,
    /// Severity after normalization.
    pub level: LogLevel,
    /// Redacted, bounded message.
    pub message: String,
    /// Host/core timestamp, not a browser event ID.
    pub timestamp: Timestamp,
}

/// Network metadata retained without browser request IDs or bodies.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NetworkEntry {
    /// Logical space/page that observed the request.
    pub scope: ObservationScope,
    /// Redacted URL, including redacted query secrets.
    pub url: String,
    /// Uppercase HTTP method.
    pub method: String,
    /// Optional response status.
    pub status: Option<u16>,
    /// Bounded resource classification.
    pub resource_type: String,
    /// Redacted request headers.
    pub request_headers: BTreeMap<String, String>,
    /// Redacted response headers.
    pub response_headers: BTreeMap<String, String>,
    /// Whether request/response bodies were intentionally omitted.
    pub bodies_redacted: bool,
    /// Host/core timestamp.
    pub timestamp: Timestamp,
}

/// One bounded trace span without raw browser identity or sensitive fields.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TraceEntry {
    /// Logical space/page that emitted the span.
    pub scope: ObservationScope,
    /// Redacted operation/span name.
    pub name: String,
    /// Bounded duration, when known.
    pub duration_ms: Option<u64>,
    /// Allowlisted, redacted attributes.
    pub attributes: BTreeMap<String, String>,
    /// Host/core timestamp.
    pub timestamp: Timestamp,
}

/// Per-store retention limits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ObservabilityLimits {
    /// Maximum records of each kind per scope.
    pub max_records_per_scope: usize,
}

impl Default for ObservabilityLimits {
    fn default() -> Self {
        Self {
            max_records_per_scope: MAX_RECORDS_PER_SCOPE,
        }
    }
}

/// In-memory bounded observability partitioned by logical scope.
#[derive(Debug, Clone)]
pub struct ObservabilityStore {
    limits: ObservabilityLimits,
    logs: BTreeMap<ObservationScope, VecDeque<LogEntry>>,
    network: BTreeMap<ObservationScope, VecDeque<NetworkEntry>>,
    traces: BTreeMap<ObservationScope, VecDeque<TraceEntry>>,
}

impl Default for ObservabilityStore {
    fn default() -> Self {
        Self::new(ObservabilityLimits::default())
    }
}

impl ObservabilityStore {
    /// Construct a bounded store.
    pub fn new(limits: ObservabilityLimits) -> Self {
        Self {
            limits: ObservabilityLimits {
                max_records_per_scope: limits.max_records_per_scope.clamp(1, MAX_RECORDS_PER_SCOPE),
            },
            logs: BTreeMap::new(),
            network: BTreeMap::new(),
            traces: BTreeMap::new(),
        }
    }

    /// Admit a redacted log record.
    pub fn record_log(
        &mut self,
        scope: ObservationScope,
        level: LogLevel,
        message: impl AsRef<str>,
        timestamp: Timestamp,
    ) -> Result<(), HostError> {
        validate_scope(&scope)?;
        let entry = LogEntry {
            scope: scope.clone(),
            level,
            message: redact_text(message.as_ref()),
            timestamp,
        };
        push_bounded(
            self.logs.entry(scope).or_default(),
            entry,
            self.limits.max_records_per_scope,
        );
        Ok(())
    }

    /// Admit network metadata; supplied bodies are never retained.
    #[allow(clippy::too_many_arguments)]
    pub fn record_network(
        &mut self,
        scope: ObservationScope,
        url: impl AsRef<str>,
        method: impl AsRef<str>,
        status: Option<u16>,
        resource_type: impl AsRef<str>,
        request_headers: &BTreeMap<String, String>,
        response_headers: &BTreeMap<String, String>,
        request_body: Option<&str>,
        response_body: Option<&str>,
        timestamp: Timestamp,
    ) -> Result<(), HostError> {
        validate_scope(&scope)?;
        let entry = NetworkEntry {
            scope: scope.clone(),
            url: redact_text(url.as_ref()),
            method: bounded_token(method.as_ref(), 32).to_ascii_uppercase(),
            status,
            resource_type: bounded_token(resource_type.as_ref(), 64),
            request_headers: redact_headers(request_headers),
            response_headers: redact_headers(response_headers),
            bodies_redacted: request_body.is_some() || response_body.is_some(),
            timestamp,
        };
        push_bounded(
            self.network.entry(scope).or_default(),
            entry,
            self.limits.max_records_per_scope,
        );
        Ok(())
    }

    /// Admit a redacted trace span.
    pub fn record_trace(
        &mut self,
        scope: ObservationScope,
        name: impl AsRef<str>,
        duration_ms: Option<u64>,
        attributes: &BTreeMap<String, String>,
        timestamp: Timestamp,
    ) -> Result<(), HostError> {
        validate_scope(&scope)?;
        let entry = TraceEntry {
            scope: scope.clone(),
            name: bounded_token(name.as_ref(), 128),
            duration_ms,
            attributes: redact_attributes(attributes),
            timestamp,
        };
        push_bounded(
            self.traces.entry(scope).or_default(),
            entry,
            self.limits.max_records_per_scope,
        );
        Ok(())
    }

    /// Read logs visible to a requested logical scope.
    pub fn logs(&self, requested: &ObservationScope) -> Result<Vec<LogEntry>, HostError> {
        validate_scope(requested)?;
        Ok(self
            .logs
            .iter()
            .filter(|(scope, _)| scope_matches(scope, requested))
            .flat_map(|(_, entries)| entries.iter().cloned())
            .collect())
    }

    /// Read network metadata visible to a requested logical scope.
    pub fn network(&self, requested: &ObservationScope) -> Result<Vec<NetworkEntry>, HostError> {
        validate_scope(requested)?;
        Ok(self
            .network
            .iter()
            .filter(|(scope, _)| scope_matches(scope, requested))
            .flat_map(|(_, entries)| entries.iter().cloned())
            .collect())
    }

    /// Read traces visible to a requested logical scope.
    pub fn traces(&self, requested: &ObservationScope) -> Result<Vec<TraceEntry>, HostError> {
        validate_scope(requested)?;
        Ok(self
            .traces
            .iter()
            .filter(|(scope, _)| scope_matches(scope, requested))
            .flat_map(|(_, entries)| entries.iter().cloned())
            .collect())
    }

    /// Remove retained records for one exact logical scope.
    pub fn clear(&mut self, scope: &ObservationScope) -> Result<(), HostError> {
        validate_scope(scope)?;
        self.logs.remove(scope);
        self.network.remove(scope);
        self.traces.remove(scope);
        Ok(())
    }
}

fn validate_scope(scope: &ObservationScope) -> Result<(), HostError> {
    scope.validate()
}

fn scope_matches(record: &ObservationScope, requested: &ObservationScope) -> bool {
    record.space_id == requested.space_id
        && requested
            .page_id
            .as_ref()
            .is_none_or(|page_id| record.page_id.as_ref() == Some(page_id))
}

fn push_bounded<T>(entries: &mut VecDeque<T>, entry: T, max: usize) {
    if entries.len() >= max {
        entries.pop_front();
    }
    entries.push_back(entry);
}

fn bounded_token(value: &str, max: usize) -> String {
    value
        .chars()
        .filter(|character| !character.is_control())
        .take(max)
        .collect()
}

/// Redact secrets, bodies, and raw browser identity assignments from text.
pub fn redact_text(value: &str) -> String {
    let mut output = bounded_token(value, MAX_OBSERVABILITY_TEXT_BYTES);
    for marker in [
        "authorization",
        "bearer",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "request_body",
        "response_body",
        "post_data",
    ] {
        redact_marker_value(&mut output, marker);
    }
    for marker in [
        "tabid",
        "targetid",
        "sessionid",
        "groupid",
        "windowid",
        "frameid",
        "requestid",
        "loaderid",
        "objectid",
        "executioncontextid",
        "backendnodeid",
    ] {
        redact_marker_value(&mut output, marker);
    }
    output
}

fn redact_marker_value(value: &mut String, marker: &str) {
    let lower = value.to_ascii_lowercase();
    let Some(start) = lower.find(marker) else {
        return;
    };
    let after_marker = start + marker.len();
    let bytes = value.as_bytes();
    let mut value_start = after_marker;
    while value_start < bytes.len()
        && matches!(
            bytes[value_start],
            b' ' | b'\t' | b'=' | b':' | b'"' | b'\''
        )
    {
        value_start += 1;
    }
    if value_start == after_marker && marker == "bearer" {
        value_start = after_marker;
        while value_start < bytes.len() && bytes[value_start].is_ascii_whitespace() {
            value_start += 1;
        }
    }
    let mut end = value_start;
    while end < bytes.len()
        && !matches!(
            bytes[end],
            b' ' | b'\t' | b'\n' | b'\r' | b'&' | b';' | b','
        )
    {
        end += 1;
    }
    if end > value_start {
        value.replace_range(value_start..end, "[redacted]");
    }
}

/// Redact header values while retaining harmless header names.
pub fn redact_headers(headers: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    headers
        .iter()
        .filter_map(|(name, value)| {
            let safe_name = bounded_token(name, 128).to_ascii_lowercase();
            if safe_name.is_empty() || is_raw_identity_key(&safe_name) {
                return None;
            }
            let safe_value = if is_secret_key(&safe_name) {
                "[redacted]".to_owned()
            } else {
                redact_text(value)
            };
            Some((safe_name, safe_value))
        })
        .collect()
}

fn redact_attributes(attributes: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    attributes
        .iter()
        .filter_map(|(key, value)| {
            let safe_key = bounded_token(key, 128).to_ascii_lowercase();
            if safe_key.is_empty() || is_raw_identity_key(&safe_key) || is_secret_key(&safe_key) {
                return None;
            }
            Some((safe_key, redact_text(value)))
        })
        .collect()
}

fn is_raw_identity_key(key: &str) -> bool {
    let normalized = key.replace(['-', '_'], "");
    matches!(
        normalized.as_str(),
        "tabid"
            | "targetid"
            | "sessionid"
            | "groupid"
            | "windowid"
            | "frameid"
            | "requestid"
            | "loaderid"
            | "objectid"
            | "scriptid"
            | "executioncontextid"
            | "backendnodeid"
    ) || normalized.ends_with("requestid")
        || normalized.ends_with("targetid")
        || normalized.ends_with("sessionid")
}

fn is_secret_key(key: &str) -> bool {
    let normalized = key.replace(['-', '_'], "");
    matches!(
        normalized.as_str(),
        "authorization"
            | "cookie"
            | "setcookie"
            | "proxyauthorization"
            | "password"
            | "passwd"
            | "secret"
            | "token"
            | "accesstoken"
            | "refreshtoken"
            | "apikey"
            | "requestbody"
            | "responsebody"
            | "postdata"
    ) || normalized.contains("secret")
        || normalized.contains("apikey")
        || normalized.contains("authorization")
        || normalized.contains("password")
        || normalized.ends_with("token")
        || normalized.ends_with("requestbody")
        || normalized.ends_with("responsebody")
        || normalized.ends_with("postdata")
}

/// Construct a typed capability-unavailable host failure.
pub fn capability_unavailable(message: impl Into<String>) -> HostError {
    CoreError::new(ErrorCode::CapabilityUnavailable, message).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentyc_core::{PageId, SpaceId};

    fn space(value: &str) -> SpaceId {
        SpaceId::from_suffix(value).expect("space")
    }

    fn page(value: &str) -> PageId {
        PageId::from_suffix(value).expect("page")
    }

    #[test]
    fn two_space_and_page_queries_are_isolated() {
        let mut store = ObservabilityStore::default();
        let first = ObservationScope::page(space("one"), page("main"));
        let second = ObservationScope::page(space("two"), page("main"));
        store
            .record_log(first.clone(), LogLevel::Info, "one", Timestamp::new(1))
            .expect("first log");
        store
            .record_log(second.clone(), LogLevel::Info, "two", Timestamp::new(2))
            .expect("second log");
        assert_eq!(
            store
                .logs(&ObservationScope::space(space("one")))
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            store
                .logs(&ObservationScope::space(space("two")))
                .unwrap()
                .len(),
            1
        );
        assert_eq!(store.logs(&first).unwrap()[0].message, "one");
    }

    #[test]
    fn redaction_removes_secret_bodies_and_raw_browser_ids() {
        let mut store = ObservabilityStore::default();
        let scope = ObservationScope::page(space("redact"), page("main"));
        let mut headers = BTreeMap::new();
        headers.insert("Authorization".to_owned(), "Bearer top-secret".to_owned());
        headers.insert("X-Trace".to_owned(), "safe".to_owned());
        store
            .record_network(
                scope.clone(),
                "https://example.test/?token=hidden",
                "get",
                Some(200),
                "document",
                &headers,
                &BTreeMap::new(),
                Some("password=hidden"),
                Some("secret body"),
                Timestamp::new(1),
            )
            .expect("network");
        let entry = &store.network(&scope).unwrap()[0];
        let encoded = serde_json::to_string(entry).expect("json");
        assert!(!encoded.contains("top-secret"));
        assert!(!encoded.contains("secret body"));
        assert!(!encoded.contains("request_id"));
        assert!(entry.bodies_redacted);
    }
}
