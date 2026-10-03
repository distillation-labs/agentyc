use std::collections::BTreeMap;

use agentyc_core::SpaceLifecycle;
use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, LeaseArgs, LeaseRenewArgs, LeaseReturnArgs, SpaceCommand,
    SpaceCreateArgs, SpaceTransitionArgs, host_error, lease_epoch, parse_space, remote_field,
    remote_string, timestamp,
};

pub(super) fn run(context: &DirectContext, command: SpaceCommand) -> DirectResult<Value> {
    match command {
        SpaceCommand::Create(args) => create(context, args),
        SpaceCommand::List => list(context),
        SpaceCommand::Claim(args) => claim(context, args),
        SpaceCommand::Renew(args) => renew(context, args),
        SpaceCommand::Takeover(args) => takeover(context, args),
        SpaceCommand::Return(args) => return_control(context, args),
        SpaceCommand::Finish(args) => finish(context, args),
        SpaceCommand::Release(args) => release(context, args),
    }
}

fn create(context: &DirectContext, args: SpaceCreateArgs) -> DirectResult<Value> {
    if let Some((broker, authority)) = context.local() {
        let space = broker
            .create_space(authority, args.label)
            .map_err(host_error)?;
        return Ok(json!({
            "space": space,
            "space_id": space.space_id,
            "lifecycle": space.lifecycle
        }));
    }

    let response = context.request(
        "space.create",
        BTreeMap::from([("label".to_owned(), args.label)]),
    )?;
    Ok(json!({
        "space": remote_field(&response, "space")?,
        "space_id": remote_string(&response, "space_id")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}

fn list(context: &DirectContext) -> DirectResult<Value> {
    if let Some((broker, authority)) = context.local() {
        let spaces = broker.list_spaces(authority).map_err(host_error)?;
        return Ok(json!({"spaces": spaces}));
    }

    let response = context.request("space.list", BTreeMap::new())?;
    Ok(json!({"spaces": remote_field(&response, "spaces")?}))
}

fn claim(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let grant = broker
            .acquire_lease(&space_id, authority, now, args.ttl)
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": grant.space_id,
            "lease": grant.lease,
            "lifecycle": SpaceLifecycle::AgentOwned,
        }));
    }

    let response = context.request(
        "lease.acquire",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("now".to_owned(), now.get().to_string()),
            ("ttl".to_owned(), args.ttl.to_string()),
        ]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "lease": remote_field(&response, "lease")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}

fn renew(context: &DirectContext, args: LeaseRenewArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let grant = broker
            .renew_lease(
                &space_id,
                authority,
                lease_epoch(args.lease_epoch),
                now,
                args.ttl,
            )
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": grant.space_id,
            "lease": grant.lease,
            "lifecycle": SpaceLifecycle::AgentOwned,
        }));
    }

    let response = context.request(
        "lease.renew",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
            ("ttl".to_owned(), args.ttl.to_string()),
        ]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "lease": remote_field(&response, "lease")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}

fn takeover(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .takeover(&space_id, authority, now, args.ttl)
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": result.space_id,
            "lease_epoch": result.lease_epoch,
            "fence_acknowledged": result.fence_acknowledged,
            "lifecycle": result.lifecycle,
        }));
    }

    let response = context.request(
        "space.takeover",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("now".to_owned(), now.get().to_string()),
            ("ttl".to_owned(), args.ttl.to_string()),
        ]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "lease_epoch": remote_field(&response, "lease_epoch")?,
        "fence_acknowledged": remote_field(&response, "fence_acknowledged")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}

fn return_control(context: &DirectContext, args: LeaseReturnArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .return_control(&space_id, authority, lease_epoch(args.lease_epoch), now)
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": result.space_id,
            "released_epoch": result.released_epoch,
            "fence_epoch": result.fence_epoch,
            "lifecycle": result.lifecycle,
            "control_ticket": {
                "space_id": result.control_ticket.space_id(),
                "broker_epoch": result.control_ticket.broker_epoch(),
                "fence_epoch": result.control_ticket.fence_epoch(),
                "opaque": true,
            },
        }));
    }

    let response = context.request(
        "space.return",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "space_id": remote_string(&response, "space_id")?,
        "released_epoch": remote_field(&response, "released_epoch")?,
        "fence_epoch": remote_field(&response, "fence_epoch")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
        "control_ticket": remote_field(&response, "control_ticket")?,
    }))
}

fn finish(context: &DirectContext, args: SpaceTransitionArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let space = broker
            .finish_space(&space_id, authority, lease_epoch(args.lease_epoch), now)
            .map_err(host_error)?;
        return Ok(json!({
            "space": space,
            "space_id": space.space_id,
            "lifecycle": space.lifecycle,
        }));
    }

    let response = context.request(
        "space.finish",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "space": remote_field(&response, "space")?,
        "space_id": remote_string(&response, "space_id")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}

fn release(context: &DirectContext, args: SpaceTransitionArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let space = broker
            .release_space(&space_id, authority, lease_epoch(args.lease_epoch), now)
            .map_err(host_error)?;
        return Ok(json!({
            "space": space,
            "space_id": space.space_id,
            "lifecycle": space.lifecycle,
        }));
    }

    let response = context.request(
        "space.release",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "space": remote_field(&response, "space")?,
        "space_id": remote_string(&response, "space_id")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
    }))
}
