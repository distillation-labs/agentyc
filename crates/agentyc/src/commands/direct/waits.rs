use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::{DirectContext, DirectResult, WaitArgs, parse_page, parse_space, remote_field};

/// Dispatch one direct wait through the host's canonical `wait.for` method.
pub(super) fn run(context: &DirectContext, args: WaitArgs) -> DirectResult<Value> {
    let condition = serde_json::from_str::<Value>(&args.condition).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!("invalid wait condition: {error}"))
    })?;
    if !condition.is_object() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "wait condition must be a JSON object",
        ));
    }

    let space_id = args.space_id.as_deref().map(parse_space).transpose()?;
    let page_id = args.page_id.as_deref().map(parse_page).transpose()?;
    if page_id.is_some() && space_id.is_none() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "page_id requires space_id",
        ));
    }

    let mut params = BTreeMap::from([
        ("condition".to_owned(), args.condition),
        ("timeout_ms".to_owned(), args.timeout_ms.to_string()),
    ]);
    if let Some(after_epoch) = args.after_epoch {
        params.insert("after_epoch".to_owned(), after_epoch.to_string());
        params.insert("after_sequence".to_owned(), args.after_sequence.to_string());
    } else if args.after_sequence != 0 {
        params.insert("after_sequence".to_owned(), args.after_sequence.to_string());
    }
    if let Some(space_id) = space_id {
        params.insert("space_id".to_owned(), space_id.to_string());
    }
    if let Some(page_id) = page_id {
        params.insert("page_id".to_owned(), page_id.to_string());
    }

    let response = context.request("wait.for", params)?;
    Ok(json!({
        "wait": remote_field(&response, "wait")?,
        "event": remote_field(&response, "event")?,
        "cursor": remote_field(&response, "cursor")?,
    }))
}
