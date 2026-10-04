use std::collections::BTreeMap;

use agentyc_host::HostLifecycle;
use serde_json::{Value, json};

use super::{DirectContext, DirectResult, HostCommand, host_error, remote_field};

pub(super) fn run(context: &DirectContext, command: HostCommand) -> DirectResult<Value> {
    match command {
        HostCommand::Status => status(context),
    }
}

fn status(context: &DirectContext) -> DirectResult<Value> {
    if let Some((broker, _authority)) = context.local() {
        let lifecycle = broker.lifecycle().map_err(host_error)?;
        let epoch = broker.broker_epoch().map_err(host_error)?;
        let capabilities = broker.capabilities().map_err(host_error)?;
        return Ok(json!({
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
        }));
    }

    let response = context.request("host.status", BTreeMap::new())?;
    let capabilities = remote_field(&response, "capabilities")?;
    let optional = |field: &str| -> DirectResult<Value> {
        response
            .get(field)
            .map(|_| remote_field(&response, field))
            .transpose()
            .map(|value| value.unwrap_or(Value::Null))
    };
    let connected = capabilities
        .as_array()
        .is_some_and(|capabilities| !capabilities.is_empty());
    Ok(json!({
        "state_directory": context.state_dir,
        "broker_epoch": remote_field(&response, "broker_epoch")?,
        "lifecycle": remote_field(&response, "lifecycle")?,
        "connection_epoch": optional("connection_epoch")?,
        "worker_instance_epoch": optional("worker_instance_epoch")?,
        "browser_session_epoch": optional("browser_session_epoch")?,
        "extension_version": optional("extension_version")?,
        "profile_instance_id": optional("profile_instance_id")?,
        "profile_scope": optional("profile_scope")?,
        "profile_bound": optional("profile_bound")?,
        "bridge": {
            "mode": "extension",
            "connected": connected,
            "capabilities": capabilities,
            "test_seam": false,
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
