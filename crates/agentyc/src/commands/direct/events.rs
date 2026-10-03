use std::collections::BTreeMap;

use agentyc_core::{BrokerEpoch, EventCursor, EventScope, EventSequence, ResumeResult};
use agentyc_host::EventQuery;
use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, EventsArgs, host_error, parse_page, parse_space, remote_field,
    remote_string,
};

pub(super) fn run(context: &DirectContext, args: EventsArgs) -> DirectResult<Value> {
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
    let limit = args.limit.min(1_024);

    if let Some((broker, authority)) = context.local() {
        let current_epoch = broker.broker_epoch().map_err(host_error)?;
        let after = EventCursor {
            broker_epoch: BrokerEpoch::new(args.after_epoch.unwrap_or(current_epoch.get())),
            sequence: EventSequence::new(args.after_sequence),
        };
        let batch = broker
            .resume_events(authority, EventQuery { after, scope })
            .map_err(host_error)?;
        let events: Vec<_> = batch.events.into_iter().take(limit).collect();
        return Ok(json!({
            "broker_epoch": batch.broker_epoch,
            "cursor": batch.cursor,
            "resume": match batch.result {
                ResumeResult::Accepted => "accepted",
                ResumeResult::ResyncRequired => "resync_required",
            },
            "events": events,
        }));
    }

    let mut params = BTreeMap::from([
        ("after_sequence".to_owned(), args.after_sequence.to_string()),
        ("limit".to_owned(), limit.to_string()),
    ]);
    if let Some(after_epoch) = args.after_epoch {
        params.insert("after_epoch".to_owned(), after_epoch.to_string());
    }
    if let Some(scope) = scope {
        if let Some(space_id) = scope.space_id {
            params.insert("space_id".to_owned(), space_id.to_string());
        }
        if let Some(page_id) = scope.page_id {
            params.insert("page_id".to_owned(), page_id.to_string());
        }
    }
    let response = context.request("events.resume", params)?;
    Ok(json!({
        "broker_epoch": remote_field(&response, "broker_epoch")?,
        "cursor": remote_field(&response, "cursor")?,
        "resume": remote_string(&response, "resume")?,
        "events": remote_field(&response, "events")?,
    }))
}
