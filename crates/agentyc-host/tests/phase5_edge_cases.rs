use std::collections::BTreeMap;

use agentyc_core::events::{EventAttribution, EventMetadata};
use agentyc_core::{
    BrokerEpoch, EventId, EventKind, EventRecord, EventScope, EventSequence, Generation,
    GenerationWatermark, PROTOCOL_VERSION, SpaceId, Timestamp,
};
use agentyc_host::{
    CancellationToken, EventRouter, FakeClock, RouterIngest, RouterLimits, WaitCondition,
    WaitEngine, WaitOutcome,
};

fn event(sequence: u64, scope: EventScope, kind: EventKind, page_generation: u64) -> EventRecord {
    EventRecord {
        protocol: PROTOCOL_VERSION,
        event_id: EventId::from_suffix(format!("phase5-event-{sequence}")).expect("event identity"),
        broker_epoch: BrokerEpoch::new(1),
        sequence: EventSequence::new(sequence),
        scope,
        event: kind,
        generation: GenerationWatermark {
            page_generation: Generation::new(page_generation),
            metadata: Some(EventMetadata::for_event(kind, EventAttribution::broker())),
            ..GenerationWatermark::default()
        },
        dirty_reason: None,
        coalesced: false,
        resync_required: false,
        payload: BTreeMap::from([(String::from("state"), String::from("ready"))]),
    }
}

fn space(suffix: &str) -> SpaceId {
    SpaceId::from_suffix(suffix).expect("space identity")
}

#[test]
fn wait_registration_observes_only_events_after_its_watermark() {
    let first_space = space("first");
    let mut router = EventRouter::new(BrokerEpoch::new(1), RouterLimits::new(8));
    assert!(matches!(
        router.ingest(event(
            1,
            EventScope::space(first_space.clone()),
            EventKind::ConnectionChanged,
            1,
        )),
        RouterIngest::Accepted { .. }
    ));

    let mut engine = WaitEngine::new(FakeClock::new(Timestamp::new(0)));
    let mut registration = engine.register_scoped(
        &router,
        WaitCondition::event_kind(EventKind::PageChanged),
        Some(EventScope::space(first_space.clone())),
        Timestamp::new(100),
        CancellationToken::new(),
    );
    assert!(matches!(
        engine.poll(&mut registration, &router),
        WaitOutcome::Pending { .. }
    ));

    router.ingest(event(
        2,
        EventScope::space(first_space),
        EventKind::PageChanged,
        2,
    ));
    match engine.poll(&mut registration, &router) {
        WaitOutcome::Matched { event, .. } => {
            assert_eq!(event.sequence, EventSequence::new(2));
            assert_eq!(event.generation.page_generation, Generation::new(2));
        }
        other => panic!("expected post-registration match, got {other:?}"),
    }
}

#[test]
fn cancellation_wins_over_a_matching_event_while_wait_is_active() {
    let cancellation = CancellationToken::new();
    let mut router = EventRouter::empty();
    let mut engine = WaitEngine::new(FakeClock::default());
    let mut registration = engine.register(
        &router,
        WaitCondition::event_kind(EventKind::PageChanged),
        Timestamp::new(100),
        cancellation.clone(),
    );

    cancellation.cancel();
    router.ingest(event(
        1,
        EventScope {
            space_id: None,
            page_id: None,
        },
        EventKind::PageChanged,
        1,
    ));

    let outcome = engine.poll(&mut registration, &router);
    assert!(matches!(outcome, WaitOutcome::Cancelled { .. }));
    assert_eq!(
        WaitEngine::<FakeClock>::outcome_error(&outcome)
            .expect("cancel error")
            .code,
        agentyc_core::ErrorCode::Cancelled
    );
}

#[test]
fn absolute_deadline_wins_over_an_event_at_the_deadline() {
    let clock = FakeClock::new(Timestamp::new(0));
    let mut engine = WaitEngine::new(clock.clone());
    let mut router = EventRouter::empty();
    let mut registration = engine.register(
        &router,
        WaitCondition::event_kind(EventKind::PageChanged),
        Timestamp::new(5),
        CancellationToken::new(),
    );

    clock.advance_to(Timestamp::new(5));
    router.ingest(event(
        1,
        EventScope {
            space_id: None,
            page_id: None,
        },
        EventKind::PageChanged,
        1,
    ));

    let outcome = engine.poll(&mut registration, &router);
    assert!(matches!(outcome, WaitOutcome::DeadlineExceeded { .. }));
    assert_eq!(
        WaitEngine::<FakeClock>::outcome_error(&outcome)
            .expect("timeout error")
            .code,
        agentyc_core::ErrorCode::Timeout
    );
}

#[test]
fn scope_filtering_does_not_route_another_space_into_a_waiter() {
    let first_space = space("first");
    let second_space = space("second");
    let mut router = EventRouter::empty();
    let mut engine = WaitEngine::new(FakeClock::default());
    let mut registration = engine.register_scoped(
        &router,
        WaitCondition::event_kind(EventKind::PageChanged),
        Some(EventScope::space(first_space.clone())),
        Timestamp::new(100),
        CancellationToken::new(),
    );

    router.ingest(event(
        1,
        EventScope::space(second_space),
        EventKind::PageChanged,
        1,
    ));
    assert!(matches!(
        engine.poll(&mut registration, &router),
        WaitOutcome::Pending { .. }
    ));

    router.ingest(event(
        2,
        EventScope::space(first_space),
        EventKind::PageChanged,
        1,
    ));
    assert!(matches!(
        engine.poll(&mut registration, &router),
        WaitOutcome::Matched { .. }
    ));
}

#[test]
fn sequence_gap_requires_resync_instead_of_matching_a_waiter() {
    let mut router = EventRouter::empty();
    let mut engine = WaitEngine::new(FakeClock::default());
    let mut registration = engine.register(
        &router,
        WaitCondition::event_kind(EventKind::PageChanged),
        Timestamp::new(100),
        CancellationToken::new(),
    );

    assert!(matches!(
        router.ingest(event(
            2,
            EventScope {
                space_id: None,
                page_id: None,
            },
            EventKind::PageChanged,
            1,
        )),
        RouterIngest::ResyncRequired { .. }
    ));
    let outcome = engine.poll(&mut registration, &router);
    assert!(matches!(outcome, WaitOutcome::ResyncRequired { .. }));
    assert_eq!(
        WaitEngine::<FakeClock>::outcome_error(&outcome)
            .expect("resync error")
            .code,
        agentyc_core::ErrorCode::EventLagged
    );
}

#[test]
fn generation_waits_require_every_generation_dimension() {
    let target = GenerationWatermark {
        page_generation: Generation::new(2),
        document_generation: Generation::new(3),
        ..GenerationWatermark::default()
    };
    let lower = event(
        1,
        EventScope {
            space_id: None,
            page_id: None,
        },
        EventKind::PageChanged,
        2,
    );
    assert!(!WaitCondition::GenerationAtLeast(target.clone()).matches(&lower));

    let mut higher = lower;
    higher.sequence = EventSequence::new(2);
    higher.event_id = EventId::from_suffix("phase5-event-higher").expect("event identity");
    higher.generation.document_generation = Generation::new(3);
    assert!(WaitCondition::GenerationAtLeast(target).matches(&higher));
    assert!(EventRouter::generation_reached(&higher, &higher.generation));
}

#[test]
fn heartbeat_replay_keeps_typed_source_metadata() {
    let mut router = EventRouter::empty();
    let heartbeat = event(
        1,
        EventScope {
            space_id: None,
            page_id: None,
        },
        EventKind::Heartbeat,
        0,
    );
    assert!(matches!(
        router.ingest(heartbeat),
        RouterIngest::Accepted { .. }
    ));
    let replay = router.replay(
        agentyc_core::EventCursor {
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(0),
        },
        None,
    );
    assert_eq!(replay.events.len(), 1);
    assert_eq!(
        replay.events[0]
            .metadata()
            .and_then(|metadata| metadata.heartbeat)
            .map(|heartbeat| heartbeat.source),
        Some(agentyc_core::events::EventSource::Broker)
    );
}

#[test]
fn scoped_missing_attribution_is_not_routed_by_fallback() {
    let mut router = EventRouter::empty();
    let mut missing = event(
        1,
        EventScope::space(space("attribution")),
        EventKind::PageChanged,
        1,
    );
    missing.generation.metadata = None;
    assert!(matches!(
        router.ingest(missing),
        RouterIngest::ResyncRequired {
            reason: agentyc_host::RouterResyncReason::MissingAttribution,
            ..
        }
    ));
}
