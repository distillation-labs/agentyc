use agentyc_core::SpaceLifecycle;
use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, LeaseArgs, LeaseRenewArgs, LeaseReturnArgs, SpaceCommand,
    SpaceCreateArgs, SpaceTransitionArgs, host_error, lease_epoch, parse_space, timestamp,
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
    let space = context
        .broker
        .create_space(&context.authority, args.label)
        .map_err(host_error)?;
    Ok(json!({"space": space, "space_id": space.space_id, "lifecycle": space.lifecycle}))
}

fn list(context: &DirectContext) -> DirectResult<Value> {
    let spaces = context
        .broker
        .list_spaces(&context.authority)
        .map_err(host_error)?;
    Ok(json!({"spaces": spaces}))
}

fn claim(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let grant = context
        .broker
        .acquire_lease(&space_id, &context.authority, timestamp(args.now), args.ttl)
        .map_err(host_error)?;
    Ok(json!({
        "space_id": grant.space_id,
        "lease": grant.lease,
        "lifecycle": SpaceLifecycle::AgentOwned,
    }))
}

fn renew(context: &DirectContext, args: LeaseRenewArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let grant = context
        .broker
        .renew_lease(
            &space_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
            args.ttl,
        )
        .map_err(host_error)?;
    Ok(json!({
        "space_id": grant.space_id,
        "lease": grant.lease,
        "lifecycle": SpaceLifecycle::AgentOwned,
    }))
}

fn takeover(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let result = context
        .broker
        .takeover(&space_id, &context.authority, timestamp(args.now), args.ttl)
        .map_err(host_error)?;
    Ok(json!({
        "space_id": result.space_id,
        "lease_epoch": result.lease_epoch,
        "fence_acknowledged": result.fence_acknowledged,
        "lifecycle": result.lifecycle,
    }))
}

fn return_control(context: &DirectContext, args: LeaseReturnArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let result = context
        .broker
        .return_control(
            &space_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({
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
    }))
}

fn finish(context: &DirectContext, args: SpaceTransitionArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let space = context
        .broker
        .finish_space(
            &space_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({
        "space": space,
        "space_id": space.space_id,
        "lifecycle": space.lifecycle,
    }))
}

fn release(context: &DirectContext, args: SpaceTransitionArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let space = context
        .broker
        .release_space(
            &space_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({
        "space": space,
        "space_id": space.space_id,
        "lifecycle": space.lifecycle,
    }))
}
