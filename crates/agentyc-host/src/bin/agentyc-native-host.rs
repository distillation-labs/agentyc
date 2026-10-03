//! Chrome-launched Native Messaging host for an enrolled agentyc profile.
//!
//! The executable owns one durable broker for the profile and keeps Chrome's
//! Native Messaging stdio isolated from the agent/local protocol. It never
//! launches Chrome, discovers a debugger endpoint, or treats a client-supplied
//! profile value as authentication.

use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    path::PathBuf,
    process::ExitCode,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use agentyc_core::{CoreError, ErrorCode, LeaseEpoch, PrincipalId, SpaceId, Timestamp};
use agentyc_host::native_messaging::NativeRequest;
use agentyc_host::{
    AuthorityTicket, Broker, LocalHostServer, NativeMessagingBridge, NativeMessagingConfig,
    configured_socket_path,
};
use serde_json::{Map, Value, json};

const DEFAULT_SIDE_PANEL_TTL: u64 = 60_000;
const SUPERVISOR_POLL_INTERVAL: Duration = Duration::from_millis(5);

#[derive(Debug, Default)]
struct SidePanelTicketRegistry {
    issued: BTreeMap<String, (String, String, u64)>,
    used: BTreeSet<String>,
    profile_instance_id: Option<String>,
    browser_session_epoch: Option<u64>,
}

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
    let mut side_panel_tickets = send_control_spaces(&broker, connection.authority(), &bridge)?;

    // The reader thread owns Native Messaging input and routes responses/events
    // to the bridge. Agent/MCP clients use the separate owner-only local socket;
    // they never open a second ledger or broker.
    let local_server = LocalHostServer::start(broker.clone(), configured_socket_path(&state_dir))
        .map_err(|error| error.to_string())?;
    supervise_native_requests(
        &broker,
        connection.authority(),
        &bridge,
        &mut side_panel_tickets,
    )?;
    local_server.stop();
    let _ = broker.disconnect(connection.authority());
    Ok(())
}

fn send_control_spaces(
    broker: &Broker,
    authority: &AuthorityTicket,
    bridge: &NativeMessagingBridge,
) -> Result<SidePanelTicketRegistry, String> {
    let profile = bridge.hello().map_err(|error| error.to_string())?;
    let spaces = broker
        .list_profile_spaces(authority)
        .map_err(|error| error.to_string())?;
    let actions = [
        "takeover",
        "return_control",
        "stop",
        "pause",
        "handoff",
        "finish",
        "release",
        "retain",
    ];
    let mut registry = SidePanelTicketRegistry {
        profile_instance_id: Some(profile.profile_instance_id.clone()),
        browser_session_epoch: Some(profile.browser_session_epoch),
        ..SidePanelTicketRegistry::default()
    };
    let spaces = spaces
        .into_iter()
        .map(|space| {
            let intent_tickets = actions
                .iter()
                .map(|action| {
                    let ticket_id = format!(
                        "ticket_sidepanel_{}_{}_{}",
                        space.space_id, action, profile.browser_session_epoch
                    );
                    let expires_at = current_millis().saturating_add(15 * 60 * 1000);
                    registry.issued.insert(
                        ticket_id.clone(),
                        (space.space_id.to_string(), (*action).to_owned(), expires_at),
                    );
                    (
                        (*action).to_owned(),
                        json!({
                            "issued_by_host": true,
                            "ticket_id": ticket_id,
                            "purpose": "sidepanel",
                            "action": action,
                            "space_id": space.space_id,
                            "profile_instance_id": profile.profile_instance_id,
                            "browser_session_epoch": profile.browser_session_epoch,
                            "expires_at": expires_at,
                        }),
                    )
                })
                .collect::<serde_json::Map<_, _>>();
            json!({
                "space_id": space.space_id,
                "label": space.label,
                "lifecycle": space.lifecycle,
                "owner": space.owner,
                "intent_tickets": intent_tickets,
                "pages": space.pages.into_iter().map(|page| json!({
                    "page_id": page.page_id,
                    "label": page.label,
                    "lifecycle": page.lifecycle,
                    "ownership": page.ownership,
                    "binding": page.binding,
                })).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>();
    bridge
        .send_event("host.spaces", json!({"spaces": spaces}))
        .map_err(|error| error.to_string())?;
    Ok(registry)
}

fn supervise_native_requests(
    broker: &Broker,
    authority: &AuthorityTicket,
    bridge: &NativeMessagingBridge,
    tickets: &mut SidePanelTicketRegistry,
) -> Result<(), String> {
    loop {
        for request in bridge.drain_requests() {
            if bridge.is_closed() {
                return Ok(());
            }
            let result = dispatch_native_request(broker, authority, &request, tickets);
            let should_refresh_spaces = result.is_ok();
            if let Err(error) = bridge.respond(&request, result) {
                if bridge.is_closed() {
                    return Ok(());
                }
                return Err(error.to_string());
            }
            if should_refresh_spaces {
                let refreshed = send_control_spaces(broker, authority, bridge)?;
                tickets.issued.extend(refreshed.issued);
            }
        }
        if bridge.is_closed() {
            return Ok(());
        }
        thread::sleep(SUPERVISOR_POLL_INTERVAL);
    }
}

fn dispatch_native_request(
    broker: &Broker,
    authority: &AuthorityTicket,
    request: &NativeRequest,
    tickets: &mut SidePanelTicketRegistry,
) -> Result<Value, CoreError> {
    let params = request.params.as_object().ok_or_else(|| {
        CoreError::invalid_argument("Native Messaging request params must be an object")
    })?;
    match request.method.as_str() {
        "space.create" => {
            ensure_allowed_params(params, &["label"])?;
            let label = required_string_param(params, "label")?;
            let space = broker
                .create_space(authority, label.to_owned())
                .map_err(|error| error.as_core_error())?;
            Ok(json!({
                "space": &space,
                "space_id": &space.space_id,
                "lifecycle": &space.lifecycle,
            }))
        }
        "space.takeover" => {
            ensure_allowed_params(params, &["space_id", "now", "ttl", "intent_ticket"])?;
            let space_id = parse_space_param(params)?;
            let now = timestamp_param(params)?;
            let ttl = optional_u64_param(params, "ttl")?.unwrap_or(DEFAULT_SIDE_PANEL_TTL);
            validate_side_panel_intent(params, "space.takeover", &space_id, tickets)?;
            let takeover = broker
                .takeover(&space_id, authority, now, ttl)
                .map_err(|error| error.as_core_error())?;
            Ok(json!({
                "space_id": &takeover.space_id,
                "lease_epoch": &takeover.lease_epoch,
                "fence_acknowledged": takeover.fence_acknowledged,
                "lifecycle": &takeover.lifecycle,
            }))
        }
        "space.return" | "space.return_control" => {
            ensure_allowed_params(params, &["space_id", "lease_epoch", "now", "intent_ticket"])?;
            let space_id = parse_space_param(params)?;
            let lease_epoch = lease_epoch_param(broker, authority, &space_id, params)?;
            let now = timestamp_param(params)?;
            validate_side_panel_intent(params, "space.return_control", &space_id, tickets)?;
            let returned = broker
                .return_control(&space_id, authority, lease_epoch, now)
                .map_err(|error| error.as_core_error())?;
            let control_ticket = json!({
                "space_id": returned.control_ticket.space_id(),
                "broker_epoch": returned.control_ticket.broker_epoch(),
                "fence_epoch": returned.control_ticket.fence_epoch(),
                "token": returned.control_ticket.token().to_string(),
            });
            Ok(json!({
                "space_id": &returned.space_id,
                "released_epoch": &returned.released_epoch,
                "fence_epoch": &returned.fence_epoch,
                "lifecycle": &returned.lifecycle,
                "control_ticket": control_ticket,
            }))
        }
        "space.finish" => {
            ensure_allowed_params(params, &["space_id", "lease_epoch", "now", "intent_ticket"])?;
            let space_id = parse_space_param(params)?;
            let lease_epoch = lease_epoch_param(broker, authority, &space_id, params)?;
            let now = timestamp_param(params)?;
            validate_side_panel_intent(params, "space.finish", &space_id, tickets)?;
            let space = broker
                .finish_space(&space_id, authority, lease_epoch, now)
                .map_err(|error| error.as_core_error())?;
            Ok(json!({
                "space": &space,
                "space_id": &space.space_id,
                "lifecycle": &space.lifecycle,
            }))
        }
        "space.release" => {
            ensure_allowed_params(params, &["space_id", "lease_epoch", "now", "intent_ticket"])?;
            let space_id = parse_space_param(params)?;
            let lease_epoch = lease_epoch_param(broker, authority, &space_id, params)?;
            let now = timestamp_param(params)?;
            validate_side_panel_intent(params, "space.release", &space_id, tickets)?;
            let space = broker
                .release_space(&space_id, authority, lease_epoch, now)
                .map_err(|error| error.as_core_error())?;
            Ok(json!({
                "space": &space,
                "space_id": &space.space_id,
                "lifecycle": &space.lifecycle,
            }))
        }
        _ => Err(CoreError::new(
            ErrorCode::InvalidArgument,
            "Native Messaging method is not allowlisted",
        )),
    }
}

fn validate_side_panel_intent(
    params: &Map<String, Value>,
    method: &str,
    space_id: &SpaceId,
    tickets: &mut SidePanelTicketRegistry,
) -> Result<(), CoreError> {
    let ticket = params
        .get("intent_ticket")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::PermissionDenied,
                "side-panel intent ticket is required",
            )
        })?;
    if ticket.get("issued_by_host") != Some(&Value::Bool(true))
        || ticket.get("purpose").and_then(Value::as_str) != Some("sidepanel")
        || ticket.get("space_id").and_then(Value::as_str) != Some(space_id.as_str())
        || ticket
            .get("ticket_id")
            .and_then(Value::as_str)
            .is_none_or(|value| !is_logical_ticket_id(value))
        || ticket
            .get("expires_at")
            .and_then(Value::as_u64)
            .is_none_or(|value| value <= current_millis())
    {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "side-panel intent ticket is invalid or expired",
        ));
    }
    let ticket_id = ticket
        .get("ticket_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let action = ticket.get("action").and_then(Value::as_str).unwrap_or("");
    let Some((issued_space, issued_action, issued_expires)) = tickets.issued.get(ticket_id) else {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "side-panel intent ticket is unknown",
        ));
    };
    if tickets.used.contains(ticket_id)
        || issued_space != space_id.as_str()
        || *issued_expires <= current_millis()
        || tickets.profile_instance_id.as_deref()
            != ticket.get("profile_instance_id").and_then(Value::as_str)
        || tickets.browser_session_epoch
            != ticket.get("browser_session_epoch").and_then(Value::as_u64)
    {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "side-panel intent ticket is expired, replayed, or mis-scoped",
        ));
    }
    if issued_action != action {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "side-panel intent ticket action does not match the issued ticket",
        ));
    }
    let valid = match method {
        "space.takeover" => action == "takeover" || action == "retain",
        "space.return_control" => matches!(action, "return_control" | "stop" | "pause" | "handoff"),
        "space.finish" => action == "finish",
        "space.release" => action == "release",
        _ => false,
    };
    if !valid {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "side-panel intent ticket does not authorize this method",
        ));
    }
    tickets.used.insert(ticket_id.to_owned());
    Ok(())
}

fn is_logical_ticket_id(value: &str) -> bool {
    (8..=128).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
}

fn ensure_allowed_params(params: &Map<String, Value>, allowed: &[&str]) -> Result<(), CoreError> {
    if let Some(name) = params.keys().find(|name| !allowed.contains(&name.as_str())) {
        return Err(CoreError::invalid_argument(format!(
            "Native Messaging parameter is not allowlisted: {name}"
        )));
    }
    Ok(())
}

fn required_string_param<'a>(
    params: &'a Map<String, Value>,
    name: &str,
) -> Result<&'a str, CoreError> {
    params
        .get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| CoreError::invalid_argument(format!("{name} is missing or invalid")))
}

fn optional_u64_param(params: &Map<String, Value>, name: &str) -> Result<Option<u64>, CoreError> {
    params
        .get(name)
        .map(|value| {
            value
                .as_u64()
                .ok_or_else(|| CoreError::invalid_argument(format!("{name} is invalid")))
        })
        .transpose()
}

fn parse_space_param(params: &Map<String, Value>) -> Result<SpaceId, CoreError> {
    let value = required_string_param(params, "space_id")?;
    value
        .parse::<SpaceId>()
        .map_err(|error| CoreError::invalid_argument(format!("invalid space_id: {error}")))
}

fn timestamp_param(params: &Map<String, Value>) -> Result<Timestamp, CoreError> {
    Ok(Timestamp::new(
        optional_u64_param(params, "now")?.unwrap_or_else(current_millis),
    ))
}

fn lease_epoch_param(
    broker: &Broker,
    authority: &AuthorityTicket,
    space_id: &SpaceId,
    params: &Map<String, Value>,
) -> Result<LeaseEpoch, CoreError> {
    if let Some(epoch) = optional_u64_param(params, "lease_epoch")? {
        if epoch == 0 {
            return Err(CoreError::invalid_argument("lease_epoch must be positive"));
        }
        return Ok(LeaseEpoch::new(epoch));
    }
    let space = broker
        .describe_space(authority, space_id)
        .map_err(|error| error.as_core_error())?;
    space
        .lease
        .map(|lease| lease.lease_epoch)
        .ok_or_else(|| CoreError::invalid_argument("lease_epoch is required for this transition"))
}

fn current_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| {
            duration.as_millis().min(u128::from(u64::MAX)) as u64
        })
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
    use super::*;
    use agentyc_host::{FakeBridge, NativeHello};
    use serde_json::json;
    use tempfile::tempdir;

    fn test_broker() -> (tempfile::TempDir, Broker, AuthorityTicket) {
        let directory = tempdir().expect("temporary ledger");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let native_hello = NativeHello {
            protocol: agentyc_core::PROTOCOL_VERSION,
            nonce: "nonce_native_test".to_owned(),
            sequence: 1,
            worker_instance_epoch: 1,
            browser_session_epoch: 1,
            profile_instance_id: "profile_native_test".to_owned(),
            extension_version: "0.1.0".to_owned(),
            capabilities: vec!["logical_tabs".to_owned()],
        };
        let hello = native_hello
            .to_core_hello(PrincipalId::from_suffix("extension").expect("principal"))
            .expect("core hello");
        let connection = broker.hello(&hello).expect("connection");
        (directory, broker, connection.authority().clone())
    }

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
    fn side_panel_create_uses_the_authenticated_broker_authority() {
        let (_directory, broker, authority) = test_broker();
        let mut tickets = SidePanelTicketRegistry::default();
        let request = NativeRequest {
            request_id: "req_side_panel_create".to_owned(),
            action_id: Some("action_side_panel_create".to_owned()),
            method: "space.create".to_owned(),
            params: json!({"label": "panel"}),
        };

        let result =
            dispatch_native_request(&broker, &authority, &request, &mut tickets).expect("create");
        assert_eq!(result["space_id"], json!("space_space-1"));
        assert_eq!(
            broker.list_spaces(&authority).expect("spaces").len(),
            1,
            "the request must mutate the same broker authority"
        );
    }

    #[test]
    fn side_panel_dispatch_rejects_unknown_methods_and_malformed_scopes() {
        let (_directory, broker, authority) = test_broker();
        let mut tickets = SidePanelTicketRegistry::default();
        let unknown = NativeRequest {
            request_id: "req_side_panel_unknown".to_owned(),
            action_id: None,
            method: "space.takeover_with_control_ticket".to_owned(),
            params: json!({}),
        };
        let unknown_error = dispatch_native_request(&broker, &authority, &unknown, &mut tickets)
            .expect_err("control-ticket takeover must not be exposed");
        assert_eq!(unknown_error.code, ErrorCode::InvalidArgument);

        let malformed_scope = NativeRequest {
            request_id: "req_side_panel_scope".to_owned(),
            action_id: None,
            method: "space.takeover".to_owned(),
            params: json!({"space_id": "tab_7", "ttl": 60_000}),
        };
        let scope_error =
            dispatch_native_request(&broker, &authority, &malformed_scope, &mut tickets)
                .expect_err("raw browser scope must be rejected");
        assert_eq!(scope_error.code, ErrorCode::InvalidArgument);

        let extra_raw_id = NativeRequest {
            request_id: "req_side_panel_raw".to_owned(),
            action_id: None,
            method: "space.release".to_owned(),
            params: json!({"space_id": "space_panel", "tab_id": 7}),
        };
        let raw_id_error =
            dispatch_native_request(&broker, &authority, &extra_raw_id, &mut tickets)
                .expect_err("raw browser parameter must be rejected");
        assert_eq!(raw_id_error.code, ErrorCode::InvalidArgument);
    }
}
