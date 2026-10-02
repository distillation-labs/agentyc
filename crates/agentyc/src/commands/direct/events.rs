use agentyc_core::{BrokerEpoch, EventCursor, EventScope, EventSequence, ResumeResult};
use agentyc_host::EventQuery;
use serde_json::{Value, json};

use super::{DirectContext, DirectResult, EventsArgs, host_error, parse_page, parse_space};

pub(super) fn run(context: &DirectContext, args: EventsArgs) -> DirectResult<Value> {
    let current_epoch = context.broker.broker_epoch().map_err(host_error)?;
    let after = EventCursor {
        broker_epoch: BrokerEpoch::new(args.after_epoch.unwrap_or(current_epoch.get())),
        sequence: EventSequence::new(args.after_sequence),
    };
    let scope = match (args.space_id.as_deref(), args.page_id.as_deref()) {
        (None, Some(_)) => {
            return Err(agentyc_core::CoreError::invalid_argument(
                "--page-id requires --space-id",
            ));
        }
        (Some(space), page) => Some(EventScope {
            space_id: Some(parse_space(space)?),
            page_id: page.map(parse_page).transpose()?,
        }),
        (None, None) => None,
    };
    let batch = context
        .broker
        .resume_events(&context.authority, EventQuery { after, scope })
        .map_err(host_error)?;
    let limit = args.limit.min(1_024);
    let events: Vec<_> = batch.events.into_iter().take(limit).collect();
    Ok(json!({
        "broker_epoch": batch.broker_epoch,
        "cursor": batch.cursor,
        "resume": match batch.result {
            ResumeResult::Accepted => "accepted",
            ResumeResult::ResyncRequired => "resync_required",
        },
        "events": events,
    }))
}
