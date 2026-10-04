use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, PageCommand, PageCreateArgs, PageCreateManagedArgs,
    PageInventoryArgs, PageListArgs, host_error, lease_epoch, parse_space, remote_field,
    remote_string, timestamp,
};

pub(super) fn run(context: &DirectContext, command: PageCommand) -> DirectResult<Value> {
    match command {
        PageCommand::Create(args) => create(context, args),
        PageCommand::CreateManaged(args) => create_managed(context, args),
        PageCommand::List(args) => list(context, args),
        PageCommand::Inventory(args) => inventory(context, args),
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

fn create_managed(context: &DirectContext, args: PageCreateManagedArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let page = broker
            .create_managed_page(
                &space_id,
                authority,
                lease_epoch(args.lease_epoch),
                args.label,
                now,
                args.url.as_deref(),
                args.title.as_deref(),
            )
            .map_err(host_error)?;
        return Ok(json!({
            "page": page,
            "page_id": page.page_id,
            "space_id": page.space_id
        }));
    }

    let mut params = BTreeMap::from([
        ("space_id".to_owned(), space_id.to_string()),
        ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
        ("label".to_owned(), args.label),
        ("now".to_owned(), now.get().to_string()),
    ]);
    if let Some(url) = args.url {
        params.insert("url".to_owned(), url);
    }
    if let Some(title) = args.title {
        params.insert("title".to_owned(), title);
    }
    let response = context.request("page.create_managed", params)?;
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

fn inventory(context: &DirectContext, args: PageInventoryArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    if let Some((broker, authority)) = context.local() {
        if context.offline {
            let space = broker
                .describe_space(authority, &space_id)
                .map_err(host_error)?;
            return Ok(json!({"space_id": space.space_id, "pages": space.pages, "groups": []}));
        }
        let inventory = broker
            .page_inventory(authority, &space_id)
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": space_id,
            "pages": inventory.pages,
            "groups": inventory.groups,
            "safety": inventory.safety,
            "recovery_observed": inventory.recovery_observed,
        }));
    }

    let response = context.request(
        "page.inventory",
        BTreeMap::from([("space_id".to_owned(), space_id.to_string())]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "pages": remote_field(&response, "pages")?,
        "groups": remote_field(&response, "groups")?,
        "safety": remote_field(&response, "safety")?,
        "recovery_observed": remote_field(&response, "recovery_observed")?,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::direct::{
        DEFAULT_TTL, DirectCommand, DirectOptions, LeaseArgs, PageCommand, PageCreateArgs,
        PageListArgs, SpaceCommand, SpaceCreateArgs, execute,
    };
    use tempfile::tempdir;

    #[test]
    fn planned_page_results_are_logical_and_listable() {
        let directory = tempdir().expect("temporary state directory");
        let context = DirectContext::open(&DirectOptions {
            state_dir: Some(directory.path().display().to_string()),
            principal: Some("principal_page_test".to_owned()),
            profile_binding_id: None,
            offline: true,
            json: true,
        })
        .expect("offline direct context");
        let space = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "pages".to_owned(),
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
        let lease_epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");

        let page = execute(
            &context,
            DirectCommand::Page(PageCommand::Create(PageCreateArgs {
                space_id: space_id.clone(),
                lease_epoch,
                label: "main".to_owned(),
                now: Some(2),
            })),
        )
        .expect("create planned page");
        assert_eq!(page["page_id"], page["page"]["page_id"]);
        assert_eq!(page["space_id"], space_id);
        let encoded = serde_json::to_string(&page).expect("serialize logical page");
        for forbidden in ["target_id", "tab_id", "session_id", "debugger_id"] {
            assert!(!encoded.contains(forbidden), "unexpected {forbidden}");
        }

        let listed = execute(
            &context,
            DirectCommand::Page(PageCommand::List(PageListArgs { space_id })),
        )
        .expect("list pages");
        assert_eq!(listed["pages"].as_array().expect("page list").len(), 1);
        assert_eq!(listed["pages"][0]["page_id"], page["page_id"]);
    }
}
