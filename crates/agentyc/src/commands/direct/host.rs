use agentyc_host::HostLifecycle;
use serde_json::{Value, json};

use super::{DirectContext, DirectResult, HostCommand, host_error};

pub(super) fn run(context: &DirectContext, command: HostCommand) -> DirectResult<Value> {
    match command {
        HostCommand::Status => status(context),
    }
}

fn status(context: &DirectContext) -> DirectResult<Value> {
    let lifecycle = context.broker.lifecycle().map_err(host_error)?;
    let epoch = context.broker.broker_epoch().map_err(host_error)?;
    let capabilities = context.broker.capabilities().map_err(host_error)?;
    Ok(json!({
        "state_directory": context.state_dir,
        "broker_epoch": epoch,
        "lifecycle": lifecycle_name(lifecycle),
        "bridge": {
            "mode": if context.offline { "fake" } else { "extension" },
            "connected": context.offline && !capabilities.is_empty(),
            "capabilities": capabilities,
            "test_seam": context.offline,
        },
        "direct_path": {
            "browser_auto_launch": false,
            "copied_debug_endpoint": false,
            "logical_ids_only": true,
        },
    }))
}

fn lifecycle_name(lifecycle: HostLifecycle) -> &'static str {
    match lifecycle {
        HostLifecycle::Ready => "ready",
        HostLifecycle::Draining => "draining",
        HostLifecycle::Stopped => "stopped",
    }
}
