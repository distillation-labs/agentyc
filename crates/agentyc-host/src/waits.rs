//! Deterministic event-driven wait registration and polling.
//!
//! Waiters capture a router cursor before work begins and only inspect events
//! after that cursor. Callers drive polling from their event loop; this module
//! never sleeps or guesses that time has advanced. The host core does not read
//! an OS clock; a later adapter must supply a trusted [`Clock`] implementation.

use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicU64, Ordering},
};

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
#[derive(Debug, Clone, Default)]
pub struct CancellationToken {
    cancelled: Arc<AtomicBool>,
}

impl CancellationToken {
    /// Construct a non-cancelled token.
    pub fn new() -> Self {
        Self::default()
    }

    /// Cancel all registrations holding a clone of this token.
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }

    /// Return whether cancellation has been requested.
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::SeqCst)
    }

    /// Alias for [`Self::is_cancelled`].
    pub fn cancelled(&self) -> bool {
        self.is_cancelled()
    }
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
}

impl WaitCondition {
    /// Construct an event-kind condition.
    pub const fn event_kind(kind: EventKind) -> Self {
        Self::EventKind(kind)
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
        }
    }
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
    finished: bool,
}

/// Alias used by callers that treat a registration as a handle.
pub type WaitHandle = WaitRegistration;

/// Poll result for a registered event-driven wait.
#[derive(Debug, Clone, PartialEq, Eq)]
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
        if self.now().get() >= registration.deadline.get() {
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
            if event.requires_resync() {
                registration.finished = true;
                return WaitOutcome::ResyncRequired {
                    cursor: registration.cursor,
                };
            }
            if registration.condition.matches(&event) {
                registration.finished = true;
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
}
