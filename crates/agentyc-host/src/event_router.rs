//! Bounded, scope-aware event routing over broker-sequenced records.
//!
//! The broker remains the durable source of truth. This module is a small
//! consumer-side replay/coalescing layer that never invents a scope for an
//! event and explicitly reports gaps as resynchronization requests.

use std::collections::VecDeque;

use agentyc_core::{
    BrokerEpoch, CoreError, EventCursor, EventKind, EventRecord, EventScope, EventSequence,
    GenerationWatermark, ResumeResult,
};
use serde::{Deserialize, Serialize};

use crate::events::{EventBatch, EventQuery};

/// Retention and coalescing bounds for an [`EventRouter`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RouterLimits {
    /// Maximum number of retained logical events.
    pub max_events: usize,
    /// Whether adjacent high-frequency page/snapshot events may coalesce.
    pub coalesce_dirty_events: bool,
}

impl RouterLimits {
    /// Construct explicit router limits.
    pub const fn new(max_events: usize) -> Self {
        Self {
            max_events,
            coalesce_dirty_events: true,
        }
    }

    /// Enable or disable deterministic dirty-event coalescing.
    #[must_use]
    pub const fn with_coalescing(mut self, enabled: bool) -> Self {
        self.coalesce_dirty_events = enabled;
        self
    }
}

impl Default for RouterLimits {
    fn default() -> Self {
        Self {
            max_events: 256,
            coalesce_dirty_events: true,
        }
    }
}

/// Current broker and retention watermarks exposed to wait registrations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RouterWatermark {
    /// Broker epoch owning the sequence.
    pub broker_epoch: BrokerEpoch,
    /// Highest broker sequence observed, including coalesced events.
    pub sequence: EventSequence,
    /// Oldest retained event sequence, if any.
    pub oldest_sequence: Option<EventSequence>,
    /// Whether a gap/epoch change requires a fresh logical read.
    pub resync_required: bool,
}

impl RouterWatermark {
    /// Convert the watermark to the replay cursor used by the broker contract.
    pub const fn cursor(self) -> EventCursor {
        EventCursor {
            broker_epoch: self.broker_epoch,
            sequence: self.sequence,
        }
    }
}

/// Reason an event consumer must resynchronize.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RouterResyncReason {
    /// The incoming event belongs to another broker epoch.
    BrokerEpochChanged,
    /// The incoming sequence skipped a broker watermark.
    SequenceGap,
    /// The requested cursor predates retained events.
    RetainedHistoryLagged,
    /// A broker batch already declared resynchronization.
    BrokerResync,
    /// The router was explicitly reset.
    Explicit,
}

/// Result of ingesting one broker event or batch.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum RouterIngest {
    /// The event was accepted; the contained cursor is current.
    Accepted {
        /// Current broker cursor.
        cursor: EventCursor,
    },
    /// The event stream cannot be replayed without a fresh snapshot.
    ResyncRequired {
        /// Current broker cursor, which is safe as a new registration point.
        cursor: EventCursor,
        /// Stable reason for the resync.
        reason: RouterResyncReason,
    },
}

/// A bounded event router with broker watermark tracking.
#[derive(Debug, Clone)]
pub struct EventRouter {
    broker_epoch: BrokerEpoch,
    sequence: EventSequence,
    events: VecDeque<EventRecord>,
    /// Earliest source sequence represented by each retained event. Coalescing
    /// keeps the earliest sequence so a waiter registered before the burst can
    /// still observe the coalesced result.
    retained_starts: VecDeque<EventSequence>,
    limits: RouterLimits,
    resync_required: bool,
}

impl EventRouter {
    /// Construct an empty router at a known broker epoch.
    pub fn new(broker_epoch: BrokerEpoch, limits: RouterLimits) -> Self {
        Self {
            broker_epoch,
            sequence: EventSequence::new(0),
            events: VecDeque::new(),
            retained_starts: VecDeque::new(),
            limits,
            resync_required: false,
        }
    }

    /// Construct a router at an existing broker cursor.
    pub fn new_at(cursor: EventCursor, limits: RouterLimits) -> Self {
        Self {
            broker_epoch: cursor.broker_epoch,
            sequence: cursor.sequence,
            events: VecDeque::new(),
            retained_starts: VecDeque::new(),
            limits,
            resync_required: false,
        }
    }

    /// Construct a router with default retention/coalescing limits.
    pub fn with_epoch(broker_epoch: BrokerEpoch) -> Self {
        Self::new(broker_epoch, RouterLimits::default())
    }

    /// Construct a router at broker epoch one.
    pub fn empty() -> Self {
        Self::with_epoch(BrokerEpoch::new(1))
    }

    /// Return configured router limits.
    pub const fn limits(&self) -> RouterLimits {
        self.limits
    }

    /// Return current broker/retention watermarks.
    pub fn watermark(&self) -> RouterWatermark {
        RouterWatermark {
            broker_epoch: self.broker_epoch,
            sequence: self.sequence,
            oldest_sequence: self.events.front().map(|event| event.sequence),
            resync_required: self.resync_required,
        }
    }

    /// Return the current broker watermark.
    pub fn broker_watermark(&self) -> RouterWatermark {
        self.watermark()
    }

    /// Return the current replay cursor.
    pub const fn cursor(&self) -> EventCursor {
        EventCursor {
            broker_epoch: self.broker_epoch,
            sequence: self.sequence,
        }
    }

    /// Return whether the router requires a fresh snapshot/watermark.
    pub const fn resync_required(&self) -> bool {
        self.resync_required
    }

    /// Ingest one broker-sequenced event.
    pub fn ingest(&mut self, event: EventRecord) -> RouterIngest {
        if event.broker_epoch != self.broker_epoch {
            self.resync_required = true;
            return RouterIngest::ResyncRequired {
                cursor: self.cursor(),
                reason: RouterResyncReason::BrokerEpochChanged,
            };
        }
        let expected = self.sequence.get().saturating_add(1);
        if event.sequence.get() != expected {
            self.resync_required = true;
            return RouterIngest::ResyncRequired {
                cursor: self.cursor(),
                reason: RouterResyncReason::SequenceGap,
            };
        }
        self.sequence = event.sequence;
        if event.resync_required {
            self.resync_required = true;
        }

        if self.limits.coalesce_dirty_events && is_coalescible(event.event) {
            let can_coalesce = self.events.back().is_some_and(|previous| {
                is_coalescible(previous.event)
                    && previous.event == event.event
                    && previous.scope == event.scope
            });
            if can_coalesce {
                if let Some(previous) = self.events.back_mut() {
                    let mut replacement = event;
                    replacement.coalesced = true;
                    replacement.coalesced |= previous.coalesced;
                    previous.clone_from(&replacement);
                }
                return RouterIngest::Accepted {
                    cursor: self.cursor(),
                };
            }
        }

        if self.limits.max_events == 0 {
            self.resync_required = true;
            return RouterIngest::ResyncRequired {
                cursor: self.cursor(),
                reason: RouterResyncReason::RetainedHistoryLagged,
            };
        }
        let sequence = event.sequence;
        self.events.push_back(event);
        self.retained_starts.push_back(sequence);
        while self.events.len() > self.limits.max_events {
            self.events.pop_front();
            self.retained_starts.pop_front();
        }
        RouterIngest::Accepted {
            cursor: self.cursor(),
        }
    }

    /// Alias for [`Self::ingest`].
    pub fn ingest_event(&mut self, event: EventRecord) -> RouterIngest {
        self.ingest(event)
    }

    /// Alias for [`Self::ingest`].
    pub fn publish(&mut self, event: EventRecord) -> RouterIngest {
        self.ingest(event)
    }

    /// Ingest a broker replay batch without bypassing epoch/gap checks.
    pub fn ingest_batch(&mut self, batch: EventBatch) -> Result<RouterIngest, CoreError> {
        if batch.broker_epoch != self.broker_epoch {
            self.resync_required = true;
            return Ok(RouterIngest::ResyncRequired {
                cursor: self.cursor(),
                reason: RouterResyncReason::BrokerEpochChanged,
            });
        }
        if batch.result == ResumeResult::ResyncRequired {
            self.resync_required = true;
            return Ok(RouterIngest::ResyncRequired {
                cursor: self.cursor(),
                reason: RouterResyncReason::BrokerResync,
            });
        }
        let mut outcome = RouterIngest::Accepted {
            cursor: self.cursor(),
        };
        for event in batch.events {
            // Broker batches may already be scope-filtered, so absent
            // sequences are not proof of a gap here. Direct `ingest` remains
            // strict and reports sequence gaps.
            if event.sequence.get() > self.sequence.get().saturating_add(1) {
                self.sequence = EventSequence::new(event.sequence.get().saturating_sub(1));
            }
            outcome = self.ingest(event);
            if matches!(outcome, RouterIngest::ResyncRequired { .. }) {
                break;
            }
        }
        if matches!(outcome, RouterIngest::Accepted { .. }) && batch.cursor.sequence > self.sequence
        {
            self.sequence = batch.cursor.sequence;
            outcome = RouterIngest::Accepted {
                cursor: self.cursor(),
            };
        }
        Ok(outcome)
    }

    /// Replay retained events after a cursor with an optional exact logical scope.
    pub fn replay(&self, after: EventCursor, scope: Option<&EventScope>) -> EventBatch {
        let cursor = self.cursor();
        if self.resync_required
            || after.broker_epoch != self.broker_epoch
            || after.sequence.get() > self.sequence.get()
            || self
                .retained_starts
                .front()
                .is_some_and(|sequence| after.sequence.get().saturating_add(1) < sequence.get())
        {
            return EventBatch {
                broker_epoch: self.broker_epoch,
                result: ResumeResult::ResyncRequired,
                events: Vec::new(),
                cursor,
            };
        }
        let events = self
            .events
            .iter()
            .filter(|event| event.sequence.get() > after.sequence.get())
            .filter(|event| scope.is_none_or(|requested| scope_matches(event, requested)))
            .cloned()
            .collect();
        EventBatch {
            broker_epoch: self.broker_epoch,
            result: ResumeResult::Accepted,
            events,
            cursor,
        }
    }

    /// Replay using the host broker's query type.
    pub fn resume(&self, query: &EventQuery) -> EventBatch {
        self.replay(query.after, query.scope.as_ref())
    }

    /// Alias suitable for event-driven waiter polling.
    pub fn poll(&self, after: EventCursor, scope: Option<&EventScope>) -> EventBatch {
        self.replay(after, scope)
    }

    /// Return a retained snapshot of the router's events in sequence order.
    pub fn retained(&self) -> Vec<EventRecord> {
        self.events.iter().cloned().collect()
    }

    /// Mark the router as needing a fresh logical read.
    pub fn request_resync(&mut self) {
        self.resync_required = true;
    }

    /// Clear retained events and establish a new broker watermark after resync.
    pub fn reset(&mut self, cursor: EventCursor) {
        self.broker_epoch = cursor.broker_epoch;
        self.sequence = cursor.sequence;
        self.events.clear();
        self.retained_starts.clear();
        self.resync_required = false;
    }

    /// Return whether an event's generation watermark is at least a target.
    pub fn generation_reached(event: &EventRecord, target: &GenerationWatermark) -> bool {
        generation_at_least(&event.generation, target)
    }
}

impl Default for EventRouter {
    fn default() -> Self {
        Self::empty()
    }
}

fn is_coalescible(kind: EventKind) -> bool {
    matches!(kind, EventKind::PageChanged | EventKind::SnapshotChanged)
}

fn scope_matches(event: &EventRecord, requested: &EventScope) -> bool {
    // A broad event can be consumed by a broad request, but it must never be
    // guessed into a page-specific waiter. Page-scoped events still match their
    // owning space request through the core logical scope rule.
    if requested.page_id.is_some()
        && (requested.space_id.is_none() || event.scope.page_id.is_none())
    {
        return false;
    }
    if requested.space_id.is_some() && event.scope.space_id.is_none() {
        return false;
    }
    if event.scope.page_id.is_some() && event.scope.space_id.is_none() {
        return false;
    }
    event.scope.matches(requested)
}

/// Compare event generation watermarks without inventing absent snapshot data.
pub fn generation_at_least(actual: &GenerationWatermark, target: &GenerationWatermark) -> bool {
    actual.space_generation.get() >= target.space_generation.get()
        && actual.page_generation.get() >= target.page_generation.get()
        && actual.navigation_generation.get() >= target.navigation_generation.get()
        && actual.document_generation.get() >= target.document_generation.get()
        && match (actual.snapshot_version, target.snapshot_version) {
            (_, None) => true,
            (Some(actual), Some(target)) => actual.get() >= target.get(),
            (None, Some(_)) => false,
        }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentyc_core::{EventId, Generation, PageId, SpaceId};
    use std::collections::BTreeMap;

    fn event(sequence: u64, scope: EventScope, kind: EventKind, payload: &str) -> EventRecord {
        EventRecord {
            protocol: agentyc_core::PROTOCOL_VERSION,
            event_id: EventId::from_suffix(format!("event-{sequence}")).expect("event identity"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(sequence),
            scope,
            event: kind,
            generation: GenerationWatermark {
                page_generation: Generation::new(sequence),
                ..GenerationWatermark::default()
            },
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::from([(String::from("value"), payload.to_owned())]),
        }
    }

    #[test]
    fn scoped_replay_never_routes_global_or_other_page_events() {
        let space = SpaceId::from_suffix("one").expect("space");
        let page = PageId::from_suffix("one").expect("page");
        let other = PageId::from_suffix("two").expect("page");
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        assert!(matches!(
            router.ingest(event(
                1,
                EventScope::space(space.clone()),
                EventKind::PageChanged,
                "one"
            )),
            RouterIngest::Accepted { .. }
        ));
        assert!(matches!(
            router.ingest(event(
                2,
                EventScope::page(space.clone(), other),
                EventKind::PageChanged,
                "other"
            )),
            RouterIngest::Accepted { .. }
        ));
        assert!(matches!(
            router.ingest(event(
                3,
                EventScope {
                    space_id: None,
                    page_id: None
                },
                EventKind::ConnectionChanged,
                "global"
            )),
            RouterIngest::Accepted { .. }
        ));
        let batch = router.replay(
            EventCursor {
                broker_epoch: BrokerEpoch::new(1),
                sequence: EventSequence::new(0),
            },
            Some(&EventScope::page(space, page)),
        );
        assert!(batch.events.is_empty());
    }

    #[test]
    fn adjacent_dirty_events_coalesce_to_the_latest_watermark() {
        let space = SpaceId::from_suffix("one").expect("space");
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        router.ingest(event(
            1,
            EventScope::space(space.clone()),
            EventKind::SnapshotChanged,
            "old",
        ));
        router.ingest(event(
            2,
            EventScope::space(space),
            EventKind::SnapshotChanged,
            "new",
        ));
        assert_eq!(router.retained().len(), 1);
        assert_eq!(router.retained()[0].sequence, EventSequence::new(2));
        assert!(router.retained()[0].coalesced);
        let replay = router.replay(
            EventCursor {
                broker_epoch: BrokerEpoch::new(1),
                sequence: EventSequence::new(0),
            },
            None,
        );
        assert_eq!(replay.result, ResumeResult::Accepted);
        assert_eq!(replay.events[0].sequence, EventSequence::new(2));
    }

    #[test]
    fn sequence_gaps_and_retention_lag_require_resync() {
        let mut gap_router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        assert!(matches!(
            gap_router.ingest(event(
                1,
                EventScope {
                    space_id: None,
                    page_id: None,
                },
                EventKind::ConnectionChanged,
                "one"
            )),
            RouterIngest::Accepted { .. }
        ));
        assert!(matches!(
            gap_router.ingest(event(
                3,
                EventScope {
                    space_id: None,
                    page_id: None,
                },
                EventKind::ConnectionChanged,
                "three"
            )),
            RouterIngest::ResyncRequired { .. }
        ));

        let mut retained = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(2));
        for sequence in 1..=3 {
            retained.ingest(event(
                sequence,
                EventScope {
                    space_id: None,
                    page_id: None,
                },
                EventKind::ConnectionChanged,
                "event",
            ));
        }
        let lagged = retained.replay(
            EventCursor {
                broker_epoch: BrokerEpoch::new(1),
                sequence: EventSequence::new(0),
            },
            None,
        );
        assert_eq!(lagged.result, ResumeResult::ResyncRequired);
        let accepted = retained.replay(
            EventCursor {
                broker_epoch: BrokerEpoch::new(1),
                sequence: EventSequence::new(1),
            },
            None,
        );
        assert_eq!(accepted.result, ResumeResult::Accepted);
        assert_eq!(accepted.events.len(), 2);
    }
}
