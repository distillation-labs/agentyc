//! Chrome-launched Native Messaging host for an enrolled agentyc profile.
//!
//! The executable owns one durable broker for the profile and keeps Chrome's
//! Native Messaging stdio isolated from the agent/local protocol. It never
//! launches Chrome, discovers a debugger endpoint, or treats a client-supplied
//! profile value as authentication.

use std::{env, path::PathBuf, process::ExitCode};

use agentyc_core::PrincipalId;
use agentyc_host::{Broker, NativeMessagingBridge, NativeMessagingConfig};

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
    let mut arguments = env::args();
    let _program = arguments.next();
    let transport_origin = arguments
        .next()
        .ok_or_else(|| "Chrome's transport origin argument is required".to_owned())?;
    if arguments.next().is_some() {
        return Err("unexpected Native Messaging arguments".to_owned());
    }

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
    // to the bridge. The process stays alive for the Chrome port lifetime.
    let _ = bridge.wait_closed();
    Ok(())
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
