use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, PageCommand, PageCreateArgs, PageListArgs, host_error,
    lease_epoch, parse_space, timestamp,
};

pub(super) fn run(context: &DirectContext, command: PageCommand) -> DirectResult<Value> {
    match command {
        PageCommand::Create(args) => create(context, args),
        PageCommand::List(args) => list(context, args),
    }
}

fn create(context: &DirectContext, args: PageCreateArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let page = context
        .broker
        .create_page_at(
            &space_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            args.label,
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({"page": page, "page_id": page.page_id, "space_id": page.space_id}))
}

fn list(context: &DirectContext, args: PageListArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let space = context
        .broker
        .describe_space(&context.authority, &space_id)
        .map_err(host_error)?;
    Ok(json!({"space_id": space.space_id, "pages": space.pages}))
}
