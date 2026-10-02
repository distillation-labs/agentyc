use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, SnapshotArgs, host_error, lease_epoch, parse_page, parse_space,
    timestamp,
};

pub(super) fn run(context: &DirectContext, args: SnapshotArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let page_id = parse_page(&args.page_id)?;
    let result = context
        .broker
        .read_snapshot(
            &space_id,
            &page_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({
        "space_id": space_id,
        "page_id": page_id,
        "snapshot": result.envelope,
        "cache_state": result.cache_state,
        "scan_performed": result.scan_performed,
    }))
}
