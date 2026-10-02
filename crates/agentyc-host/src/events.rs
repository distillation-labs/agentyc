//! Broker event resume and scope-filtering result types.

use agentyc_core::{EventCursor, EventRecord, EventScope, ResumeResult};

/// A bounded event query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventQuery {
    /// Cursor after which events are requested.
    pub after: EventCursor,
    /// Optional logical scope filter.
    pub scope: Option<EventScope>,
}

impl EventQuery {
    /// Query all scopes after a cursor.
    pub fn all(after: EventCursor) -> Self {
        Self { after, scope: None }
    }

    /// Query one logical scope after a cursor.
    pub fn scoped(after: EventCursor, scope: EventScope) -> Self {
        Self {
            after,
            scope: Some(scope),
        }
    }
}

/// Result of a resume request. `ResyncRequired` intentionally carries no
/// guessed replay; callers must perform a fresh logical read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventBatch {
    /// Broker epoch owning the returned cursor.
    pub broker_epoch: agentyc_core::BrokerEpoch,
    /// Whether replay is accepted or a fresh snapshot is required.
    pub result: ResumeResult,
    /// Events after the cursor that match the requested scope.
    pub events: Vec<EventRecord>,
    /// Last sequence observed by the broker.
    pub cursor: EventCursor,
}
