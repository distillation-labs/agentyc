use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, PageCommand, PageCreateArgs, PageListArgs, host_error,
    lease_epoch, parse_space, remote_field, remote_string, timestamp,
};

pub(super) fn run(context: &DirectContext, command: PageCommand) -> DirectResult<Value> {
    match command {
        PageCommand::Create(args) => create(context, args),
        PageCommand::List(args) => list(context, args),
    }
}

fn create(context: &DirectContext, args: PageCreateArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let page = broker
            .create_page_at(
                &space_id,
                authority,
                lease_epoch(args.lease_epoch),
                args.label,
                now,
            )
            .map_err(host_error)?;
        return Ok(json!({
            "page": page,
            "page_id": page.page_id,
            "space_id": page.space_id
        }));
    }

    let response = context.request(
        "page.create",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("label".to_owned(), args.label),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "page": remote_field(&response, "page")?,
        "page_id": remote_string(&response, "page_id")?,
        "space_id": remote_string(&response, "space_id")?,
    }))
}

fn list(context: &DirectContext, args: PageListArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    if let Some((broker, authority)) = context.local() {
        let space = broker
            .describe_space(authority, &space_id)
            .map_err(host_error)?;
        return Ok(json!({"space_id": space.space_id, "pages": space.pages}));
    }

    let response = context.request(
        "page.list",
        BTreeMap::from([("space_id".to_owned(), space_id.to_string())]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "pages": remote_field(&response, "pages")?,
    }))
}
