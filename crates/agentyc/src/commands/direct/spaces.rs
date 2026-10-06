use std::collections::BTreeMap;

use agentyc_core::{
    BrokerEpoch, LeaseEpoch, ProfileDisclosure, ReconcileToken, SpaceId, SpaceLifecycle,
};
use serde_json::{Value, json};

use super::{
    DirectContext, DirectResult, LeaseArgs, LeaseRenewArgs, LeaseReturnArgs, SpaceCommand,
    SpaceCreateArgs, SpacePruneArgs, SpaceReclaimArgs, SpaceTransitionArgs, host_error,
    lease_epoch, parse_space, parse_value, remote_field, remote_string, timestamp,
};

const MAX_CONTROL_TICKET_JSON_BYTES: usize = 8 * 1024;

struct ControlTicketEnvelope {
    space_id: SpaceId,
    broker_epoch: BrokerEpoch,
    fence_epoch: LeaseEpoch,
    token: Option<ReconcileToken>,
    opaque: Option<bool>,
    in_memory: Option<bool>,
}

pub(super) fn run(context: &DirectContext, command: SpaceCommand) -> DirectResult<Value> {
    match command {
        SpaceCommand::Create(args) => create(context, args),
        SpaceCommand::List => list(context),
        SpaceCommand::Prune(args) => prune(context, args),
        SpaceCommand::Claim(args) => claim(context, args),
        SpaceCommand::Renew(args) => renew(context, args),
        SpaceCommand::Takeover(args) => takeover(context, args),
        SpaceCommand::AcknowledgeFence(args) => acknowledge_fence(context, args),
        SpaceCommand::Reclaim(args) => reclaim(context, args),
        SpaceCommand::Return(args) => return_control(context, args),
        SpaceCommand::Pause(args) => pause(context, args),
        SpaceCommand::Handoff(args) => handoff(context, args),
        SpaceCommand::Finish(args) => finish(context, args),
        SpaceCommand::Release(args) => release(context, args),
    }
}

fn prune(context: &DirectContext, args: SpacePruneArgs) -> DirectResult<Value> {
    if args.max_count == 0 {
        return Ok(json!({"pruned": 0}));
    }
    if let Some((broker, authority)) = context.local() {
        let pruned = broker
            .prune_released_spaces(authority, args.max_count as usize)
            .map_err(host_error)?;
        return Ok(json!({"pruned": pruned}));
    }
    let response = context.request(
        "space.prune",
        BTreeMap::from([("max_count".to_owned(), args.max_count.to_string())]),
    )?;
    Ok(json!({"pruned": remote_field(&response, "pruned")?}))
}

fn create(context: &DirectContext, args: SpaceCreateArgs) -> DirectResult<Value> {
    if !args.accept_shared_profile_disclosure {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::PermissionDenied,
            "explicit shared-profile disclosure acknowledgement is required before space creation",
        ));
    }
    let disclosure = ProfileDisclosure {
        profile_scope: ProfileDisclosure::PROFILE_SCOPE.to_owned(),
        shared_state_notice: ProfileDisclosure::SHARED_STATE_NOTICE.to_owned(),
        isolation_claim: false,
        acknowledged: true,
    };
    if let Some((broker, authority)) = context.local() {
        let space = broker
            .create_space_with_disclosure(authority, args.label, disclosure)
            .map_err(host_error)?;
        return Ok(json!({
            "space": space,
            "space_id": space.space_id,
            "lifecycle": space.lifecycle
        }));
    }

    let response = context.request(
        "space.create",
        BTreeMap::from([
            ("label".to_owned(), args.label),
            ("profile_scope".to_owned(), disclosure.profile_scope),
            (
                "shared_state_notice".to_owned(),
                disclosure.shared_state_notice,
            ),
            ("isolation_claim".to_owned(), "false".to_owned()),
            (
                "profile_disclosure_acknowledged".to_owned(),
                "true".to_owned(),
            ),
        ]),
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

fn acknowledge_fence(context: &DirectContext, args: LeaseRenewArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .acknowledge_fence_with_ttl(
                &space_id,
                authority,
                lease_epoch(args.lease_epoch),
                now,
                args.ttl,
            )
            .map_err(host_error)?;
        return Ok(json!({
            "space_id": result.space_id,
            "lease_epoch": result.lease_epoch,
            "fence_acknowledged": result.fence_acknowledged,
            "lifecycle": result.lifecycle,
        }));
    }

    let response = context.request(
        "space.acknowledge_fence",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
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

fn reclaim(context: &DirectContext, args: SpaceReclaimArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let control_ticket = args
        .control_ticket
        .as_deref()
        .map(parse_control_ticket)
        .transpose()?;
    let now = timestamp(args.now);

    if let Some((broker, authority)) = context.local() {
        let ticket = match context.control_ticket(&space_id) {
            Some(ticket) => ticket,
            None => broker
                .control_ticket(authority, &space_id)
                .map_err(host_error)?,
        };
        if let Some(control_ticket) = &control_ticket {
            validate_control_ticket(control_ticket, &ticket)?;
        }
        let result = broker
            .takeover_with_control_ticket(&space_id, authority, &ticket, now, args.ttl)
            .map_err(host_error)?;
        context.take_control_ticket(&space_id);
        return Ok(json!({
            "space_id": result.space_id,
            "lease_epoch": result.lease_epoch,
            "fence_acknowledged": result.fence_acknowledged,
            "lifecycle": result.lifecycle,
        }));
    }

    let control_ticket = control_ticket.ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument("remote space reclaim requires --control-ticket")
    })?;
    let response = context.request(
        "space.takeover_with_control_ticket",
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            (
                "control_ticket".to_owned(),
                serialize_control_ticket(&control_ticket)?,
            ),
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

fn parse_control_ticket(value: &str) -> DirectResult<ControlTicketEnvelope> {
    if value.len() > MAX_CONTROL_TICKET_JSON_BYTES {
        return Err(agentyc_core::CoreError::invalid_argument(
            "control_ticket exceeds the 8192-byte limit",
        ));
    }
    let object = serde_json::from_str::<Value>(value)
        .map_err(|error| {
            agentyc_core::CoreError::invalid_argument(format!("invalid control_ticket: {error}"))
        })?
        .as_object()
        .cloned()
        .ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument("control_ticket must be a JSON object")
        })?;
    const ALLOWED_FIELDS: [&str; 6] = [
        "space_id",
        "broker_epoch",
        "fence_epoch",
        "token",
        "opaque",
        "in_memory",
    ];
    if object
        .keys()
        .any(|key| !ALLOWED_FIELDS.contains(&key.as_str()))
    {
        return Err(agentyc_core::CoreError::invalid_argument(
            "control_ticket contains an unknown field",
        ));
    }
    let field = |name: &str| {
        object.get(name).ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument(format!("control_ticket is missing {name}"))
        })
    };
    let space_id = field("space_id")?
        .as_str()
        .ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument("control_ticket space_id must be a string")
        })
        .and_then(parse_space)?;
    let broker_epoch = BrokerEpoch::new(field("broker_epoch")?.as_u64().ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(
            "control_ticket broker_epoch must be an unsigned integer",
        )
    })?);
    let fence_epoch = LeaseEpoch::new(field("fence_epoch")?.as_u64().ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(
            "control_ticket fence_epoch must be an unsigned integer",
        )
    })?);
    let token = object
        .get("token")
        .map(|value| {
            value.as_str().ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("control_ticket token must be a string")
            })
        })
        .transpose()?
        .map(|value| parse_value::<ReconcileToken>(value, "control_ticket token"))
        .transpose()?;
    let opaque = object
        .get("opaque")
        .map(|value| {
            value.as_bool().ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("control_ticket opaque must be a boolean")
            })
        })
        .transpose()?;
    let in_memory = object
        .get("in_memory")
        .map(|value| {
            value.as_bool().ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument(
                    "control_ticket in_memory must be a boolean",
                )
            })
        })
        .transpose()?;
    Ok(ControlTicketEnvelope {
        space_id,
        broker_epoch,
        fence_epoch,
        token,
        opaque,
        in_memory,
    })
}

fn serialize_control_ticket(ticket: &ControlTicketEnvelope) -> DirectResult<String> {
    let mut value = json!({
        "space_id": ticket.space_id,
        "broker_epoch": ticket.broker_epoch,
        "fence_epoch": ticket.fence_epoch,
    });
    let object = value.as_object_mut().ok_or_else(|| {
        agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::InvalidJson,
            "control_ticket could not be represented as JSON",
        )
    })?;
    if let Some(token) = &ticket.token {
        object.insert("token".to_owned(), Value::String(token.to_string()));
    }
    if let Some(opaque) = ticket.opaque {
        object.insert("opaque".to_owned(), Value::Bool(opaque));
    }
    if let Some(in_memory) = ticket.in_memory {
        object.insert("in_memory".to_owned(), Value::Bool(in_memory));
    }
    serde_json::to_string(&value).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!(
            "control_ticket cannot be serialized: {error}"
        ))
    })
}

fn validate_control_ticket(
    envelope: &ControlTicketEnvelope,
    ticket: &agentyc_host::ControlTicket,
) -> DirectResult<()> {
    if envelope.token.is_none() && envelope.in_memory != Some(true) {
        return Err(agentyc_core::CoreError::invalid_argument(
            "control_ticket token is required outside the in-memory seam",
        ));
    }
    if envelope.space_id.as_str() != ticket.space_id().as_str()
        || envelope.broker_epoch != ticket.broker_epoch()
        || envelope.fence_epoch != ticket.fence_epoch()
        || envelope
            .token
            .as_ref()
            .is_some_and(|token| token != ticket.token())
    {
        return Err(agentyc_core::CoreError::invalid_argument(
            "control_ticket does not match the current space handoff",
        ));
    }
    Ok(())
}

fn return_control(context: &DirectContext, args: LeaseReturnArgs) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .return_control(&space_id, authority, lease_epoch(args.lease_epoch), now)
            .map_err(host_error)?;
        context.remember_control_ticket(result.control_ticket.clone());
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
                "in_memory": true,
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

fn pause(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    fenced_transition(context, args, "space.pause")
}

fn handoff(context: &DirectContext, args: LeaseArgs) -> DirectResult<Value> {
    fenced_transition(context, args, "space.handoff")
}

fn fenced_transition(
    context: &DirectContext,
    args: LeaseArgs,
    method: &str,
) -> DirectResult<Value> {
    let space_id = parse_space(&args.space_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let space = if method == "space.pause" {
            broker
                .pause_space(&space_id, authority, now, args.ttl)
                .map_err(host_error)?
        } else {
            broker
                .handoff_space(&space_id, authority, now, args.ttl)
                .map_err(host_error)?
        };
        return Ok(json!({
            "space": space,
            "space_id": space.space_id,
            "lifecycle": space.lifecycle,
        }));
    }

    let response = context.request(
        method,
        BTreeMap::from([
            ("space_id".to_owned(), space_id.to_string()),
            ("now".to_owned(), now.get().to_string()),
            ("ttl".to_owned(), args.ttl.to_string()),
        ]),
    )?;
    Ok(json!({
        "space": remote_field(&response, "space")?,
        "space_id": remote_string(&response, "space_id")?,
        "lifecycle": remote_string(&response, "lifecycle")?,
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
