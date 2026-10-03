//! Chrome-launched Native Messaging host for an enrolled agentyc profile.
//!
//! The executable owns one durable broker for the profile and keeps Chrome's
//! Native Messaging stdio isolated from the agent/local protocol. It never
//! launches Chrome, discovers a debugger endpoint, or treats a client-supplied
//! profile value as authentication.

use std::{env, path::PathBuf, process::ExitCode};

use agentyc_core::PrincipalId;
use agentyc_host::{
    Broker, LocalHostServer, NativeMessagingBridge, NativeMessagingConfig, configured_socket_path,
};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("agentyc native host: {error}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<(), String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let transport_origin = parse_native_messaging_arguments(&arguments)?;

    // Chrome supplies argv[1] only after matching the manifest's exact
    // allowed_origins entry. Normalize only Chrome's trailing slash spelling;
    // direct same-user execution cannot be cryptographically distinguished.
    let configured = agentyc_host::normalize_extension_origin(&transport_origin)
        .map_err(|error| error.to_string())?;

    let state_dir = state_directory()?;
    let config = NativeMessagingConfig::new(configured).map_err(|error| error.to_string())?;
    let (native_hello, bridge) =
        NativeMessagingBridge::accept_stdio(config).map_err(|error| error.to_string())?;
    let principal = PrincipalId::from_suffix("extension")
        .map_err(|error| format!("invalid extension principal: {error}"))?;
    let hello = native_hello
        .to_core_hello(principal)
        .map_err(|error| error.to_string())?;
    let broker = Broker::open(&state_dir, bridge.clone()).map_err(|error| error.to_string())?;
    let connection = broker.hello(&hello).map_err(|error| error.to_string())?;
    let capabilities = broker.capabilities().map_err(|error| error.to_string())?;
    bridge
        .complete_handshake(
            &native_hello,
            connection.broker_epoch,
            connection.connection_epoch,
            &capabilities,
        )
        .map_err(|error| error.to_string())?;

    // The reader thread owns Native Messaging input and routes responses/events
    // to the bridge. Agent/MCP clients use the separate owner-only local socket;
    // they never open a second ledger or broker.
    let local_server = LocalHostServer::start(broker.clone(), configured_socket_path(&state_dir))
        .map_err(|error| error.to_string())?;
    let _ = bridge.wait_closed();
    local_server.stop();
    let _ = broker.disconnect(connection.authority());
    Ok(())
}

fn parse_native_messaging_arguments(arguments: &[String]) -> Result<String, String> {
    let Some(transport_origin) = arguments.first() else {
        return Err("Chrome's transport origin argument is required".to_owned());
    };
    if arguments.len() > 2 {
        return Err("unexpected Native Messaging arguments".to_owned());
    }
    if let Some(parent_window) = arguments.get(1) {
        let handle = parent_window
            .strip_prefix("--parent-window=")
            .ok_or_else(|| "unexpected Native Messaging argument".to_owned())?;
        if handle.is_empty()
            || handle.len() > 20
            || !handle.bytes().all(|byte| byte.is_ascii_digit())
        {
            return Err("invalid Native Messaging parent-window argument".to_owned());
        }
    }
    Ok(transport_origin.clone())
}

fn state_directory() -> Result<PathBuf, String> {
    if let Ok(path) = env::var("AGENTYC_STATE_DIR") {
        if path.trim().is_empty() {
            return Err("AGENTYC_STATE_DIR must not be empty".to_owned());
        }
        return Ok(PathBuf::from(path));
    }
    let home =
        env::var("HOME").map_err(|_| "HOME is unavailable; set AGENTYC_STATE_DIR".to_owned())?;
    Ok(PathBuf::from(home).join(".agentyc").join("state"))
}

#[cfg(test)]
mod tests {
    use super::parse_native_messaging_arguments;

    #[test]
    fn accepts_chrome_origin_and_windows_parent_window_argument() {
        let arguments = vec![
            "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/".to_owned(),
            "--parent-window=0".to_owned(),
        ];
        assert_eq!(
            parse_native_messaging_arguments(&arguments).expect("arguments"),
            arguments[0]
        );
    }

    #[test]
    fn rejects_unrecognized_native_messaging_arguments() {
        let arguments = vec![
            "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/".to_owned(),
            "--unexpected".to_owned(),
        ];
        assert!(parse_native_messaging_arguments(&arguments).is_err());
    }
}
