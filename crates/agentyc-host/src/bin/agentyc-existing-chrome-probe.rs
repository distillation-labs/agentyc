//! Existing-Chrome coexistence probe over the owner-only local host socket.
//!
//! This executable never launches Chrome, never uses CDP, and never handles raw
//! browser identifiers. It verifies host/extension connectivity through the
//! existing logical protocol and executes safe logical coexistence checkpoints.

use std::{
    collections::BTreeMap,
    env,
    path::PathBuf,
    process::ExitCode,
    time::{SystemTime, UNIX_EPOCH},
};

use agentyc_core::{
    ClientId, ClientMetadata, ConnectionNonce, ErrorCode, HelloEnvelope, PROTOCOL_VERSION,
    PrincipalId,
};
use agentyc_host::{LocalSocketClient, configured_socket_path};
use serde::{Deserialize, Serialize};

const CHECKPOINTS: [&str; 9] = [
    "connect.local_socket",
    "connect.host_extension",
    "scenario.two_spaces",
    "scenario.page_create_list",
    "scenario.isolation",
    "scenario.lease_takeover",
    "scenario.return_control",
    "scenario.cleanup",
    "scenario.cleanup_returned_space",
];

#[derive(Debug, Clone)]
struct ProbeConfig {
    socket_path: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
enum CheckpointStatus {
    Passed,
    Failed,
    Skipped,
}

#[derive(Debug, Clone, Serialize)]
struct CheckpointResult {
    name: String,
    status: CheckpointStatus,
    detail: String,
}

#[derive(Debug, Clone, Serialize)]
struct ProbeReport {
    success: bool,
    socket_path: String,
    broker_epoch: Option<u64>,
    connection_epoch: Option<u64>,
    checkpoints: Vec<CheckpointResult>,
    limitations: Vec<String>,
}

impl ProbeReport {
    fn new(socket_path: PathBuf) -> Self {
        Self {
            success: false,
            socket_path: socket_path.display().to_string(),
            broker_epoch: None,
            connection_epoch: None,
            checkpoints: Vec::new(),
            limitations: Vec::new(),
        }
    }

    fn pass(&mut self, name: &str, detail: impl Into<String>) {
        self.checkpoints.push(CheckpointResult {
            name: name.to_owned(),
            status: CheckpointStatus::Passed,
            detail: detail.into(),
        });
    }

    fn fail(&mut self, name: &str, detail: impl Into<String>) {
        self.checkpoints.push(CheckpointResult {
            name: name.to_owned(),
            status: CheckpointStatus::Failed,
            detail: detail.into(),
        });
    }

    fn skip(&mut self, name: &str, detail: impl Into<String>) {
        self.checkpoints.push(CheckpointResult {
            name: name.to_owned(),
            status: CheckpointStatus::Skipped,
            detail: detail.into(),
        });
    }
}

#[derive(Debug, Deserialize)]
struct LeaseView {
    lease_epoch: u64,
}

#[derive(Debug, Deserialize)]
struct PageView {
    page_id: String,
}

#[derive(Debug)]
struct SpaceLease {
    space_id: String,
    lease_epoch: u64,
}

enum ProbeOutcome {
    Passed(ProbeReport),
    Failed(ProbeReport),
}

fn main() -> ExitCode {
    let outcome = match parse_config() {
        Ok(config) => run_probe(config),
        Err(error) => {
            let mut report = ProbeReport::new(PathBuf::from(""));
            report.fail(CHECKPOINTS[0], error);
            for checkpoint in &CHECKPOINTS[1..] {
                report.skip(checkpoint, "not run after earlier failure");
            }
            ProbeOutcome::Failed(report)
        }
    };

    match outcome {
        ProbeOutcome::Passed(mut report) => {
            report.success = true;
            emit_report(&report);
            ExitCode::SUCCESS
        }
        ProbeOutcome::Failed(report) => {
            emit_report(&report);
            ExitCode::from(1)
        }
    }
}

fn emit_report(report: &ProbeReport) {
    match serde_json::to_string_pretty(report) {
        Ok(text) => println!("{text}"),
        Err(error) => {
            eprintln!("failed to serialize probe report: {error}");
            println!("{{\"success\":false,\"serialization_error\":true}}");
        }
    }
}

fn parse_config() -> Result<ProbeConfig, String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let mut socket_path = None;
    let mut index = 0;

    while index < arguments.len() {
        let argument = &arguments[index];
        if let Some(value) = argument.strip_prefix("--socket=") {
            if value.is_empty() {
                return Err("--socket must not be empty".to_owned());
            }
            socket_path = Some(PathBuf::from(value));
            index += 1;
            continue;
        }

        if argument == "--socket" {
            let Some(value) = arguments.get(index + 1) else {
                return Err("--socket requires a value".to_owned());
            };
            if value.trim().is_empty() {
                return Err("--socket must not be empty".to_owned());
            }
            socket_path = Some(PathBuf::from(value));
            index += 2;
            continue;
        }

        return Err(format!("unexpected argument: {argument}"));
    }

    let socket_path = socket_path.unwrap_or_else(|| configured_socket_path(state_directory()));
    Ok(ProbeConfig { socket_path })
}

fn state_directory() -> PathBuf {
    if let Ok(path) = env::var("AGENTYC_STATE_DIR")
        && !path.trim().is_empty()
    {
        return PathBuf::from(path);
    }
    let home = env::var("HOME").unwrap_or_else(|_| ".".to_owned());
    PathBuf::from(home).join(".agentyc").join("state")
}

fn run_probe(config: ProbeConfig) -> ProbeOutcome {
    let mut report = ProbeReport::new(config.socket_path.clone());

    let hello = match probe_hello() {
        Ok(hello) => hello,
        Err(error) => return fail_closed(report, 0, error),
    };

    let client = match LocalSocketClient::connect(&config.socket_path, hello) {
        Ok(client) => {
            report.broker_epoch = Some(client.hello_ok().broker_epoch.get());
            report.connection_epoch = Some(client.hello_ok().connection_epoch.get());
            report.pass(
                CHECKPOINTS[0],
                format!(
                    "connected (broker_epoch={}, connection_epoch={})",
                    client.hello_ok().broker_epoch.get(),
                    client.hello_ok().connection_epoch.get(),
                ),
            );
            client
        }
        Err(error) => {
            return fail_closed(report, 0, format!("local socket connect failed: {error}"));
        }
    };

    let status = match client.request("host.status", BTreeMap::new()) {
        Ok(status) => status,
        Err(error) => {
            return fail_closed(
                report,
                1,
                format!("host.status failed: {error} ({:?})", error.as_core_error().code),
            );
        }
    };

    let lifecycle: String = match field(&status, "lifecycle") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 1, error),
    };
    let capabilities: Vec<String> = match field(&status, "capabilities") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 1, error),
    };

    if lifecycle != "ready" {
        return fail_closed(
            report,
            1,
            format!("host lifecycle is {lifecycle}, expected ready"),
        );
    }
    if !capabilities.iter().any(|value| value == "action") {
        return fail_closed(
            report,
            1,
            format!(
                "extension action capability is unavailable; advertised capabilities: {}",
                capabilities.join(",")
            ),
        );
    }
    report.pass(
        CHECKPOINTS[1],
        format!("host ready; extension capabilities={}", capabilities.join(",")),
    );

    let run_tag = format!("{}", now_millis() % 1_000_000_000);
    let mut one = match create_space_with_lease(&client, &format!("probe-{run_tag}-one"), 1) {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 2, error),
    };
    let two = match create_space_with_lease(&client, &format!("probe-{run_tag}-two"), 2) {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 2, error),
    };
    report.pass(
        CHECKPOINTS[2],
        format!(
            "created spaces {} (lease={}) and {} (lease={})",
            one.space_id, one.lease_epoch, two.space_id, two.lease_epoch
        ),
    );

    let page_one = match create_page(&client, &one.space_id, one.lease_epoch, "probe-page-one", 3) {
        Ok(page_id) => page_id,
        Err(error) => return fail_closed(report, 3, error),
    };
    let page_two = match create_page(&client, &two.space_id, two.lease_epoch, "probe-page-two", 4) {
        Ok(page_id) => page_id,
        Err(error) => return fail_closed(report, 3, error),
    };

    if let Err(error) = assert_page_list_contains(&client, &one.space_id, &page_one) {
        return fail_closed(report, 3, error);
    }
    if let Err(error) = assert_page_list_contains(&client, &two.space_id, &page_two) {
        return fail_closed(report, 3, error);
    }
    report.pass(
        CHECKPOINTS[3],
        format!(
            "created and listed pages {} in {} and {} in {}",
            page_one, one.space_id, page_two, two.space_id
        ),
    );

    if let Err(error) = assert_page_list_excludes(&client, &one.space_id, &page_two) {
        return fail_closed(report, 4, error);
    }
    if let Err(error) = assert_page_list_excludes(&client, &two.space_id, &page_one) {
        return fail_closed(report, 4, error);
    }

    let stale_params = BTreeMap::from([
        ("space_id".to_owned(), one.space_id.clone()),
        ("lease_epoch".to_owned(), "0".to_owned()),
        ("label".to_owned(), "stale-lease-check".to_owned()),
        ("now".to_owned(), "5".to_owned()),
    ]);

    match client.request("page.create", stale_params) {
        Ok(_) => {
            return fail_closed(
                report,
                4,
                "stale lease mutation unexpectedly succeeded".to_owned(),
            );
        }
        Err(error) => {
            let code = error.as_core_error().code;
            if code != ErrorCode::StaleLease {
                return fail_closed(
                    report,
                    4,
                    format!("unexpected stale lease error code: {code:?}"),
                );
            }
            report.pass(
                CHECKPOINTS[4],
                "space listings stayed isolated and stale lease mutation was rejected",
            );
        }
    }

    let takeover = match client.request(
        "space.takeover",
        BTreeMap::from([
            ("space_id".to_owned(), one.space_id.clone()),
            ("ttl".to_owned(), "600".to_owned()),
            ("now".to_owned(), "6".to_owned()),
        ]),
    ) {
        Ok(value) => value,
        Err(error) => {
            return fail_closed(
                report,
                5,
                format!("space.takeover failed: {error} ({:?})", error.as_core_error().code),
            );
        }
    };

    let takeover_epoch: u64 = match field(&takeover, "lease_epoch") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 5, error),
    };
    let fence_acknowledged: bool = match field(&takeover, "fence_acknowledged") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 5, error),
    };
    let takeover_lifecycle: String = match field(&takeover, "lifecycle") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 5, error),
    };
    if !fence_acknowledged || takeover_lifecycle != "agent_owned" {
        return fail_closed(
            report,
            5,
            format!(
                "takeover did not reach acknowledged agent ownership (fence_acknowledged={fence_acknowledged}, lifecycle={takeover_lifecycle})"
            ),
        );
    }
    one.lease_epoch = takeover_epoch;
    report.pass(
        CHECKPOINTS[5],
        format!("takeover acknowledged with lease_epoch={takeover_epoch}"),
    );

    let returned = match client.request(
        "space.return",
        BTreeMap::from([
            ("space_id".to_owned(), two.space_id.clone()),
            ("lease_epoch".to_owned(), two.lease_epoch.to_string()),
            ("now".to_owned(), "7".to_owned()),
        ]),
    ) {
        Ok(value) => value,
        Err(error) => {
            return fail_closed(
                report,
                6,
                format!("space.return failed: {error} ({:?})", error.as_core_error().code),
            );
        }
    };
    let return_lifecycle: String = match field(&returned, "lifecycle") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 6, error),
    };
    if return_lifecycle != "user_owned" {
        return fail_closed(
            report,
            6,
            format!("space.return lifecycle is {return_lifecycle}, expected user_owned"),
        );
    }

    match client.request(
        "lease.acquire",
        BTreeMap::from([
            ("space_id".to_owned(), two.space_id.clone()),
            ("ttl".to_owned(), "600".to_owned()),
            ("now".to_owned(), "8".to_owned()),
        ]),
    ) {
        Ok(_) => {
            return fail_closed(
                report,
                6,
                "returned space unexpectedly allowed a fresh lease without ticketed reconciliation"
                    .to_owned(),
            );
        }
        Err(error) => {
            if error.as_core_error().code != ErrorCode::UserControlRequired {
                return fail_closed(
                    report,
                    6,
                    format!(
                        "returned space lease rejection code was {:?}, expected UserControlRequired",
                        error.as_core_error().code
                    ),
                );
            }
        }
    }
    report.pass(
        CHECKPOINTS[6],
        format!("space {} returned to user control", two.space_id),
    );

    let finished = match client.request(
        "space.finish",
        BTreeMap::from([
            ("space_id".to_owned(), one.space_id.clone()),
            ("lease_epoch".to_owned(), one.lease_epoch.to_string()),
            ("now".to_owned(), "9".to_owned()),
        ]),
    ) {
        Ok(value) => value,
        Err(error) => {
            return fail_closed(
                report,
                7,
                format!("space.finish failed: {error} ({:?})", error.as_core_error().code),
            );
        }
    };
    let finished_lifecycle: String = match field(&finished, "lifecycle") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 7, error),
    };
    if finished_lifecycle != "finished" {
        return fail_closed(
            report,
            7,
            format!("space.finish lifecycle is {finished_lifecycle}, expected finished"),
        );
    }

    let released = match client.request(
        "space.release",
        BTreeMap::from([
            ("space_id".to_owned(), one.space_id.clone()),
            ("lease_epoch".to_owned(), one.lease_epoch.to_string()),
            ("now".to_owned(), "10".to_owned()),
        ]),
    ) {
        Ok(value) => value,
        Err(error) => {
            return fail_closed(
                report,
                7,
                format!("space.release failed: {error} ({:?})", error.as_core_error().code),
            );
        }
    };
    let released_lifecycle: String = match field(&released, "lifecycle") {
        Ok(value) => value,
        Err(error) => return fail_closed(report, 7, error),
    };
    if released_lifecycle != "released" {
        return fail_closed(
            report,
            7,
            format!("space.release lifecycle is {released_lifecycle}, expected released"),
        );
    }
    report.pass(
        CHECKPOINTS[7],
        format!("finished and released {}", one.space_id),
    );

    report.skip(
        CHECKPOINTS[8],
        "local protocol does not expose ticketed reclaim; returned space remains user_owned",
    );
    report.limitations.push(
        "A space returned with space.return cannot be reclaimed through the current local protocol because ticketed takeover is not exposed; cleanup of that returned space requires a separate host path."
            .to_owned(),
    );

    ProbeOutcome::Passed(report)
}

fn fail_closed(mut report: ProbeReport, failed_index: usize, detail: String) -> ProbeOutcome {
    report.fail(CHECKPOINTS[failed_index], detail);
    for checkpoint in &CHECKPOINTS[(failed_index + 1)..] {
        report.skip(checkpoint, "not run after earlier failure");
    }
    ProbeOutcome::Failed(report)
}

fn probe_hello() -> Result<HelloEnvelope, String> {
    let nonce_seed = now_millis();
    let principal = PrincipalId::from_suffix("existing-chrome-probe")
        .map_err(|error| format!("invalid probe principal: {error}"))?;
    let client_id = ClientId::from_suffix("existing-chrome-probe")
        .map_err(|error| format!("invalid probe client id: {error}"))?;
    let nonce = ConnectionNonce::from_suffix(format!(
        "probe-{}-{}",
        std::process::id(),
        nonce_seed % 1_000_000_000
    ))
    .map_err(|error| format!("invalid probe connection nonce: {error}"))?;

    Ok(HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: principal,
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: Some(client_id),
            client_name: Some("agentyc-existing-chrome-probe".to_owned()),
            client_version: Some(env!("CARGO_PKG_VERSION").to_owned()),
            connection_nonce: Some(nonce),
            profile_binding_id: None,
        }),
    })
}

fn create_space_with_lease(
    client: &LocalSocketClient,
    label: &str,
    now: u64,
) -> Result<SpaceLease, String> {
    let created = client
        .request(
            "space.create",
            BTreeMap::from([("label".to_owned(), label.to_owned())]),
        )
        .map_err(|error| format!("space.create failed: {error} ({:?})", error.as_core_error().code))?;
    let space_id: String = field(&created, "space_id")?;

    let claimed = client
        .request(
            "lease.acquire",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("ttl".to_owned(), "600".to_owned()),
                ("now".to_owned(), now.to_string()),
            ]),
        )
        .map_err(|error| format!("lease.acquire failed: {error} ({:?})", error.as_core_error().code))?;
    let lease: LeaseView = field(&claimed, "lease")?;

    Ok(SpaceLease {
        space_id,
        lease_epoch: lease.lease_epoch,
    })
}

fn create_page(
    client: &LocalSocketClient,
    space_id: &str,
    lease_epoch: u64,
    label: &str,
    now: u64,
) -> Result<String, String> {
    let created = client
        .request(
            "page.create",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.to_owned()),
                ("lease_epoch".to_owned(), lease_epoch.to_string()),
                ("label".to_owned(), label.to_owned()),
                ("now".to_owned(), now.to_string()),
            ]),
        )
        .map_err(|error| format!("page.create failed: {error} ({:?})", error.as_core_error().code))?;
    field(&created, "page_id")
}

fn assert_page_list_contains(
    client: &LocalSocketClient,
    space_id: &str,
    expected_page_id: &str,
) -> Result<(), String> {
    let listed = client
        .request(
            "page.list",
            BTreeMap::from([("space_id".to_owned(), space_id.to_owned())]),
        )
        .map_err(|error| format!("page.list failed: {error} ({:?})", error.as_core_error().code))?;
    let pages: Vec<PageView> = field(&listed, "pages")?;
    if pages.iter().any(|page| page.page_id == expected_page_id) {
        return Ok(());
    }
    Err(format!(
        "page.list for {space_id} does not include expected page {expected_page_id}"
    ))
}

fn assert_page_list_excludes(
    client: &LocalSocketClient,
    space_id: &str,
    other_page_id: &str,
) -> Result<(), String> {
    let listed = client
        .request(
            "page.list",
            BTreeMap::from([("space_id".to_owned(), space_id.to_owned())]),
        )
        .map_err(|error| format!("page.list failed: {error} ({:?})", error.as_core_error().code))?;
    let pages: Vec<PageView> = field(&listed, "pages")?;
    if pages.iter().any(|page| page.page_id == other_page_id) {
        return Err(format!(
            "page.list for {space_id} unexpectedly includes foreign page {other_page_id}"
        ));
    }
    Ok(())
}

fn field<T: for<'de> Deserialize<'de>>(
    map: &BTreeMap<String, String>,
    key: &str,
) -> Result<T, String> {
    let Some(raw) = map.get(key) else {
        return Err(format!("response field {key} is missing"));
    };
    serde_json::from_str(raw)
        .map_err(|error| format!("response field {key} is invalid JSON: {error}"))
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_socket_argument_and_rejects_unknown_flags() {
        let config = parse_config_from([
            "agentyc-existing-chrome-probe",
            "--socket",
            "/tmp/agentyc.sock",
        ])
        .expect("config");
        assert_eq!(config.socket_path, PathBuf::from("/tmp/agentyc.sock"));
        assert!(parse_config_from(["agentyc-existing-chrome-probe", "--unexpected"]).is_err());
    }

    #[test]
    fn fail_closed_marks_following_checkpoints_skipped() {
        let report = ProbeReport::new(PathBuf::from("/tmp/example.sock"));
        let ProbeOutcome::Failed(report) = fail_closed(report, 2, "boom".to_owned()) else {
            panic!("expected failed outcome");
        };
        assert_eq!(report.checkpoints.len(), CHECKPOINTS.len() - 2);
        assert!(matches!(
            report.checkpoints[0].status,
            CheckpointStatus::Failed
        ));
        for checkpoint in report.checkpoints.iter().skip(1) {
            assert!(matches!(checkpoint.status, CheckpointStatus::Skipped));
        }
    }

    fn parse_config_from<I, S>(arguments: I) -> Result<ProbeConfig, String>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let arguments = arguments
            .into_iter()
            .map(Into::into)
            .skip(1)
            .collect::<Vec<_>>();
        parse_config_with_arguments(arguments)
    }

    fn parse_config_with_arguments(arguments: Vec<String>) -> Result<ProbeConfig, String> {
        let mut socket_path = None;
        let mut index = 0;
        while index < arguments.len() {
            let argument = &arguments[index];
            if let Some(value) = argument.strip_prefix("--socket=") {
                if value.is_empty() {
                    return Err("--socket must not be empty".to_owned());
                }
                socket_path = Some(PathBuf::from(value));
                index += 1;
                continue;
            }
            if argument == "--socket" {
                let Some(value) = arguments.get(index + 1) else {
                    return Err("--socket requires a value".to_owned());
                };
                if value.trim().is_empty() {
                    return Err("--socket must not be empty".to_owned());
                }
                socket_path = Some(PathBuf::from(value));
                index += 2;
                continue;
            }
            return Err(format!("unexpected argument: {argument}"));
        }
        Ok(ProbeConfig {
            socket_path: socket_path.unwrap_or_else(|| PathBuf::from("fallback.sock")),
        })
    }
}
