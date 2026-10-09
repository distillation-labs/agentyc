//! Chrome-launched Native Messaging host for an enrolled agentyc profile.
//!
//! The executable owns one durable broker for the profile and keeps Chrome's
//! Native Messaging stdio isolated from the agent/local protocol. It never
//! launches Chrome, discovers a debugger endpoint, or treats a client-supplied
//! profile value as authentication.

use std::{env, path::PathBuf, process::ExitCode, sync::Arc, thread, time::Duration};

use agentyc_core::{ErrorCode, PrincipalId, ProfileBindingId};
use agentyc_host::{
    BridgeRouter, Broker, CdpBridge, DEFAULT_CDP_PORT, EndpointMetadata, HostLifecycle, Ledger,
    LedgerError, LocalHostServer, NativeForwardServer, NativeMessagingBridge,
    NativeMessagingConfig, TabCreationTransportRouter, configured_socket_path,
    forward_stdio_to_owner, publish_endpoint_metadata, remove_endpoint_metadata_if_owner,
};

const SUPERVISOR_POLL_INTERVAL: Duration = Duration::from_millis(5);

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
    let configured = agentyc_host::normalize_extension_origin(&transport_origin)
        .map_err(|error| error.to_string())?;
    let state_dir = state_directory()?;
    let config = NativeMessagingConfig::new(configured).map_err(|error| error.to_string())?;

    let ledger = match Ledger::open(&state_dir) {
        Ok(ledger) => ledger,
        Err(LedgerError::AlreadyOwned) => {
            #[cfg(unix)]
            {
                forward_stdio_to_owner(&state_dir, config.handshake_timeout)
                    .map_err(|error| error.to_string())?;
                return Ok(());
            }
            #[cfg(not(unix))]
            {
                return Err("a broker already owns the state directory".to_owned());
            }
        }
        Err(error) => return Err(error.to_string()),
    };

    #[cfg(unix)]
    let forward_server =
        NativeForwardServer::start(&state_dir).map_err(|error| error.to_string())?;
    let (native_hello, bridge) =
        NativeMessagingBridge::accept_stdio(config.clone()).map_err(|error| error.to_string())?;
    let tab_creation = Arc::new(TabCreationTransportRouter::default());
    tab_creation
        .install_native_messaging_bridge(bridge.clone())
        .map_err(|error| error.to_string())?;
    let cdp_bridge = CdpBridge::connect_local(
        Arc::clone(&tab_creation),
        configured_cdp_port()?,
        Duration::from_secs(2),
    )
    .map_err(|error| error.to_string())?;
    let principal = PrincipalId::from_suffix("extension")
        .map_err(|error| format!("invalid extension principal: {error}"))?;
    let hello = native_hello
        .to_core_hello(principal)
        .map_err(|error| error.to_string())?;
    let router = Arc::new(BridgeRouter::with_bridge(Arc::new(cdp_bridge)));
    let broker = Broker::with_shared_bridge(ledger, router);
    broker
        .transition_lifecycle(HostLifecycle::Starting)
        .map_err(|error| error.to_string())?;
    broker
        .wait_for_extension()
        .map_err(|error| error.to_string())?;
    let connection = match broker.hello(&hello) {
        Ok(connection) => connection,
        Err(error) => {
            if error.as_core_error().code == ErrorCode::ProfileNotFound
                && let Ok(observed_profile) =
                    ProfileBindingId::new(native_hello.profile_instance_id.clone())
            {
                let _ = broker.mark_profile_rebind_required(&observed_profile);
            }
            return Err(error.to_string());
        }
    };
    bridge
        .complete_handshake_with_resume(
            &native_hello,
            connection.broker_epoch,
            connection.connection_epoch,
            &[],
            connection.resume,
        )
        .map_err(|error| error.to_string())?;
    broker.mark_ready().map_err(|error| error.to_string())?;

    let local_server = LocalHostServer::start(broker.clone(), configured_socket_path(&state_dir))
        .map_err(|error| error.to_string())?;
    let endpoint = EndpointMetadata::new(
        broker
            .broker_epoch()
            .map_err(|error| error.to_string())?
            .get(),
        std::process::id(),
        local_server.socket_path().display().to_string(),
        #[cfg(unix)]
        forward_server.socket_path().display().to_string(),
        #[cfg(not(unix))]
        "unsupported".to_owned(),
    );
    publish_endpoint_metadata(&state_dir, &endpoint).map_err(|error| error.to_string())?;
    let supervision = supervise_native_connection(
        &broker,
        bridge,
        #[cfg(unix)]
        &forward_server,
        &tab_creation,
        &config,
    );
    local_server.stop();
    #[cfg(unix)]
    forward_server.stop();
    let _ = broker.disconnect(connection.authority());
    let _ =
        remove_endpoint_metadata_if_owner(&state_dir, endpoint.broker_epoch, endpoint.owner_pid);
    supervision
}

fn supervise_native_connection(
    broker: &Broker,
    bridge: NativeMessagingBridge,
    #[cfg(unix)] forward_server: &NativeForwardServer,
    tab_creation: &TabCreationTransportRouter,
    config: &NativeMessagingConfig,
) -> Result<(), String> {
    let mut bridge = bridge;
    loop {
        #[cfg(unix)]
        if let Ok(stream) = forward_server.accept_forwarded(Duration::ZERO) {
            let reader = stream
                .try_clone()
                .map_err(|error| format!("Native Messaging forwarding clone failed: {error}"))?;
            if let Ok((next_hello, next_bridge)) =
                NativeMessagingBridge::accept(reader, stream, config.clone())
            {
                tab_creation
                    .install_native_messaging_bridge(next_bridge.clone())
                    .map_err(|error| error.to_string())?;
                let principal = PrincipalId::from_suffix("extension")
                    .map_err(|error| format!("invalid extension principal: {error}"))?;
                let hello = next_hello
                    .to_core_hello(principal)
                    .map_err(|error| error.to_string())?;
                let connection = broker.hello(&hello).map_err(|error| error.to_string())?;
                next_bridge
                    .complete_handshake_with_resume(
                        &next_hello,
                        connection.broker_epoch,
                        connection.connection_epoch,
                        &[],
                        connection.resume,
                    )
                    .map_err(|error| error.to_string())?;
                broker.mark_ready().map_err(|error| error.to_string())?;
                bridge = next_bridge;
            }
        }

        if bridge.is_closed() {
            thread::sleep(SUPERVISOR_POLL_INTERVAL);
            continue;
        }
        thread::sleep(SUPERVISOR_POLL_INTERVAL);
    }
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

fn configured_cdp_port() -> Result<u16, String> {
    match env::var("AGENTYC_CDP_PORT") {
        Ok(value) => configured_cdp_port_value(Some(&value)),
        Err(env::VarError::NotPresent) => Ok(DEFAULT_CDP_PORT),
        Err(env::VarError::NotUnicode(_)) => Err("AGENTYC_CDP_PORT must be valid UTF-8".to_owned()),
    }
}

fn configured_cdp_port_value(value: Option<&str>) -> Result<u16, String> {
    value
        .map(str::parse::<u16>)
        .transpose()
        .map_err(|_| "AGENTYC_CDP_PORT must be a valid TCP port".to_owned())?
        .map_or(Ok(DEFAULT_CDP_PORT), Ok)
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn cdp_port_defaults_and_accepts_a_test_override() {
        assert_eq!(
            configured_cdp_port_value(None).expect("default port"),
            DEFAULT_CDP_PORT
        );
        assert_eq!(
            configured_cdp_port_value(Some("9333")).expect("override port"),
            9333
        );
    }
}
