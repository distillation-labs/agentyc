use std::collections::BTreeMap;

use serde_json::{Value, json};

use super::{
    ActionCommand, ActionReconcileArgs, ActionStatusArgs, DirectContext, DirectResult, host_error,
    lease_epoch, parse_action, remote_field, remote_string, timestamp,
};

pub(super) fn run(context: &DirectContext, command: ActionCommand) -> DirectResult<Value> {
    match command {
        ActionCommand::Status(args) => status(context, args),
        ActionCommand::Reconcile(args) => reconcile(context, args),
    }
}

fn status(context: &DirectContext, args: ActionStatusArgs) -> DirectResult<Value> {
    let action_id = parse_action(&args.action_id)?;
    if let Some((broker, authority)) = context.local() {
        let receipt = broker
            .action_status(authority, &action_id)
            .map_err(host_error)?;
        return Ok(json!({"action_id": receipt.action_id, "receipt": receipt}));
    }

    let response = context.request(
        "action.status",
        BTreeMap::from([("action_id".to_owned(), action_id.to_string())]),
    )?;
    Ok(json!({
        "action_id": remote_string(&response, "action_id")?,
        "receipt": remote_field(&response, "receipt")?,
    }))
}

fn reconcile(context: &DirectContext, args: ActionReconcileArgs) -> DirectResult<Value> {
    let action_id = parse_action(&args.action_id)?;
    let now = timestamp(args.now);
    if let Some((broker, authority)) = context.local() {
        let result = broker
            .reconcile_action(&action_id, authority, lease_epoch(args.lease_epoch), now)
            .map_err(host_error)?;
        return Ok(json!({
            "action_id": result.receipt.action_id,
            "receipt": result.receipt
        }));
    }

    let response = context.request(
        "action.reconcile",
        BTreeMap::from([
            ("action_id".to_owned(), action_id.to_string()),
            ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
            ("now".to_owned(), now.get().to_string()),
        ]),
    )?;
    Ok(json!({
        "action_id": remote_string(&response, "action_id")?,
        "receipt": remote_field(&response, "receipt")?,
    }))
}
