//! Deterministic event-driven wait registration and polling.
//!
//! Waiters capture a router cursor before work begins and only inspect events
//! after that cursor. Callers drive polling from their event loop; this module
//! never sleeps or guesses that time has advanced. The host core does not read
//! an OS clock; a later adapter must supply a trusted [`Clock`] implementation.

use std::{
    collections::BTreeMap,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};

use tokio::sync::Notify;

use agentyc_core::{CoreError, EventKind, EventRecord, EventScope, GenerationWatermark, Timestamp};

use crate::event_router::{EventRouter, generation_at_least};

/// Clock abstraction used by wait deadlines and deterministic tests.
pub trait Clock {
    /// Return the current logical timestamp.
    fn now(&self) -> Timestamp;
}

/// Shared deterministic clock backed by an atomic logical tick.
#[derive(Debug, Clone)]
pub struct FakeClock {
    now: Arc<AtomicU64>,
}

impl FakeClock {
    /// Construct a clock at a logical timestamp.
    pub fn new(now: Timestamp) -> Self {
        Self {
            now: Arc::new(AtomicU64::new(now.get())),
        }
    }

    /// Set the current logical timestamp.
    pub fn set(&self, now: Timestamp) {
        self.now.store(now.get(), Ordering::SeqCst);
    }

    /// Advance the clock by logical ticks.
    pub fn advance(&self, ticks: u64) {
        self.now
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                Some(current.saturating_add(ticks))
            })
            .ok();
    }

    /// Set the clock to an absolute logical timestamp.
    pub fn advance_to(&self, now: Timestamp) {
        self.set(now);
    }
}

impl Default for FakeClock {
    fn default() -> Self {
        Self::new(Timestamp::new(0))
    }
}

impl Clock for FakeClock {
    fn now(&self) -> Timestamp {
        Timestamp::new(self.now.load(Ordering::SeqCst))
    }
}

/// Cooperative cancellation token safe to share between a waiter and its owner.
#[derive(Debug, Clone)]
pub struct CancellationToken {
    cancelled: Arc<AtomicBool>,
    notify: Arc<Notify>,
}

impl Default for CancellationToken {
    fn default() -> Self {
        Self {
            cancelled: Arc::new(AtomicBool::new(false)),
            notify: Arc::new(Notify::new()),
        }
    }
}

impl CancellationToken {
    /// Construct a non-cancelled token.
    pub fn new() -> Self {
        Self::default()
    }

    /// Cancel all registrations holding a clone of this token.
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
        self.notify.notify_waiters();
    }

    /// Return whether cancellation has been requested.
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::SeqCst)
    }

    /// Alias for [`Self::is_cancelled`].
    pub fn cancelled(&self) -> bool {
        self.is_cancelled()
    }

    /// Wait until cancellation is requested without polling or sleeping.
    pub async fn wait_cancelled(&self) {
        loop {
            if self.is_cancelled() {
                return;
            }
            let notified = self.notify.notified();
            if self.is_cancelled() {
                return;
            }
            notified.await;
        }
    }
}

/// Matching strategy for URL, selector text, request, and download fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TextMatcher {
    /// Match the complete value.
    Exact(String),
    /// Match a value containing the text.
    Contains(String),
    /// Match a value beginning with the text.
    Prefix(String),
    /// Match a value ending with the text.
    Suffix(String),
}

impl TextMatcher {
    /// Construct an exact matcher.
    pub fn exact(value: impl Into<String>) -> Self {
        Self::Exact(value.into())
    }

    /// Construct a substring matcher.
    pub fn contains(value: impl Into<String>) -> Self {
        Self::Contains(value.into())
    }

    /// Return whether a value satisfies this matcher.
    pub fn matches(&self, value: &str) -> bool {
        match self {
            Self::Exact(expected) => value == expected,
            Self::Contains(expected) => value.contains(expected),
            Self::Prefix(expected) => value.starts_with(expected),
            Self::Suffix(expected) => value.ends_with(expected),
        }
    }
}

/// Navigation transition associated with a URL event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NavigationKind {
    /// A normal URL navigation, including redirects.
    Url,
    /// A same-document history transition.
    History,
    /// A reload of the current document.
    Reload,
    /// Any navigation transition.
    Any,
}

/// Direction of a history transition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HistoryDirection {
    /// Back navigation.
    Back,
    /// Forward navigation.
    Forward,
    /// Either history direction.
    Any,
}

/// Element state used by an element wait.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ElementState {
    /// The element exists in the document.
    Present,
    /// The element does not exist in the document.
    Absent,
    /// The element exists and is visible.
    Visible,
    /// The element exists and is hidden.
    Hidden,
}

/// Download lifecycle state used by a download wait.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DownloadState {
    /// The download has started.
    Started,
    /// The download completed successfully.
    Completed,
    /// The download failed or was cancelled.
    Failed,
}

/// Typed URL/navigation wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UrlWait {
    /// URL matcher.
    pub matcher: TextMatcher,
    /// Optional transition kind.
    pub navigation: Option<NavigationKind>,
    /// Optional generation postcondition.
    pub generation: Option<GenerationWatermark>,
}

/// Typed history wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistoryWait {
    /// History direction to observe.
    pub direction: HistoryDirection,
    /// Optional generation postcondition.
    pub generation: Option<GenerationWatermark>,
}

/// Typed reload wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReloadWait {
    /// Optional generation postcondition.
    pub generation: Option<GenerationWatermark>,
}

/// Typed network-idle wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetworkIdleWait {
    /// Required quiet interval in logical clock ticks.
    pub quiet_for: u64,
}

/// Typed request wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestWait {
    /// Optional URL matcher.
    pub url: Option<TextMatcher>,
    /// Optional HTTP method.
    pub method: Option<String>,
    /// Optional resource type.
    pub resource_type: Option<String>,
}

/// Typed response wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResponseWait {
    /// Optional URL matcher.
    pub url: Option<TextMatcher>,
    /// Optional HTTP method correlated from the request.
    pub method: Option<String>,
    /// Optional resource type.
    pub resource_type: Option<String>,
    /// Optional HTTP status.
    pub status: Option<u16>,
}

/// Typed DOM and geometry stability wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StableDomWait {
    /// Required mutation quiet interval in logical clock ticks.
    pub quiet_for: u64,
    /// Required geometry quiet interval in logical clock ticks.
    pub geometry_quiet_for: u64,
}

/// Typed element wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ElementWait {
    /// Optional CSS selector.
    pub selector: Option<String>,
    /// Optional text matcher.
    pub text: Option<TextMatcher>,
    /// Required element state.
    pub state: ElementState,
    /// Optional generation postcondition.
    pub generation: Option<GenerationWatermark>,
}

/// Typed logical-page wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageWait {
    /// Optional logical page identity.
    pub page_id: Option<String>,
    /// Optional lifecycle value.
    pub lifecycle: Option<String>,
    /// Optional generation postcondition.
    pub generation: Option<GenerationWatermark>,
}

/// Typed download wait.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadWait {
    /// Optional filename matcher.
    pub name: Option<TextMatcher>,
    /// Required download state.
    pub state: DownloadState,
}

/// Conditions that can be matched entirely from broker event data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WaitCondition {
    /// Match any event of one logical kind.
    EventKind(EventKind),
    /// Match an event kind and all listed payload fields.
    Event {
        /// Optional event kind.
        kind: Option<EventKind>,
        /// Required payload key/value pairs.
        payload: std::collections::BTreeMap<String, String>,
    },
    /// Match one exact payload key/value pair on any event kind.
    Payload {
        /// Payload key.
        key: String,
        /// Required value.
        value: String,
    },
    /// Match an event whose generation watermark reaches a target.
    GenerationAtLeast(GenerationWatermark),
    /// Match when any nested condition matches.
    Any(Vec<WaitCondition>),
    /// Match when every nested condition matches the same event.
    All(Vec<WaitCondition>),
    /// Match a URL reached by a navigation or same-document transition.
    Url(UrlWait),
    /// Match a history transition.
    History(HistoryWait),
    /// Match a reload transition.
    Reload(ReloadWait),
    /// Match a scoped network-idle postcondition.
    NetworkIdle(NetworkIdleWait),
    /// Match a network request.
    Request(RequestWait),
    /// Match a network response, including response-before-request traces.
    Response(ResponseWait),
    /// Match mutation quiet and geometry stability.
    StableDom(StableDomWait),
    /// Match an element observation.
    Element(ElementWait),
    /// Match a logical page observation.
    Page(PageWait),
    /// Match a download observation.
    Download(DownloadWait),
}

impl WaitCondition {
    /// Construct an event-kind condition.
    pub const fn event_kind(kind: EventKind) -> Self {
        Self::EventKind(kind)
    }

    /// Construct an exact URL wait.
    pub fn url(value: impl Into<String>) -> Self {
        Self::Url(UrlWait {
            matcher: TextMatcher::Exact(value.into()),
            navigation: None,
            generation: None,
        })
    }

    /// Construct a substring URL wait.
    pub fn url_contains(value: impl Into<String>) -> Self {
        Self::Url(UrlWait {
            matcher: TextMatcher::Contains(value.into()),
            navigation: None,
            generation: None,
        })
    }

    /// Construct a history wait.
    pub const fn history(direction: HistoryDirection) -> Self {
        Self::History(HistoryWait {
            direction,
            generation: None,
        })
    }

    /// Construct a reload wait.
    pub const fn reload() -> Self {
        Self::Reload(ReloadWait { generation: None })
    }

    /// Construct a network-idle wait.
    pub const fn network_idle(quiet_for: u64) -> Self {
        Self::NetworkIdle(NetworkIdleWait { quiet_for })
    }

    /// Construct a request wait.
    pub const fn request() -> Self {
        Self::Request(RequestWait {
            url: None,
            method: None,
            resource_type: None,
        })
    }

    /// Construct a response wait.
    pub const fn response() -> Self {
        Self::Response(ResponseWait {
            url: None,
            method: None,
            resource_type: None,
            status: None,
        })
    }

    /// Construct a stable-DOM wait with one quiet interval for both signals.
    pub const fn stable_dom(quiet_for: u64) -> Self {
        Self::StableDom(StableDomWait {
            quiet_for,
            geometry_quiet_for: quiet_for,
        })
    }

    /// Construct an element wait by CSS selector.
    pub fn element(selector: impl Into<String>, state: ElementState) -> Self {
        Self::Element(ElementWait {
            selector: Some(selector.into()),
            text: None,
            state,
            generation: None,
        })
    }

    /// Construct a logical-page wait.
    pub fn page(page_id: impl Into<String>) -> Self {
        Self::Page(PageWait {
            page_id: Some(page_id.into()),
            lifecycle: None,
            generation: None,
        })
    }

    /// Construct a completed-download wait.
    pub fn download(name: impl Into<String>) -> Self {
        Self::Download(DownloadWait {
            name: Some(TextMatcher::Exact(name.into())),
            state: DownloadState::Completed,
        })
    }

    /// Construct a payload condition.
    pub fn payload(key: impl Into<String>, value: impl Into<String>) -> Self {
        Self::Payload {
            key: key.into(),
            value: value.into(),
        }
    }

    /// Return whether this condition matches an event.
    pub fn matches(&self, event: &EventRecord) -> bool {
        match self {
            Self::EventKind(kind) => event.event == *kind,
            Self::Event { kind, payload } => {
                kind.is_none_or(|expected| event.event == expected)
                    && payload.iter().all(|(key, value)| {
                        event.payload.get(key).is_some_and(|actual| actual == value)
                    })
            }
            Self::Payload { key, value } => {
                event.payload.get(key).is_some_and(|actual| actual == value)
            }
            Self::GenerationAtLeast(target) => generation_at_least(&event.generation, target),
            Self::Any(conditions) => conditions.iter().any(|condition| condition.matches(event)),
            Self::All(conditions) => conditions.iter().all(|condition| condition.matches(event)),
            Self::Url(condition) => typed_event_matches_url(condition, event),
            Self::History(condition) => typed_event_matches_history(condition, event),
            Self::Reload(condition) => typed_event_matches_reload(condition, event),
            Self::NetworkIdle(_) | Self::StableDom(_) => false,
            Self::Request(condition) => typed_event_matches_request(condition, event),
            Self::Response(condition) => typed_event_matches_response(condition, event, None),
            Self::Element(condition) => typed_event_matches_element(condition, event),
            Self::Page(condition) => typed_event_matches_page(condition, event),
            Self::Download(condition) => typed_event_matches_download(condition, event),
        }
    }
}

#[derive(Debug, Clone, Default)]
struct WaitState {
    network_active: BTreeMap<String, bool>,
    network_quiet_since: Option<Timestamp>,
    dom_quiet_since: Option<Timestamp>,
    geometry_quiet_since: Option<Timestamp>,
    geometry_signature: Option<String>,
    request_metadata: BTreeMap<String, (String, String, String)>,
    pending_responses: BTreeMap<String, EventRecord>,
    matched_event: Option<EventRecord>,
    last_event: Option<EventRecord>,
    last_observed_at: Option<Timestamp>,
}

impl WaitState {
    fn new(now: Timestamp) -> Self {
        Self {
            network_quiet_since: Some(now),
            dom_quiet_since: Some(now),
            geometry_quiet_since: Some(now),
            last_observed_at: Some(now),
            ..Self::default()
        }
    }

    fn observe(&mut self, condition: &WaitCondition, event: &EventRecord, now: Timestamp) {
        let now = self.observe_time(now, event);
        self.last_event = Some(event.clone());
        match condition {
            WaitCondition::NetworkIdle(_) => self.observe_network(event, now),
            WaitCondition::Response(response) => {
                self.observe_request_metadata(response, event);
                self.observe_response(response, event);
            }
            WaitCondition::StableDom(_) => self.observe_dom(event, now),
            WaitCondition::Url(url) if typed_event_matches_url(url, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::History(history) if typed_event_matches_history(history, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::Reload(reload) if typed_event_matches_reload(reload, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::Request(request) if typed_event_matches_request(request, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::Element(element) if typed_event_matches_element(element, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::Page(page) if typed_event_matches_page(page, event) => {
                self.matched_event = Some(event.clone());
            }
            WaitCondition::Download(download) if typed_event_matches_download(download, event) => {
                self.matched_event = Some(event.clone());
            }
            _ if condition.matches(event) => {
                self.matched_event = Some(event.clone());
            }
            _ => {}
        }
    }

    fn observe_time(&mut self, now: Timestamp, event: &EventRecord) -> Timestamp {
        let event_time = event
            .payload
            .get("timestamp")
            .or_else(|| event.payload.get("at"))
            .and_then(|value| value.parse::<u64>().ok())
            .map(Timestamp::new)
            .unwrap_or(now);
        let previous = self.last_observed_at.unwrap_or(event_time);
        let effective = Timestamp::new(previous.get().max(event_time.get()).max(now.get()));
        self.last_observed_at = Some(effective);
        effective
    }

    fn observe_network(&mut self, event: &EventRecord, now: Timestamp) {
        let Some(request_id) = event
            .payload
            .get("request_id")
            .or_else(|| event.payload.get("requestId"))
            .map(String::as_str)
        else {
            return;
        };
        let excluded = excluded_network_event(event);
        if network_request_started(event) {
            if !excluded {
                self.network_active.insert(request_id.to_owned(), false);
                self.network_quiet_since = None;
            }
            return;
        }
        if network_request_finished(event) {
            self.network_active.remove(request_id);
            if self.network_active.is_empty() {
                self.network_quiet_since = Some(now);
            }
        }
    }

    fn observe_request_metadata(&mut self, condition: &ResponseWait, event: &EventRecord) {
        if !request_event(event) {
            return;
        }
        let Some(request_id) = event
            .payload
            .get("request_id")
            .or_else(|| event.payload.get("requestId"))
            .cloned()
        else {
            return;
        };
        let metadata = (
            payload_value(event, &["url", "href"])
                .unwrap_or_default()
                .to_owned(),
            payload_value(event, &["method"])
                .unwrap_or_default()
                .to_owned(),
            payload_value(event, &["resource_type", "resourceType", "type"])
                .unwrap_or_default()
                .to_owned(),
        );
        self.request_metadata.insert(request_id.clone(), metadata);
        if let Some(response) = self.pending_responses.remove(&request_id)
            && typed_event_matches_response(
                condition,
                &response,
                self.request_metadata.get(&request_id),
            )
        {
            self.matched_event = Some(response);
        }
    }

    fn observe_response(&mut self, condition: &ResponseWait, event: &EventRecord) {
        if !response_event(event) {
            return;
        }
        let request_id = event
            .payload
            .get("request_id")
            .or_else(|| event.payload.get("requestId"))
            .cloned()
            .unwrap_or_default();
        let metadata = self.request_metadata.get(&request_id);
        if typed_event_matches_response(condition, event, metadata) {
            self.matched_event = Some(event.clone());
        } else if !request_id.is_empty() {
            // CDP can deliver responseReceived before the corresponding request
            // notification reaches a fan-out consumer. Keep it until correlation
            // metadata arrives instead of dropping a valid response.
            self.pending_responses
                .insert(request_id.clone(), event.clone());
        }
        if !request_id.is_empty() {
            self.request_metadata.entry(request_id).or_default();
        }
    }

    fn observe_dom(&mut self, event: &EventRecord, now: Timestamp) {
        if dom_mutation_event(event) {
            self.dom_quiet_since = Some(now);
        }
        if geometry_event(event) {
            let signature = event
                .payload
                .get("geometry_signature")
                .or_else(|| event.payload.get("geometry"))
                .cloned();
            if signature != self.geometry_signature {
                self.geometry_signature = signature;
                self.geometry_quiet_since = Some(now);
            } else if self.geometry_quiet_since.is_none() {
                self.geometry_quiet_since = Some(now);
            }
        }
    }

    fn satisfied(&self, condition: &WaitCondition, now: Timestamp) -> bool {
        if self.matched_event.is_some() {
            return true;
        }
        match condition {
            WaitCondition::NetworkIdle(config) => {
                self.network_active.is_empty()
                    && self.network_quiet_since.is_some_and(|since| {
                        now.get().saturating_sub(since.get()) >= config.quiet_for
                    })
            }
            WaitCondition::StableDom(config) => {
                self.dom_quiet_since
                    .is_some_and(|since| now.get().saturating_sub(since.get()) >= config.quiet_for)
                    && self.geometry_quiet_since.is_some_and(|since| {
                        now.get().saturating_sub(since.get()) >= config.geometry_quiet_for
                    })
            }
            _ => false,
        }
    }

    fn matched_event(&self) -> Option<EventRecord> {
        self.matched_event
            .clone()
            .or_else(|| self.last_event.clone())
    }
}

fn payload_value<'a>(event: &'a EventRecord, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|key| event.payload.get(*key).map(String::as_str))
}

fn payload_true(event: &EventRecord, keys: &[&str]) -> bool {
    payload_value(event, keys)
        .is_some_and(|value| matches!(value.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
}

fn event_label_is(event: &EventRecord, labels: &[&str]) -> bool {
    ["wait_kind", "kind", "phase", "event"]
        .iter()
        .filter_map(|key| event.payload.get(*key))
        .any(|value| labels.iter().any(|label| value.eq_ignore_ascii_case(label)))
}

fn event_has_label(event: &EventRecord, labels: &[&str]) -> bool {
    ["wait_kind", "kind", "phase", "event"]
        .iter()
        .filter_map(|key| event.payload.get(*key))
        .any(|value| {
            labels.iter().any(|label| {
                value
                    .to_ascii_lowercase()
                    .contains(&label.to_ascii_lowercase())
            })
        })
}

fn generation_matches(event: &EventRecord, target: &Option<GenerationWatermark>) -> bool {
    target
        .as_ref()
        .is_none_or(|target| generation_at_least(&event.generation, target))
}

fn transition_matches(event: &EventRecord, expected: Option<NavigationKind>) -> bool {
    let Some(expected) = expected else {
        return true;
    };
    if matches!(expected, NavigationKind::Any) {
        return true;
    }
    let actual = payload_value(event, &["navigation", "navigation_kind", "transition"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    match expected {
        NavigationKind::Url => actual.is_empty() || actual == "url" || actual == "navigate",
        NavigationKind::History => actual == "history" || actual == "same_document",
        NavigationKind::Reload => actual == "reload",
        NavigationKind::Any => true,
    }
}

fn typed_event_matches_url(condition: &UrlWait, event: &EventRecord) -> bool {
    payload_value(event, &["url", "href"]).is_some_and(|url| condition.matcher.matches(url))
        && transition_matches(event, condition.navigation)
        && generation_matches(event, &condition.generation)
        && (event_has_label(
            event,
            &[
                "url",
                "navigation",
                "navigated",
                "history",
                "reload",
                "frame",
            ],
        ) || event.payload.contains_key("url"))
}

fn typed_event_matches_history(condition: &HistoryWait, event: &EventRecord) -> bool {
    let direction = payload_value(event, &["direction", "history_direction"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    let direction_matches = match condition.direction {
        HistoryDirection::Back => direction.is_empty() || direction == "back",
        HistoryDirection::Forward => direction.is_empty() || direction == "forward",
        HistoryDirection::Any => true,
    };
    direction_matches
        && generation_matches(event, &condition.generation)
        && (event_label_is(event, &["history", "navigated_within_document"])
            || payload_value(event, &["navigation", "navigation_kind"])
                .is_some_and(|value| value.eq_ignore_ascii_case("history")))
}

fn typed_event_matches_reload(condition: &ReloadWait, event: &EventRecord) -> bool {
    generation_matches(event, &condition.generation)
        && (event_label_is(event, &["reload", "reloaded"])
            || payload_value(event, &["navigation", "navigation_kind", "transition"])
                .is_some_and(|value| value.eq_ignore_ascii_case("reload")))
}

fn request_event(event: &EventRecord) -> bool {
    event_has_label(event, &["request", "request_will_be_sent"])
        || (event.payload.contains_key("request_id")
            && !event.payload.contains_key("status")
            && !event.payload.contains_key("response_status"))
}

fn response_event(event: &EventRecord) -> bool {
    event_has_label(event, &["response", "response_received"])
        || event.payload.contains_key("status")
        || event.payload.contains_key("response_status")
}

fn network_request_started(event: &EventRecord) -> bool {
    request_event(event)
        && (event_label_is(
            event,
            &[
                "request",
                "request_will_be_sent",
                "started",
                "request_started",
            ],
        ) || event.payload.contains_key("url"))
}

fn network_request_finished(event: &EventRecord) -> bool {
    event_has_label(
        event,
        &[
            "loading_finished",
            "loading_failed",
            "finished",
            "failed",
            "complete",
        ],
    ) || payload_true(event, &["finished", "completed", "failed"])
}

fn excluded_network_event(event: &EventRecord) -> bool {
    if payload_true(
        event,
        &[
            "excluded",
            "long_lived",
            "websocket",
            "download",
            "analytics",
        ],
    ) {
        return true;
    }
    let resource_type = payload_value(event, &["resource_type", "resourceType", "type"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    if matches!(
        resource_type.as_str(),
        "websocket" | "eventsource" | "download" | "webtransport"
    ) {
        return true;
    }
    let url = payload_value(event, &["url", "href"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    [
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "segment.io",
        "mixpanel",
        "amplitude",
        "hotjar",
        "plausible.io",
        "/analytics",
    ]
    .iter()
    .any(|marker| url.contains(marker))
}

fn typed_event_matches_request(condition: &RequestWait, event: &EventRecord) -> bool {
    request_event(event)
        && condition.url.as_ref().is_none_or(|matcher| {
            payload_value(event, &["url", "href"]).is_some_and(|url| matcher.matches(url))
        })
        && condition.method.as_ref().is_none_or(|method| {
            payload_value(event, &["method"])
                .is_some_and(|actual| actual.eq_ignore_ascii_case(method))
        })
        && condition
            .resource_type
            .as_ref()
            .is_none_or(|resource_type| {
                payload_value(event, &["resource_type", "resourceType", "type"])
                    .is_some_and(|actual| actual.eq_ignore_ascii_case(resource_type))
            })
}

fn typed_event_matches_response(
    condition: &ResponseWait,
    event: &EventRecord,
    request_metadata: Option<&(String, String, String)>,
) -> bool {
    response_event(event)
        && condition.url.as_ref().is_none_or(|matcher| {
            payload_value(event, &["url", "href"]).is_some_and(|url| matcher.matches(url))
        })
        && condition.method.as_ref().is_none_or(|method| {
            payload_value(event, &["method"])
                .or_else(|| request_metadata.map(|metadata| metadata.1.as_str()))
                .is_some_and(|actual| actual.eq_ignore_ascii_case(method))
        })
        && condition
            .resource_type
            .as_ref()
            .is_none_or(|resource_type| {
                payload_value(event, &["resource_type", "resourceType", "type"])
                    .or_else(|| request_metadata.map(|metadata| metadata.2.as_str()))
                    .is_some_and(|actual| actual.eq_ignore_ascii_case(resource_type))
            })
        && condition.status.is_none_or(|status| {
            payload_value(event, &["status", "response_status"])
                .and_then(|value| value.parse::<u16>().ok())
                .is_some_and(|actual| actual == status)
        })
}

fn dom_mutation_event(event: &EventRecord) -> bool {
    event_has_label(event, &["dom", "mutation", "dom_mutation"])
        || payload_true(event, &["dom_changed", "mutation"])
}

fn geometry_event(event: &EventRecord) -> bool {
    event_has_label(event, &["geometry", "layout", "resize"])
        || event.payload.contains_key("geometry_signature")
        || event.payload.contains_key("geometry")
        || payload_true(event, &["geometry_changed", "layout_changed"])
}

fn typed_event_matches_element(condition: &ElementWait, event: &EventRecord) -> bool {
    let selector_matches = condition.selector.as_ref().is_none_or(|selector| {
        payload_value(event, &["selector"]).is_some_and(|actual| actual == selector)
    });
    let text_matches = condition.text.as_ref().is_none_or(|matcher| {
        payload_value(event, &["text", "inner_text", "content"])
            .is_some_and(|text| matcher.matches(text))
    });
    let state = payload_value(event, &["state", "element_state"]).unwrap_or_default();
    let state_matches = match condition.state {
        ElementState::Present => {
            state.is_empty() || matches!(state, "present" | "visible" | "attached")
        }
        ElementState::Absent => state == "absent" || state == "detached" || state == "missing",
        ElementState::Visible => state == "visible",
        ElementState::Hidden => state == "hidden",
    };
    selector_matches
        && text_matches
        && state_matches
        && generation_matches(event, &condition.generation)
        && (event_has_label(event, &["element", "selector"]) || event.payload.contains_key("state"))
}

fn typed_event_matches_page(condition: &PageWait, event: &EventRecord) -> bool {
    condition.page_id.as_ref().is_none_or(|page_id| {
        payload_value(event, &["page_id", "pageId"]).is_some_and(|actual| actual == page_id)
    }) && condition.lifecycle.as_ref().is_none_or(|lifecycle| {
        payload_value(event, &["lifecycle", "state"]).is_some_and(|actual| actual == lifecycle)
    }) && generation_matches(event, &condition.generation)
        && (event_has_label(event, &["page", "connection", "target"])
            || event.payload.contains_key("page_id"))
}

fn event_signals_resync(event: &EventRecord) -> bool {
    payload_true(
        event,
        &[
            "generation_replaced",
            "target_replaced",
            "document_replaced",
            "disconnected",
            "disconnect",
            "resync_required",
        ],
    ) || payload_value(event, &["connection_state", "lifecycle", "state"]).is_some_and(|state| {
        matches!(
            state.to_ascii_lowercase().as_str(),
            "disconnected" | "replaced" | "resync_required"
        )
    }) || (event.event == EventKind::ConnectionChanged
        && payload_value(event, &["status", "connection"])
            .is_some_and(|state| state.eq_ignore_ascii_case("disconnected")))
}

fn typed_event_matches_download(condition: &DownloadWait, event: &EventRecord) -> bool {
    let name_matches = condition.name.as_ref().is_none_or(|matcher| {
        payload_value(event, &["name", "filename", "file_name"])
            .is_some_and(|name| matcher.matches(name))
    });
    let state = payload_value(event, &["state", "download_state"]).unwrap_or_default();
    let state_matches = match condition.state {
        DownloadState::Started => state == "started" || state == "in_progress",
        DownloadState::Completed => state == "completed" || state == "complete",
        DownloadState::Failed => state == "failed" || state == "cancelled",
    };
    name_matches
        && state_matches
        && (event_has_label(event, &["download"]) || event.payload.contains_key("filename"))
}

/// Registration returned before an action or external event source runs.
#[derive(Debug, Clone)]
pub struct WaitRegistration {
    /// Stable local registration identity.
    pub id: u64,
    /// Cursor captured before the waiter was registered.
    pub registered_after: agentyc_core::EventCursor,
    /// Current replay cursor for this registration.
    pub cursor: agentyc_core::EventCursor,
    /// Condition to match.
    pub condition: WaitCondition,
    /// Optional exact logical scope.
    pub scope: Option<EventScope>,
    /// Absolute logical deadline.
    pub deadline: Timestamp,
    /// Shared cancellation state.
    pub cancellation: CancellationToken,
    /// Absolute logical time at which this registration was created.
    pub registered_at: Timestamp,
    state: WaitState,
    finished: bool,
}

/// Alias used by callers that treat a registration as a handle.
pub type WaitHandle = WaitRegistration;

/// Poll result for a registered event-driven wait.
#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(clippy::large_enum_variant)]
pub enum WaitOutcome {
    /// No matching event has arrived and the deadline has not elapsed.
    Pending {
        /// Cursor safe for the next poll.
        cursor: agentyc_core::EventCursor,
    },
    /// A matching event was observed.
    Matched {
        /// Matching broker event.
        event: EventRecord,
        /// Cursor after the observed batch.
        cursor: agentyc_core::EventCursor,
    },
    /// The event history or broker epoch cannot prove the condition.
    ResyncRequired {
        /// Cursor to use after a fresh snapshot/watermark read.
        cursor: agentyc_core::EventCursor,
    },
    /// Cancellation was observed before event handling.
    Cancelled {
        /// Last safe cursor.
        cursor: agentyc_core::EventCursor,
    },
    /// The logical deadline was reached before event handling.
    DeadlineExceeded {
        /// Last safe cursor.
        cursor: agentyc_core::EventCursor,
    },
}

/// Short alias for [`WaitOutcome`].
pub type WaitPoll = WaitOutcome;

/// Synchronous wait engine with an injected clock.
#[derive(Debug, Clone)]
pub struct WaitEngine<C> {
    clock: C,
    next_id: u64,
}

impl<C: Clock> WaitEngine<C> {
    /// Construct an engine around a clock.
    pub fn new(clock: C) -> Self {
        Self { clock, next_id: 1 }
    }

    /// Return the injected clock's current timestamp.
    pub fn now(&self) -> Timestamp {
        self.clock.now()
    }

    /// Register against the router watermark captured at this exact point.
    pub fn register(
        &mut self,
        router: &EventRouter,
        condition: WaitCondition,
        deadline: Timestamp,
        cancellation: CancellationToken,
    ) -> WaitRegistration {
        self.register_scoped(router, condition, None, deadline, cancellation)
    }

    /// Register with an exact logical scope and absolute deadline.
    pub fn register_scoped(
        &mut self,
        router: &EventRouter,
        condition: WaitCondition,
        scope: Option<EventScope>,
        deadline: Timestamp,
        cancellation: CancellationToken,
    ) -> WaitRegistration {
        let cursor = router.cursor();
        let registered_at = self.now();
        let id = self.next_id;
        self.next_id = self.next_id.saturating_add(1);
        WaitRegistration {
            id,
            registered_after: cursor,
            cursor,
            condition,
            scope,
            deadline,
            cancellation,
            registered_at,
            state: WaitState::new(registered_at),
            finished: false,
        }
    }

    /// Register for a bounded logical duration without sleeping.
    pub fn register_for(
        &mut self,
        router: &EventRouter,
        condition: WaitCondition,
        duration: u64,
        cancellation: CancellationToken,
    ) -> WaitRegistration {
        let deadline = Timestamp::new(self.now().get().saturating_add(duration));
        self.register(router, condition, deadline, cancellation)
    }

    /// Poll a registration using retained broker events only.
    pub fn poll(&self, registration: &mut WaitRegistration, router: &EventRouter) -> WaitOutcome {
        if registration.finished {
            return WaitOutcome::Pending {
                cursor: registration.cursor,
            };
        }
        // Cancellation and deadline are checked before replay so an event at the
        // exact deadline cannot race a terminal timeout.
        if registration.cancellation.is_cancelled() {
            registration.finished = true;
            return WaitOutcome::Cancelled {
                cursor: registration.cursor,
            };
        }
        let now = self.now().max(registration.registered_at);
        if now.get() >= registration.deadline.get() {
            registration.finished = true;
            return WaitOutcome::DeadlineExceeded {
                cursor: registration.cursor,
            };
        }

        let batch = router.replay(registration.cursor, registration.scope.as_ref());
        if batch.result == agentyc_core::ResumeResult::ResyncRequired {
            registration.finished = true;
            return WaitOutcome::ResyncRequired {
                cursor: batch.cursor,
            };
        }
        registration.cursor = batch.cursor;
        for event in batch.events {
            if event.requires_resync() || event_signals_resync(&event) {
                registration.finished = true;
                return WaitOutcome::ResyncRequired {
                    cursor: registration.cursor,
                };
            }
            registration
                .state
                .observe(&registration.condition, &event, now);
            if registration.state.satisfied(&registration.condition, now) {
                registration.finished = true;
                if let Some(event) = registration.state.matched_event() {
                    return WaitOutcome::Matched {
                        event,
                        cursor: registration.cursor,
                    };
                }
            }
        }
        if registration.state.satisfied(&registration.condition, now) {
            registration.finished = true;
            if let Some(event) = registration.state.matched_event() {
                return WaitOutcome::Matched {
                    event,
                    cursor: registration.cursor,
                };
            }
        }
        WaitOutcome::Pending {
            cursor: registration.cursor,
        }
    }

    /// Return a stable timeout error for adapters that expose `Result` APIs.
    pub fn outcome_error(outcome: &WaitOutcome) -> Option<CoreError> {
        match outcome {
            WaitOutcome::ResyncRequired { .. } => Some(CoreError::new(
                agentyc_core::ErrorCode::EventLagged,
                "wait event history requires resynchronization",
            )),
            WaitOutcome::Cancelled { .. } => Some(CoreError::new(
                agentyc_core::ErrorCode::Cancelled,
                "wait was cancelled",
            )),
            WaitOutcome::DeadlineExceeded { .. } => Some(CoreError::new(
                agentyc_core::ErrorCode::Timeout,
                "wait deadline exceeded",
            )),
            WaitOutcome::Pending { .. } | WaitOutcome::Matched { .. } => None,
        }
    }
}

impl WaitEngine<FakeClock> {
    /// Construct a deterministic engine and its shared fake clock.
    pub fn deterministic(now: Timestamp) -> (Self, FakeClock) {
        let clock = FakeClock::new(now);
        (Self::new(clock.clone()), clock)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event_router::{EventRouter, RouterLimits};
    use agentyc_core::{BrokerEpoch, EventId, EventSequence};
    use std::collections::BTreeMap;

    fn event(sequence: u64, kind: EventKind) -> EventRecord {
        EventRecord {
            protocol: agentyc_core::PROTOCOL_VERSION,
            event_id: EventId::from_suffix(format!("event-{sequence}")).expect("event identity"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(sequence),
            scope: EventScope {
                space_id: None,
                page_id: None,
            },
            event: kind,
            generation: GenerationWatermark::default(),
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::new(),
        }
    }

    fn trace_event(
        sequence: u64,
        at: u64,
        fields: impl IntoIterator<Item = (&'static str, &'static str)>,
    ) -> EventRecord {
        let mut record = event(sequence, EventKind::ConnectionChanged);
        record
            .payload
            .insert("timestamp".to_owned(), at.to_string());
        record.payload.extend(
            fields
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value.to_owned())),
        );
        record
    }

    #[test]
    fn events_after_registration_match_without_sleeping() {
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let clock = FakeClock::new(Timestamp::new(0));
        let mut engine = WaitEngine::new(clock);
        let cancel = CancellationToken::new();
        let mut registration = engine.register(
            &router,
            WaitCondition::event_kind(EventKind::PageChanged),
            Timestamp::new(10),
            cancel,
        );
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        router.ingest(event(1, EventKind::PageChanged));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Matched { .. }
        ));
    }

    #[test]
    fn events_before_registration_are_not_replayed_as_new_matches() {
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        router.ingest(event(1, EventKind::PageChanged));
        let mut engine = WaitEngine::new(FakeClock::new(Timestamp::new(0)));
        let mut registration = engine.register(
            &router,
            WaitCondition::event_kind(EventKind::PageChanged),
            Timestamp::new(10),
            CancellationToken::new(),
        );
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
    }

    #[test]
    fn cancellation_and_deadline_are_checked_before_replay() {
        let router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let clock = FakeClock::new(Timestamp::new(0));
        let mut engine = WaitEngine::new(clock.clone());
        let cancellation = CancellationToken::new();
        let mut cancelled = engine.register(
            &router,
            WaitCondition::event_kind(EventKind::PageChanged),
            Timestamp::new(5),
            cancellation.clone(),
        );
        cancellation.cancel();
        assert!(matches!(
            engine.poll(&mut cancelled, &router),
            WaitOutcome::Cancelled { .. }
        ));
        let mut deadline = engine.register(
            &router,
            WaitCondition::event_kind(EventKind::PageChanged),
            Timestamp::new(5),
            CancellationToken::new(),
        );
        clock.set(Timestamp::new(5));
        assert!(matches!(
            engine.poll(&mut deadline, &router),
            WaitOutcome::DeadlineExceeded { .. }
        ));
    }

    #[test]
    fn delayed_redirect_and_same_document_navigation_use_typed_postconditions() {
        let mut router = EventRouter::empty();
        let clock = FakeClock::default();
        let mut engine = WaitEngine::new(clock.clone());
        let mut registration = engine.register(
            &router,
            WaitCondition::url("https://example.test/final"),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        router.ingest(trace_event(
            1,
            2,
            [
                ("wait_kind", "navigation"),
                ("url", "https://example.test/redirect"),
            ],
        ));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        clock.set(Timestamp::new(8));
        router.ingest(trace_event(
            2,
            8,
            [
                ("wait_kind", "navigation"),
                ("url", "https://example.test/final"),
            ],
        ));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Matched { .. }
        ));

        let mut history = engine.register(
            &router,
            WaitCondition::history(HistoryDirection::Back),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        clock.set(Timestamp::new(9));
        router.ingest(trace_event(
            3,
            9,
            [
                ("wait_kind", "history"),
                ("direction", "back"),
                ("url", "https://example.test/final#old"),
            ],
        ));
        assert!(matches!(
            engine.poll(&mut history, &router),
            WaitOutcome::Matched { .. }
        ));

        let mut reload = engine.register(
            &router,
            WaitCondition::reload(),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        clock.set(Timestamp::new(10));
        router.ingest(trace_event(
            4,
            10,
            [
                ("wait_kind", "reload"),
                ("url", "https://example.test/final"),
            ],
        ));
        assert!(matches!(
            engine.poll(&mut reload, &router),
            WaitOutcome::Matched { .. }
        ));
    }

    #[test]
    fn response_before_request_is_correlated_without_dropping_the_response() {
        let mut router = EventRouter::empty();
        let mut engine = WaitEngine::new(FakeClock::default());
        let mut registration = engine.register(
            &router,
            WaitCondition::Response(ResponseWait {
                url: Some(TextMatcher::Exact("https://example.test/api".to_owned())),
                method: Some("POST".to_owned()),
                resource_type: Some("Fetch".to_owned()),
                status: Some(201),
            }),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        router.ingest(trace_event(
            1,
            1,
            [
                ("wait_kind", "response"),
                ("request_id", "request-1"),
                ("url", "https://example.test/api"),
                ("status", "201"),
                ("resource_type", "Fetch"),
            ],
        ));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        router.ingest(trace_event(
            2,
            2,
            [
                ("wait_kind", "request"),
                ("request_id", "request-1"),
                ("url", "https://example.test/api"),
                ("method", "POST"),
                ("resource_type", "Fetch"),
            ],
        ));
        match engine.poll(&mut registration, &router) {
            WaitOutcome::Matched { event, .. } => assert_eq!(event.sequence, EventSequence::new(1)),
            other => panic!("expected correlated response, got {other:?}"),
        }
    }

    #[test]
    fn network_idle_excludes_long_lived_download_and_analytics_traffic() {
        let mut router = EventRouter::empty();
        let clock = FakeClock::default();
        let mut engine = WaitEngine::new(clock.clone());
        let mut registration = engine.register(
            &router,
            WaitCondition::network_idle(5),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        for (sequence, url, resource_type, flags) in [
            (1, "wss://example.test/live", "WebSocket", "long_lived"),
            (2, "https://analytics.example/collect", "Fetch", "analytics"),
            (3, "https://example.test/file", "Document", "download"),
        ] {
            clock.set(Timestamp::new(sequence));
            router.ingest(trace_event(
                sequence,
                sequence,
                [
                    ("wait_kind", "request"),
                    ("request_id", "excluded"),
                    ("url", url),
                    ("resource_type", resource_type),
                    (flags, "true"),
                ],
            ));
            assert!(matches!(
                engine.poll(&mut registration, &router),
                WaitOutcome::Pending { .. }
            ));
        }
        clock.set(Timestamp::new(8));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Matched { .. }
        ));
    }

    #[test]
    fn stable_dom_requires_mutation_quiet_and_geometry_quiet() {
        let mut router = EventRouter::empty();
        let clock = FakeClock::default();
        let mut engine = WaitEngine::new(clock.clone());
        let mut registration = engine.register(
            &router,
            WaitCondition::StableDom(StableDomWait {
                quiet_for: 5,
                geometry_quiet_for: 5,
            }),
            Timestamp::new(100),
            CancellationToken::new(),
        );
        clock.set(Timestamp::new(1));
        router.ingest(trace_event(1, 1, [("wait_kind", "dom_mutation")]));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        clock.set(Timestamp::new(2));
        router.ingest(trace_event(
            2,
            2,
            [("wait_kind", "geometry"), ("geometry_signature", "a")],
        ));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        clock.set(Timestamp::new(3));
        router.ingest(trace_event(
            3,
            3,
            [("wait_kind", "geometry"), ("geometry_signature", "b")],
        ));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        clock.set(Timestamp::new(7));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Pending { .. }
        ));
        clock.set(Timestamp::new(8));
        assert!(matches!(
            engine.poll(&mut registration, &router),
            WaitOutcome::Matched { .. }
        ));
    }

    #[test]
    fn cancellation_deadline_generation_replacement_and_disconnect_are_terminal() {
        let mut router = EventRouter::empty();
        let clock = FakeClock::default();
        let mut engine = WaitEngine::new(clock.clone());
        let cancellation = CancellationToken::new();
        let mut cancelled = engine.register(
            &router,
            WaitCondition::url_contains("example"),
            Timestamp::new(10),
            cancellation.clone(),
        );
        cancellation.cancel();
        router.ingest(trace_event(
            1,
            1,
            [("wait_kind", "navigation"), ("url", "https://example")],
        ));
        assert!(matches!(
            engine.poll(&mut cancelled, &router),
            WaitOutcome::Cancelled { .. }
        ));

        let mut replaced = engine.register(
            &router,
            WaitCondition::page("page_one"),
            Timestamp::new(10),
            CancellationToken::new(),
        );
        router.ingest(trace_event(
            2,
            2,
            [("generation_replaced", "true"), ("page_id", "page_one")],
        ));
        assert!(matches!(
            engine.poll(&mut replaced, &router),
            WaitOutcome::ResyncRequired { .. }
        ));

        let mut disconnected = engine.register(
            &router,
            WaitCondition::page("page_one"),
            Timestamp::new(10),
            CancellationToken::new(),
        );
        router.ingest(trace_event(3, 3, [("disconnected", "true")]));
        assert!(matches!(
            engine.poll(&mut disconnected, &router),
            WaitOutcome::ResyncRequired { .. }
        ));

        let mut deadline = engine.register(
            &router,
            WaitCondition::url("never"),
            Timestamp::new(3),
            CancellationToken::new(),
        );
        clock.set(Timestamp::new(3));
        assert!(matches!(
            engine.poll(&mut deadline, &router),
            WaitOutcome::DeadlineExceeded { .. }
        ));
    }
}
