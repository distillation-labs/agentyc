use serde_json::{Value, json};

use super::{DirectContext, DirectResult, ExtensionCommand};

pub(super) fn run(context: &DirectContext, command: ExtensionCommand) -> DirectResult<Value> {
    match command {
        ExtensionCommand::Status => status(context),
    }
}

fn status(context: &DirectContext) -> DirectResult<Value> {
    let host = super::host::status(context)?;
    let bridge = host.get("bridge").cloned().unwrap_or(Value::Null);
    let observed_connected = bridge
        .get("mode")
        .and_then(Value::as_str)
        .is_some_and(|mode| mode == "extension")
        && bridge
            .get("connected")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    Ok(json!({
        "observed_connected": observed_connected,
        "extension_version": host
            .get("extension_version")
            .cloned()
            .unwrap_or(Value::Null),
        "profile_bound": host
            .get("profile_bound")
            .cloned()
            .unwrap_or(Value::Null),
        "bridge": bridge,
        "host_lifecycle": host.get("lifecycle").cloned().unwrap_or(Value::Null),
    }))
}
