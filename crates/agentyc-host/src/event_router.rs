//! Bounded, scope-aware event routing over broker-sequenced records.
//!
//! The broker remains the durable source of truth. This module is a small
//! consumer-side replay/coalescing layer that never invents a scope for an
//! event and explicitly reports gaps as resynchronization requests.

use std::collections::{HashSet, VecDeque};

use agentyc_core::{
    BrokerEpoch, CoreError, EventCursor, EventId, EventKind, EventRecord, EventScope,
    EventSequence, GenerationWatermark, ResumeResult,
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
    /// The event had no attribution required by its logical scope.
    MissingAttribution,
    /// The event envelope or batch ordering was invalid.
    InvalidEvent,
    /// The broker sequence could not advance without overflowing.
    SequenceOverflow,
    /// A broker batch cursor was inconsistent with its events or router state.
    InvalidBatchCursor,
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
    /// IDs of recently accepted source events, including events coalesced out
    /// of the retained event list.
    seen_event_ids: HashSet<EventId>,
    seen_event_order: VecDeque<EventId>,
    limits: RouterLimits,
    resync_required: bool,
    resync_reason: Option<RouterResyncReason>,
}

impl EventRouter {
    /// Construct an empty router at a known broker epoch.
    pub fn new(broker_epoch: BrokerEpoch, limits: RouterLimits) -> Self {
        Self {
            broker_epoch,
            sequence: EventSequence::new(0),
            events: VecDeque::new(),
            retained_starts: VecDeque::new(),
            seen_event_ids: HashSet::new(),
            seen_event_order: VecDeque::new(),
            limits,
            resync_required: false,
            resync_reason: None,
        }
    }

    /// Construct a router at an existing broker cursor.
    pub fn new_at(cursor: EventCursor, limits: RouterLimits) -> Self {
        Self {
            broker_epoch: cursor.broker_epoch,
            sequence: cursor.sequence,
            events: VecDeque::new(),
            retained_starts: VecDeque::new(),
            seen_event_ids: HashSet::new(),
            seen_event_order: VecDeque::new(),
            limits,
            resync_required: false,
            resync_reason: None,
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
            oldest_sequence: self.retained_starts.front().copied(),
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
            return self.require_resync(RouterResyncReason::BrokerEpochChanged);
        }
        if self.seen_event_ids.contains(&event.event_id) {
            return RouterIngest::Accepted {
                cursor: self.cursor(),
            };
        }
        if self.resync_required {
            let reason = self.current_resync_reason();
            return self.require_resync(reason);
        }
        if event.validate_routing().is_err() {
            let reason = if event.requires_attribution() && event.metadata().is_none() {
                RouterResyncReason::MissingAttribution
            } else {
                RouterResyncReason::InvalidEvent
            };
            return self.require_resync(reason);
        }
        let Some(expected) = self.sequence.checked_next() else {
            return self.require_resync(RouterResyncReason::SequenceOverflow);
        };
        if event.sequence.get() != expected.get() {
            return self.require_resync(RouterResyncReason::SequenceGap);
        }
        self.sequence = event.sequence;
        if event.resync_required {
            self.resync_required = true;
            self.resync_reason = Some(RouterResyncReason::BrokerResync);
        }

        self.remember_event_id(event.event_id.clone());

        if self.limits.coalesce_dirty_events && is_coalescible(event.event) {
            let can_coalesce = self.events.back().is_some_and(|previous| {
                is_coalescible(previous.event)
                    && previous.event == event.event
                    && previous.scope == event.scope
                    && same_attribution(previous, &event)
            });
            if can_coalesce {
                if let Some(previous) = self.events.back_mut() {
                    let mut replacement = event;
                    let previous_count = coalesced_count(previous).unwrap_or(1);
                    let incoming_count = coalesced_count(&replacement).unwrap_or(1);
                    replacement.coalesced = true;
                    replacement.coalesced |= previous.coalesced;
                    replacement.dirty_reason = replacement.dirty_reason.or(previous.dirty_reason);
                    if let Some(metadata) = replacement.generation.metadata.as_mut() {
                        let merged_count = previous_count
                            .saturating_add(incoming_count)
                            .min(agentyc_core::events::EventMetadata::MAX_COALESCED_COUNT);
                        metadata.coalesced_count = Some(merged_count.max(2));
                    }
                    previous.clone_from(&replacement);
                }
                return RouterIngest::Accepted {
                    cursor: self.cursor(),
                };
            }
        }

        if self.limits.max_events == 0 {
            return self.require_resync(RouterResyncReason::RetainedHistoryLagged);
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

    /// Ingest a broker replay batch without bypassing epoch/order checks.
    pub fn ingest_batch(&mut self, batch: EventBatch) -> Result<RouterIngest, CoreError> {
        if batch.broker_epoch != self.broker_epoch || batch.cursor.broker_epoch != self.broker_epoch
        {
            return Ok(self.require_resync(RouterResyncReason::BrokerEpochChanged));
        }
        if batch.cursor.sequence.get() < self.sequence.get() {
            return Ok(self.require_resync(RouterResyncReason::InvalidBatchCursor));
        }
        if batch.result == ResumeResult::ResyncRequired {
            return Ok(self.require_resync(RouterResyncReason::BrokerResync));
        }

        let mut virtual_sequence = self.sequence;
        let mut previous_batch_sequence: Option<EventSequence> = None;
        for event in &batch.events {
            if event.broker_epoch != self.broker_epoch
                || event.sequence.get() == 0
                || event.sequence.get() > batch.cursor.sequence.get()
            {
                return Ok(self.require_resync(RouterResyncReason::InvalidBatchCursor));
            }
            if self.seen_event_ids.contains(&event.event_id) {
                continue;
            }
            if self.resync_required {
                let reason = self.current_resync_reason();
                return Ok(self.require_resync(reason));
            }
            if let Some(previous) = previous_batch_sequence
                && event.sequence.get() <= previous.get()
            {
                return Ok(self.require_resync(RouterResyncReason::InvalidBatchCursor));
            }
            previous_batch_sequence = Some(event.sequence);
            if event.validate_routing().is_err() {
                let reason = if event.requires_attribution() && event.metadata().is_none() {
                    RouterResyncReason::MissingAttribution
                } else {
                    RouterResyncReason::InvalidEvent
                };
                return Ok(self.require_resync(reason));
            }
            if event.sequence.get() <= virtual_sequence.get() {
                return Ok(self.require_resync(RouterResyncReason::InvalidBatchCursor));
            }
            if virtual_sequence.checked_next().is_none() {
                return Ok(self.require_resync(RouterResyncReason::SequenceOverflow));
            }
            virtual_sequence = event.sequence;
        }

        for event in batch.events {
            if event.broker_epoch == self.broker_epoch
                && self.seen_event_ids.contains(&event.event_id)
            {
                self.ingest(event);
                continue;
            }
            let Some(expected) = self.sequence.checked_next() else {
                return Ok(self.require_resync(RouterResyncReason::SequenceOverflow));
            };
            if event.sequence.get() > expected.get() {
                self.sequence = EventSequence::new(event.sequence.get() - 1);
            }
            let outcome = self.ingest(event);
            if matches!(outcome, RouterIngest::ResyncRequired { .. }) {
                return Ok(outcome);
            }
        }
        if !self.resync_required && batch.cursor.sequence.get() > self.sequence.get() {
            self.sequence = batch.cursor.sequence;
        }
        Ok(RouterIngest::Accepted {
            cursor: self.cursor(),
        })
    }

    /// Replay retained events after a cursor with an optional exact logical scope.
    pub fn replay(&self, after: EventCursor, scope: Option<&EventScope>) -> EventBatch {
        let cursor = self.cursor();
        if self.resync_required
            || after.broker_epoch != self.broker_epoch
            || after.sequence.get() > self.sequence.get()
            || self.retained_starts.front().is_some_and(|sequence| {
                after
                    .sequence
                    .checked_next()
                    .is_some_and(|next| next.get() < sequence.get())
            })
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
        self.resync_reason = Some(RouterResyncReason::Explicit);
    }

    /// Clear retained events and establish a new broker watermark after resync.
    pub fn reset(&mut self, cursor: EventCursor) {
        self.broker_epoch = cursor.broker_epoch;
        self.sequence = cursor.sequence;
        self.events.clear();
        self.retained_starts.clear();
        self.seen_event_ids.clear();
        self.seen_event_order.clear();
        self.resync_required = false;
        self.resync_reason = None;
    }

    fn current_resync_reason(&self) -> RouterResyncReason {
        self.resync_reason
            .unwrap_or(RouterResyncReason::BrokerResync)
    }

    fn require_resync(&mut self, reason: RouterResyncReason) -> RouterIngest {
        self.resync_required = true;
        self.resync_reason.get_or_insert(reason);
        RouterIngest::ResyncRequired {
            cursor: self.cursor(),
            reason: self.current_resync_reason(),
        }
    }

    fn remember_event_id(&mut self, event_id: EventId) {
        if !self.seen_event_ids.insert(event_id.clone()) {
            return;
        }
        self.seen_event_order.push_back(event_id);
        while self.seen_event_order.len() > self.limits.max_events.max(1) {
            if let Some(expired) = self.seen_event_order.pop_front() {
                self.seen_event_ids.remove(&expired);
            }
        }
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

fn same_attribution(left: &EventRecord, right: &EventRecord) -> bool {
    left.attribution() == right.attribution()
}

fn coalesced_count(event: &EventRecord) -> Option<u16> {
    event
        .metadata()
        .and_then(|metadata| metadata.coalesced_count)
}

fn scope_matches(event: &EventRecord, requested: &EventScope) -> bool {
    // A global resync/connection event carries no page data and is safe to
    // deliver to an affected scoped waiter. It is the explicit signal that the
    // waiter must stop replaying and refresh its logical state.
    if event.resync_required && event.scope.space_id.is_none() && event.scope.page_id.is_none() {
        return true;
    }
    // A broad ordinary event can be consumed by a broad request, but it must
    // never be guessed into a page-specific waiter.
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
        let metadata = agentyc_core::events::EventMetadata::for_event(
            kind,
            agentyc_core::events::EventAttribution::broker(),
        );
        EventRecord {
            protocol: agentyc_core::PROTOCOL_VERSION,
            event_id: EventId::from_suffix(format!("event-{sequence}")).expect("event identity"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(sequence),
            scope,
            event: kind,
            generation: GenerationWatermark {
                page_generation: Generation::new(sequence),
                metadata: Some(metadata),
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
    fn coalescing_preserves_reason_count_and_oldest_source_sequence() {
        let space = SpaceId::from_suffix("coalesce").expect("space");
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let mut first = event(
            1,
            EventScope::space(space.clone()),
            EventKind::PageChanged,
            "first",
        );
        first.dirty_reason = Some(agentyc_core::states::DirtyReason::DomMutation);
        let mut second = event(
            2,
            EventScope::space(space),
            EventKind::PageChanged,
            "second",
        );
        second.dirty_reason = Some(agentyc_core::states::DirtyReason::Navigation);
        router.ingest(first);
        router.ingest(second);

        let retained = router.retained();
        assert_eq!(retained.len(), 1);
        assert_eq!(
            retained[0].dirty_reason,
            Some(agentyc_core::states::DirtyReason::Navigation)
        );
        assert_eq!(
            retained[0]
                .metadata()
                .and_then(|metadata| metadata.coalesced_count),
            Some(2)
        );
        assert_eq!(
            router.watermark().oldest_sequence,
            Some(EventSequence::new(1))
        );
    }

    #[test]
    fn scoped_missing_attribution_fails_closed_until_reset() {
        let space = SpaceId::from_suffix("missing").expect("space");
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let mut missing = event(
            1,
            EventScope::space(space.clone()),
            EventKind::PageChanged,
            "missing",
        );
        missing.generation.metadata = None;
        assert!(matches!(
            router.ingest(missing),
            RouterIngest::ResyncRequired {
                reason: RouterResyncReason::MissingAttribution,
                ..
            }
        ));
        assert!(matches!(
            router.ingest(event(
                2,
                EventScope::space(space.clone()),
                EventKind::PageChanged,
                "new"
            )),
            RouterIngest::ResyncRequired { .. }
        ));
        router.reset(EventCursor {
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(0),
        });
        assert!(matches!(
            router.ingest(event(
                1,
                EventScope::space(space),
                EventKind::PageChanged,
                "reset"
            )),
            RouterIngest::Accepted { .. }
        ));
    }

    #[test]
    fn sequence_overflow_and_invalid_batch_cursor_require_resync() {
        let max = u64::MAX;
        let mut overflow = EventRouter::new_at(
            EventCursor {
                broker_epoch: BrokerEpoch::new(1),
                sequence: EventSequence::new(max),
            },
            RouterLimits::new(8),
        );
        assert!(matches!(
            overflow.ingest(event(
                max,
                EventScope {
                    space_id: None,
                    page_id: None,
                },
                EventKind::ConnectionChanged,
                "overflow"
            )),
            RouterIngest::ResyncRequired {
                reason: RouterResyncReason::SequenceOverflow,
                ..
            }
        ));

        let mut batch_router = EventRouter::empty();
        let result = batch_router
            .ingest_batch(EventBatch {
                broker_epoch: BrokerEpoch::new(1),
                result: ResumeResult::Accepted,
                events: vec![event(
                    2,
                    EventScope {
                        space_id: None,
                        page_id: None,
                    },
                    EventKind::ConnectionChanged,
                    "two",
                )],
                cursor: EventCursor {
                    broker_epoch: BrokerEpoch::new(1),
                    sequence: EventSequence::new(1),
                },
            })
            .expect("batch ingestion");
        assert!(matches!(
            result,
            RouterIngest::ResyncRequired {
                reason: RouterResyncReason::InvalidBatchCursor,
                ..
            }
        ));
    }

    #[test]
    fn replay_is_broadcast_and_does_not_consume_another_subscriber() {
        let mut router = EventRouter::empty();
        let scope = EventScope {
            space_id: None,
            page_id: None,
        };
        router.ingest(event(1, scope.clone(), EventKind::ConnectionChanged, "one"));
        router.ingest(event(2, scope, EventKind::ConnectionChanged, "two"));
        let cursor = EventCursor {
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(0),
        };
        let first = router.replay(cursor, None);
        let second = router.replay(cursor, None);
        assert_eq!(first.events, second.events);
        assert_eq!(first.events.len(), 2);
    }

    #[test]
    fn duplicate_event_id_does_not_redeliver_or_advance_watermark() {
        let scope = EventScope {
            space_id: None,
            page_id: None,
        };
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let original = event(1, scope.clone(), EventKind::ConnectionChanged, "original");
        let duplicate = EventRecord {
            sequence: EventSequence::new(2),
            payload: BTreeMap::from([(String::from("value"), String::from("retry"))]),
            ..original.clone()
        };

        assert!(matches!(
            router.ingest(original),
            RouterIngest::Accepted { .. }
        ));
        assert!(matches!(
            router.ingest(duplicate),
            RouterIngest::Accepted { .. }
        ));
        assert_eq!(router.cursor().sequence, EventSequence::new(1));
        assert_eq!(router.retained().len(), 1);
        assert_eq!(router.retained()[0].payload["value"], "original");
    }

    #[test]
    fn replay_batch_ignores_duplicate_ids_before_gap_adjustment() {
        let scope = EventScope {
            space_id: None,
            page_id: None,
        };
        let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
        let original = event(1, scope.clone(), EventKind::ConnectionChanged, "one");
        let duplicate = original.clone();
        let next = event(2, scope, EventKind::ConnectionChanged, "two");
        router.ingest(original);

        let outcome = router
            .ingest_batch(EventBatch {
                broker_epoch: BrokerEpoch::new(1),
                result: ResumeResult::Accepted,
                events: vec![duplicate, next],
                cursor: EventCursor {
                    broker_epoch: BrokerEpoch::new(1),
                    sequence: EventSequence::new(2),
                },
            })
            .expect("batch ingestion");

        assert!(matches!(outcome, RouterIngest::Accepted { .. }));
        assert_eq!(router.cursor().sequence, EventSequence::new(2));
        assert_eq!(router.retained().len(), 2);
        assert_eq!(router.retained()[0].sequence, EventSequence::new(1));
        assert_eq!(router.retained()[1].sequence, EventSequence::new(2));
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
