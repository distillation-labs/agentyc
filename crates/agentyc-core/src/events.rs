//! Broker-sequenced, scope-filtered event records.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::{
    errors::CoreError,
    ids::{
        BrokerEpoch, BrowserSessionEpoch, ConnectionEpoch, DocumentId, EventId, EventSequence,
        FrameId, Generation, NavigationId, PageId, ProfileBindingId, SnapshotVersion, SpaceId,
        WorkerInstanceEpoch,
    },
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
    /// A bounded liveness signal from an event source.
    #[serde(rename = "heartbeat")]
    Heartbeat,
}

/// Internal source that attributed an admitted event.
///
/// This is deliberately limited to logical sources. Raw browser target,
/// session, tab, and debugger identifiers never belong in an event record.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventSource {
    /// The host broker emitted the event from durable state.
    #[default]
    Broker,
    /// The browser extension bridge emitted the observation.
    Bridge,
    /// An authenticated local client requested the event.
    Client,
}

/// Typed internal attribution for an event source.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventAttribution {
    /// Logical source of the event.
    pub source: EventSource,
    /// Host connection epoch, when the source crossed a live connection.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub connection_epoch: Option<ConnectionEpoch>,
    /// Logical profile binding associated with the source.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile_binding_id: Option<ProfileBindingId>,
    /// Browser/profile session epoch observed by the extension.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub browser_session_epoch: Option<BrowserSessionEpoch>,
    /// Extension worker instance epoch observed by the bridge.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_instance_epoch: Option<WorkerInstanceEpoch>,
    /// Logical frame associated with the event, when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame_id: Option<FrameId>,
    /// Logical document associated with the event, when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub document_id: Option<DocumentId>,
    /// Logical navigation associated with the event, when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub navigation_id: Option<NavigationId>,
}

impl EventAttribution {
    /// Construct attribution for a host-generated event.
    pub const fn broker() -> Self {
        Self {
            source: EventSource::Broker,
            connection_epoch: None,
            profile_binding_id: None,
            browser_session_epoch: None,
            worker_instance_epoch: None,
            frame_id: None,
            document_id: None,
            navigation_id: None,
        }
    }

    /// Construct attribution for an authenticated client event.
    pub fn client(
        connection_epoch: ConnectionEpoch,
        profile_binding_id: Option<ProfileBindingId>,
    ) -> Self {
        Self {
            source: EventSource::Client,
            connection_epoch: Some(connection_epoch),
            profile_binding_id,
            ..Self::broker()
        }
    }

    /// Construct attribution for an extension bridge event.
    pub fn bridge(
        connection_epoch: ConnectionEpoch,
        profile_binding_id: Option<ProfileBindingId>,
        browser_session_epoch: Option<BrowserSessionEpoch>,
        worker_instance_epoch: Option<WorkerInstanceEpoch>,
    ) -> Self {
        Self {
            source: EventSource::Bridge,
            connection_epoch: Some(connection_epoch),
            profile_binding_id,
            browser_session_epoch,
            worker_instance_epoch,
            ..Self::broker()
        }
    }

    /// Validate attribution against the event's logical scope.
    pub fn validate_for_scope(&self, scope: &EventScope) -> Result<(), CoreError> {
        if self.connection_epoch.is_some_and(|epoch| epoch.get() == 0)
            || self
                .browser_session_epoch
                .is_some_and(|epoch| epoch.get() == 0)
            || self
                .worker_instance_epoch
                .is_some_and(|epoch| epoch.get() == 0)
        {
            return Err(CoreError::invalid_argument(
                "event attribution contains a zero epoch",
            ));
        }
        if matches!(self.source, EventSource::Bridge | EventSource::Client)
            && self.connection_epoch.is_none()
        {
            return Err(CoreError::invalid_argument(
                "connected event attribution requires a connection epoch",
            ));
        }
        if (self.frame_id.is_some() || self.document_id.is_some() || self.navigation_id.is_some())
            && scope.page_id.is_none()
        {
            return Err(CoreError::invalid_argument(
                "frame, document, and navigation attribution requires a page scope",
            ));
        }
        Ok(())
    }
}

/// Typed liveness metadata for a heartbeat event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventHeartbeat {
    /// Source that emitted the heartbeat.
    pub source: EventSource,
}

/// Optional internal metadata carried by an event generation watermark.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventMetadata {
    /// Typed source attribution.
    pub attribution: EventAttribution,
    /// Heartbeat marker, present only for [`EventKind::Heartbeat`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heartbeat: Option<EventHeartbeat>,
    /// Number of source events represented by a coalesced event.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coalesced_count: Option<u16>,
}

impl EventMetadata {
    /// Maximum number of source events represented by one retained record.
    pub const MAX_COALESCED_COUNT: u16 = 1_024;

    /// Construct metadata appropriate for an event kind.
    pub fn for_event(event: EventKind, attribution: EventAttribution) -> Self {
        let heartbeat = matches!(event, EventKind::Heartbeat).then_some(EventHeartbeat {
            source: attribution.source,
        });
        Self {
            attribution,
            heartbeat,
            coalesced_count: None,
        }
    }

    /// Validate metadata against the event kind, scope, and coalescing marker.
    pub fn validate(
        &self,
        event: EventKind,
        scope: &EventScope,
        coalesced: bool,
    ) -> Result<(), CoreError> {
        self.attribution.validate_for_scope(scope)?;
        if self
            .coalesced_count
            .is_some_and(|count| !(2..=Self::MAX_COALESCED_COUNT).contains(&count))
        {
            return Err(CoreError::invalid_argument(
                "event coalesced count is outside its bound",
            ));
        }
        if self.coalesced_count.is_some() && !coalesced {
            return Err(CoreError::invalid_argument(
                "event coalesced count requires a coalesced event",
            ));
        }
        match (event, self.heartbeat) {
            (EventKind::Heartbeat, Some(heartbeat))
                if heartbeat.source == self.attribution.source => {}
            (EventKind::Heartbeat, _) => {
                return Err(CoreError::invalid_argument(
                    "heartbeat events require matching heartbeat metadata",
                ));
            }
            (_, Some(_)) => {
                return Err(CoreError::invalid_argument(
                    "heartbeat metadata is only valid for heartbeat events",
                ));
            }
            (_, None) => {}
        }
        Ok(())
    }
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
    /// Internal attribution and bounded event metadata.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<EventMetadata>,
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

    /// Return the internal metadata, when the event carries it.
    pub fn metadata(&self) -> Option<&EventMetadata> {
        self.generation.metadata.as_ref()
    }

    /// Return the internal attribution, when the event carries it.
    pub fn attribution(&self) -> Option<&EventAttribution> {
        self.metadata().map(|metadata| &metadata.attribution)
    }

    /// Return whether this record is a typed heartbeat.
    pub const fn is_heartbeat(&self) -> bool {
        matches!(self.event, EventKind::Heartbeat)
    }

    /// Return whether routing requires internal attribution for this record.
    pub const fn requires_attribution(&self) -> bool {
        self.scope.space_id.is_some() || self.scope.page_id.is_some() || self.is_heartbeat()
    }

    /// Validate an event produced by a broker admission path.
    pub fn validate_admission(&self) -> Result<(), CoreError> {
        self.validate_routing()?;
        if self.metadata().is_none() {
            return Err(CoreError::invalid_argument(
                "admitted event is missing internal attribution",
            ));
        }
        Ok(())
    }

    /// Validate an event before consumer-side routing.
    pub fn validate_routing(&self) -> Result<(), CoreError> {
        self.validate_scope()?;
        if self.protocol != crate::protocol::PROTOCOL_VERSION {
            return Err(CoreError::invalid_argument(
                "event protocol version is unsupported",
            ));
        }
        if self.broker_epoch.get() == 0 || self.sequence.get() == 0 {
            return Err(CoreError::invalid_argument(
                "event epoch and sequence must be positive",
            ));
        }
        match self.metadata() {
            Some(metadata) => metadata.validate(self.event, &self.scope, self.coalesced),
            None if self.requires_attribution() => Err(CoreError::invalid_argument(
                "scoped event is missing internal attribution",
            )),
            None => Ok(()),
        }
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

    #[test]
    fn heartbeat_metadata_is_typed_and_backward_serializable() {
        let record = EventRecord {
            protocol: crate::protocol::PROTOCOL_VERSION,
            event_id: EventId::from_suffix("heartbeat").expect("valid event"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(1),
            scope: EventScope {
                space_id: None,
                page_id: None,
            },
            event: EventKind::Heartbeat,
            generation: GenerationWatermark {
                metadata: Some(EventMetadata::for_event(
                    EventKind::Heartbeat,
                    EventAttribution::broker(),
                )),
                ..GenerationWatermark::default()
            },
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::<String, String>::new(),
        };
        record.validate_admission().expect("typed heartbeat");
        assert!(record.is_heartbeat());
        let encoded = serde_json::to_value(&record).expect("serialize event");
        let decoded: EventRecord = serde_json::from_value(encoded).expect("deserialize event");
        assert_eq!(decoded.metadata(), record.metadata());
    }

    #[test]
    fn scoped_events_reject_missing_attribution() {
        let space = SpaceId::from_suffix("one").expect("space");
        let record = EventRecord {
            protocol: crate::protocol::PROTOCOL_VERSION,
            event_id: EventId::from_suffix("missing-attribution").expect("valid event"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(1),
            scope: EventScope::space(space),
            event: EventKind::PageChanged,
            generation: GenerationWatermark::default(),
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::<String, String>::new(),
        };
        assert!(record.validate_routing().is_err());
    }
}
