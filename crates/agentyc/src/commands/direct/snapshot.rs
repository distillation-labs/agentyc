use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, SnapshotArgs, host_error, lease_epoch, parse_page, parse_space,
    remote_field, remote_string, timestamp,
};

pub(super) fn run(context: &DirectContext, args: SnapshotArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let page_id = parse_page(&args.page_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .read_snapshot(
                &space_id,
                &page_id,
                authority,
                lease_epoch(args.lease_epoch),
                now,
            )
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": space_id,
            "page_id": page_id,
            "snapshot": result.envelope,
            "cache_state": result.cache_state,
            "scan_performed": result.scan_performed,
        }));
    }

    let response = context.request(
        "snapshot.read",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("page_id".to_owned(), page_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "page_id": remote_string(&response, "page_id")?,
        "snapshot": remote_field(&response, "snapshot")?,
        "cache_state": remote_string(&response, "cache_state")?,
        "scan_performed": remote_field(&response, "scan_performed")?,
    }))
}
