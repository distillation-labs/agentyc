//! Broker-sequenced, scope-filtered event records.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::{
    errors::CoreError,
    ids::{BrokerEpoch, EventId, EventSequence, Generation, PageId, SnapshotVersion, SpaceId},
    states::DirtyReason,
};

/// Event categories understood by all protocol consumers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    /// A logical space lifecycle or ownership field changed.
    #[serde(rename = "space.changed")]
    SpaceChanged,
    /// A logical page lifecycle, binding, or metadata field changed.
    #[serde(rename = "page.changed")]
    PageChanged,
    /// A lease epoch or lease state changed.
    #[serde(rename = "lease.changed")]
    LeaseChanged,
    /// An action receipt changed state.
    #[serde(rename = "action.changed")]
    ActionChanged,
    /// A snapshot cache or provenance changed.
    #[serde(rename = "snapshot.changed")]
    SnapshotChanged,
    /// The broker is draining and will not admit new work.
    #[serde(rename = "broker.draining")]
    BrokerDraining,
    /// The connection or bridge lifecycle changed.
    #[serde(rename = "connection.changed")]
    ConnectionChanged,
}

/// Logical scope used for event filtering.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventScope {
    /// Optional logical space scope.
    pub space_id: Option<SpaceId>,
    /// Optional logical page scope.
    pub page_id: Option<PageId>,
}

impl EventScope {
    /// Validate that a page scope includes its owning space.
    pub fn validate(&self) -> Result<(), CoreError> {
        if self.page_id.is_some() && self.space_id.is_none() {
            return Err(CoreError::invalid_argument(
                "event page scope requires a space scope",
            ));
        }
        Ok(())
    }

    /// Scope an event to a space.
    pub fn space(space_id: SpaceId) -> Self {
        Self {
            space_id: Some(space_id),
            page_id: None,
        }
    }

    /// Scope an event to one page and its owning space.
    pub fn page(space_id: SpaceId, page_id: PageId) -> Self {
        Self {
            space_id: Some(space_id),
            page_id: Some(page_id),
        }
    }

    /// Return whether this event can be delivered to the requested scope.
    pub fn matches(&self, requested: &Self) -> bool {
        if self.validate().is_err() || requested.validate().is_err() {
            return false;
        }
        let space_matches = requested
            .space_id
            .as_ref()
            .is_none_or(|id| self.space_id.as_ref() == Some(id));
        let page_matches = requested
            .page_id
            .as_ref()
            .is_none_or(|id| self.page_id.as_ref() == Some(id));
        space_matches && page_matches
    }
}

/// Generation watermark attached to an event.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct GenerationWatermark {
    /// Logical space generation.
    pub space_generation: Generation,
    /// Logical page generation.
    pub page_generation: Generation,
    /// Navigation generation.
    pub navigation_generation: Generation,
    /// Document generation.
    pub document_generation: Generation,
    /// Snapshot version, when the event has snapshot provenance.
    pub snapshot_version: Option<SnapshotVersion>,
}

/// One event in the broker's monotonically sequenced stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventRecord<P = BTreeMap<String, String>> {
    /// Protocol version for this event envelope.
    pub protocol: u16,
    /// Opaque event identity.
    pub event_id: EventId,
    /// Broker epoch that issued the sequence.
    pub broker_epoch: BrokerEpoch,
    /// Monotonic sequence within the broker epoch.
    pub sequence: EventSequence,
    /// Scope used by the broker for filtering.
    pub scope: EventScope,
    /// Event category.
    pub event: EventKind,
    /// Generations observed with the event.
    pub generation: GenerationWatermark,
    /// Why the event was emitted, when applicable.
    pub dirty_reason: Option<DirtyReason>,
    /// Whether multiple source changes were coalesced.
    pub coalesced: bool,
    /// Whether the consumer must request a fresh snapshot/watermark.
    pub resync_required: bool,
    /// Transport-neutral event payload.
    pub payload: P,
}

impl<P> EventRecord<P> {
    /// Validate the event's logical scope before filtering or delivery.
    pub fn validate_scope(&self) -> Result<(), CoreError> {
        self.scope.validate()
    }

    /// Return whether a resume cursor is strictly behind this event.
    pub const fn is_after(&self, sequence: EventSequence) -> bool {
        self.sequence.get() > sequence.get()
    }

    /// Return whether this event requires a resynchronization read.
    pub const fn requires_resync(&self) -> bool {
        self.resync_required
    }
}

/// Resume cursor for a broker event stream.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventCursor {
    /// Broker epoch that owns the sequence.
    pub broker_epoch: BrokerEpoch,
    /// Last fully processed sequence.
    pub sequence: EventSequence,
}

impl EventCursor {
    /// Return whether an event can be resumed after this cursor.
    pub const fn accepts(&self, event: &EventCursor) -> bool {
        self.broker_epoch.get() == event.broker_epoch.get()
            && event.sequence.get() > self.sequence.get()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{EventId, PageId, SpaceId};

    #[test]
    fn scope_matching_is_logical_and_deterministic() {
        let space = SpaceId::from_suffix("one").expect("valid space");
        let page = PageId::from_suffix("one").expect("valid page");
        let event_scope = EventScope::page(space.clone(), page);
        assert!(event_scope.matches(&EventScope::space(space)));
        assert!(!event_scope.matches(&EventScope::space(
            SpaceId::from_suffix("two").expect("valid space"),
        )));
    }

    #[test]
    fn page_scope_requires_space_scope() {
        let invalid = EventScope {
            space_id: None,
            page_id: Some(PageId::from_suffix("one").expect("page")),
        };
        assert!(invalid.validate().is_err());
        assert!(!invalid.matches(&EventScope {
            space_id: None,
            page_id: None,
        }));
    }

    #[test]
    fn event_sequence_is_monotonic_within_an_epoch() {
        let record = EventRecord {
            protocol: crate::protocol::PROTOCOL_VERSION,
            event_id: EventId::from_suffix("one").expect("valid event"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(2),
            scope: EventScope {
                space_id: None,
                page_id: None,
            },
            event: EventKind::ConnectionChanged,
            generation: GenerationWatermark::default(),
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::<String, String>::new(),
        };
        assert!(record.is_after(EventSequence::new(1)));
        assert!(!record.is_after(EventSequence::new(2)));
    }
}
