use serde_json::{Value, json};

use super::{
    ActionCommand, ActionReconcileArgs, ActionStatusArgs, DirectContext, DirectResult, host_error,
    lease_epoch, parse_action, timestamp,
};

pub(super) fn run(context: &DirectContext, command: ActionCommand) -> DirectResult<Value> {
    match command {
        ActionCommand::Status(args) => status(context, args),
        ActionCommand::Reconcile(args) => reconcile(context, args),
    }
}

fn status(context: &DirectContext, args: ActionStatusArgs) -> DirectResult<Value> {
    let action_id = parse_action(&args.action_id)?;
    let receipt = context
        .broker
        .action_status(&context.authority, &action_id)
        .map_err(host_error)?;
    Ok(json!({"action_id": receipt.action_id, "receipt": receipt}))
}

fn reconcile(context: &DirectContext, args: ActionReconcileArgs) -> DirectResult<Value> {
    let action_id = parse_action(&args.action_id)?;
    let result = context
        .broker
        .reconcile_action(
            &action_id,
            &context.authority,
            lease_epoch(args.lease_epoch),
            timestamp(args.now),
        )
        .map_err(host_error)?;
    Ok(json!({"action_id": result.receipt.action_id, "receipt": result.receipt}))
}
