use std::collections::BTreeMap;

use agentyc_host::{ContextBuilder, ContextRequest};
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
        let context = ContextBuilder::new().build(&result, &ContextRequest::auto())?;
        return Ok(json!({
            "space_id": space_id,
            "page_id": page_id,
            "snapshot": result.envelope,
            "context": context,
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
        "context": remote_field(&response, "context")?,
        "cache_state": remote_string(&response, "cache_state")?,
        "scan_performed": remote_field(&response, "scan_performed")?,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::direct::{
        DEFAULT_TTL, DirectCommand, DirectOptions, LeaseArgs, PageCommand, PageCreateArgs,
        SpaceCommand, SpaceCreateArgs, execute,
    };
    use tempfile::tempdir;

    #[test]
    fn snapshot_read_returns_logical_scope_cache_state_and_scan_marker() {
        let directory = tempdir().expect("temporary state directory");
        let context = DirectContext::open(&DirectOptions {
            state_dir: Some(directory.path().display().to_string()),
            principal: Some("principal_snapshot_test".to_owned()),
            profile_binding_id: None,
            offline: true,
            json: true,
        })
        .expect("offline direct context");
        let space = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "snapshot".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create space");
        let space_id = space["space_id"].as_str().expect("space id").to_owned();
        let claimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("claim space");
        let epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");
        let page = execute(
            &context,
            DirectCommand::Page(PageCommand::Create(PageCreateArgs {
                space_id: space_id.clone(),
                lease_epoch: epoch,
                label: "main".to_owned(),
                now: Some(2),
            })),
        )
        .expect("create logical page");
        let page_id = page["page_id"].as_str().expect("page id").to_owned();
        let (broker, authority) = context.local().expect("offline broker");
        let parsed_space = parse_space(&space_id).expect("space id");
        let parsed_page = parse_page(&page_id).expect("page id");
        let managed = broker
            .bind_page(
                &parsed_space,
                &parsed_page,
                authority,
                super::super::lease_epoch(epoch),
                timestamp(Some(3)),
                Some("https://example.test".to_owned()),
                Some("Example".to_owned()),
                0,
            )
            .expect("bind deterministic fake page");
        assert_eq!(managed.page_id, parsed_page);

        let result = run(
            &context,
            SnapshotArgs {
                space_id: space_id.clone(),
                page_id: page_id.clone(),
                lease_epoch: epoch,
                now: Some(4),
            },
        )
        .expect("read snapshot");
        assert_eq!(result["space_id"], space_id);
        assert_eq!(result["page_id"], page_id);
        assert_eq!(result["cache_state"], "fresh");
        assert_eq!(result["scan_performed"], true);
        assert_eq!(result["snapshot"]["mode"], "full");
        let encoded = serde_json::to_string(&result).expect("serialize snapshot");
        assert!(!encoded.contains("target_id"));
    }

    #[test]
    fn invalid_logical_page_is_a_structured_argument_error() {
        let directory = tempdir().expect("temporary state directory");
        let context = DirectContext::open(&DirectOptions {
            state_dir: Some(directory.path().display().to_string()),
            principal: Some("principal_snapshot_invalid".to_owned()),
            profile_binding_id: None,
            offline: true,
            json: true,
        })
        .expect("offline context");
        let error = run(
            &context,
            SnapshotArgs {
                space_id: "space_valid".to_owned(),
                page_id: "tab_123".to_owned(),
                lease_epoch: 1,
                now: Some(1),
            },
        )
        .expect_err("raw tab identifier is not a logical page");
        assert_eq!(error.code, agentyc_core::ErrorCode::InvalidArgument);
    }
}
