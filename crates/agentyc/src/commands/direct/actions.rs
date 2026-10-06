use std::collections::BTreeMap;

use agentyc_core::{
    ActionOperation, ActionRequest, ContentHash, IdempotencyKey, Postcondition, RequestId,
};
use agentyc_host::{
    ACTIONABILITY_PAYLOAD_KEY, ELEMENT_REF_PAYLOAD_KEY, EVIDENCE_PAYLOAD_KEY,
    PROVENANCE_PAYLOAD_KEY, REF_PAYLOAD_KEY, canonical_action_hash, request_requires_intent,
};
use serde_json::{Value, json};

use super::{
    ActionCommand, ActionExecuteArgs, ActionReconcileArgs, ActionStatusArgs, DirectContext,
    DirectResult, host_error, lease_epoch, parse_action, parse_page, parse_space, parse_value,
    remote_field, remote_string, timestamp,
};

const MAX_ACTION_JSON_BYTES: usize = 64 * 1024;
const MAX_ACTION_PAYLOAD_FIELDS: usize = 256;
const MAX_ACTION_KEY_BYTES: usize = 128;
const MAX_ACTION_VALUE_BYTES: usize = 65_536;

pub(super) fn run(context: &DirectContext, command: ActionCommand) -> DirectResult<Value> {
    match command {
        ActionCommand::Execute(args) => execute(context, args),
        ActionCommand::Status(args) => status(context, args),
        ActionCommand::Reconcile(args) => reconcile(context, args),
    }
}

fn execute(context: &DirectContext, args: ActionExecuteArgs) -> DirectResult<Value> {
    let request_id = parse_value::<RequestId>(&args.request_id, "request_id")?;
    let action_id = parse_action(&args.action_id)?;
    let idempotency_key = parse_value::<IdempotencyKey>(&args.idempotency_key, "idempotency_key")?;
    let space_id = parse_space(&args.space_id)?;
    let page_id = args.page_id.as_deref().map(parse_page).transpose()?;
    let operation = parse_operation(&args.operation)?;
    let payload_supplied = args.payload.is_some();
    let payload = parse_payload(args.payload.as_deref())?;
    validate_page_operation(operation, page_id.as_ref(), &payload)?;
    if request_requires_intent(operation, &payload) {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::PermissionDenied,
            "sensitive action requires a host-issued user-intent ticket; the direct CLI has no ticket-issuance flow",
        ));
    }
    let postcondition = parse_postcondition(args.postcondition.as_deref())?;
    let now = timestamp(args.now);

    if let Some((broker, authority)) = context.local() {
        let mut request = ActionRequest {
            request_id,
            action_id,
            idempotency_key,
            request_hash: ContentHash::from_bytes(b"direct-cli-action"),
            space_id,
            page_id,
            lease_epoch: lease_epoch(args.lease_epoch),
            operation,
            payload,
            postcondition,
        };
        request.request_hash = canonical_action_hash(&request).map_err(host_error)?;
        let result = broker
            .execute_action(request, authority, now)
            .map_err(host_error)?;
        return Ok(json!({
            "action_id": result.receipt.action_id,
            "receipt": result.receipt
        }));
    }

    let mut params = BTreeMap::from([
        ("request_id".to_owned(), request_id.to_string()),
        ("action_id".to_owned(), action_id.to_string()),
        ("idempotency_key".to_owned(), idempotency_key.to_string()),
        ("space_id".to_owned(), space_id.to_string()),
        ("lease_epoch".to_owned(), args.lease_epoch.to_string()),
        ("operation".to_owned(), operation_name(operation).to_owned()),
        ("now".to_owned(), now.get().to_string()),
    ]);
    if let Some(page_id) = page_id {
        params.insert("page_id".to_owned(), page_id.to_string());
    }
    if payload_supplied {
        params.insert(
            "payload".to_owned(),
            serde_json::to_string(&payload).map_err(|error| {
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::InvalidJson,
                    format!("action payload cannot be serialized: {error}"),
                )
            })?,
        );
    }
    if let Some(postcondition) = &postcondition {
        params.insert(
            "postcondition".to_owned(),
            serde_json::to_string(postcondition).map_err(|error| {
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::InvalidJson,
                    format!("action postcondition cannot be serialized: {error}"),
                )
            })?,
        );
    }
    let response = context.request("action.execute", params)?;
    Ok(json!({
        "action_id": remote_string(&response, "action_id")?,
        "receipt": remote_field(&response, "receipt")?,
    }))
}

fn parse_operation(value: &str) -> DirectResult<ActionOperation> {
    serde_json::from_value(Value::String(value.to_owned())).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!("invalid operation: {error}"))
    })
}

fn validate_page_operation(
    operation: ActionOperation,
    page_id: Option<&agentyc_core::PageId>,
    payload: &BTreeMap<String, String>,
) -> DirectResult<()> {
    let requires_page = matches!(
        operation,
        ActionOperation::Navigate | ActionOperation::Close
    );
    if requires_page && page_id.is_none() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "navigate and close actions require --page-id",
        ));
    }

    if operation == ActionOperation::Navigate {
        let url = payload.get("url").map(String::as_str).unwrap_or_default();
        if url.is_empty() || url.len() > 4_096 {
            return Err(agentyc_core::CoreError::invalid_argument(
                "navigate action requires a non-empty URL up to 4096 bytes",
            ));
        }
    }
    Ok(())
}

fn operation_name(operation: ActionOperation) -> &'static str {
    match operation {
        ActionOperation::Navigate => "navigate",
        ActionOperation::Click => "click",
        ActionOperation::Input => "input",
        ActionOperation::Evaluate => "evaluate",
        ActionOperation::Scroll => "scroll",
        ActionOperation::Wait => "wait",
        ActionOperation::Screenshot => "screenshot",
        ActionOperation::StorageWrite => "storage_write",
        ActionOperation::CookieWrite => "cookie_write",
        ActionOperation::Upload => "upload",
        ActionOperation::Close => "close",
    }
}

fn parse_payload(value: Option<&str>) -> DirectResult<BTreeMap<String, String>> {
    let Some(value) = value else {
        return Ok(BTreeMap::new());
    };
    ensure_json_bound(value, "payload")?;
    let object = serde_json::from_str::<Value>(value)
        .map_err(|error| {
            agentyc_core::CoreError::invalid_argument(format!("invalid payload: {error}"))
        })?
        .as_object()
        .cloned()
        .ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument("payload must be a JSON object")
        })?;
    if object.len() > MAX_ACTION_PAYLOAD_FIELDS {
        return Err(agentyc_core::CoreError::invalid_argument(
            "payload has too many fields",
        ));
    }

    object
        .into_iter()
        .try_fold(BTreeMap::new(), |mut payload, (key, value)| {
            if key.is_empty()
                || key.len() > MAX_ACTION_KEY_BYTES
                || key.chars().any(char::is_control)
                || forbidden_payload_key(&key)
            {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "payload contains an unsafe field name",
                ));
            }
            let value = match value {
                Value::String(value) => value,
                value
                    if matches!(
                        key.as_str(),
                        ELEMENT_REF_PAYLOAD_KEY
                            | REF_PAYLOAD_KEY
                            | PROVENANCE_PAYLOAD_KEY
                            | ACTIONABILITY_PAYLOAD_KEY
                            | EVIDENCE_PAYLOAD_KEY
                    ) =>
                {
                    serde_json::to_string(&value).map_err(|error| {
                        agentyc_core::CoreError::invalid_argument(format!(
                            "typed actionability field cannot be serialized: {error}"
                        ))
                    })?
                }
                _ => {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "payload values must be JSON strings",
                    ));
                }
            };
            if value.len() > MAX_ACTION_VALUE_BYTES {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "payload value exceeds the 65536-byte limit",
                ));
            }
            payload.insert(key, value);
            Ok(payload)
        })
}

fn parse_postcondition(value: Option<&str>) -> DirectResult<Option<Postcondition>> {
    let Some(value) = value else {
        return Ok(None);
    };
    ensure_json_bound(value, "postcondition")?;
    serde_json::from_str(value).map(Some).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!("invalid postcondition: {error}"))
    })
}

fn ensure_json_bound(value: &str, field: &str) -> DirectResult<()> {
    if value.len() > MAX_ACTION_JSON_BYTES {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "{field} exceeds the {}-byte limit",
            MAX_ACTION_JSON_BYTES
        )));
    }
    Ok(())
}

fn forbidden_payload_key(key: &str) -> bool {
    let normalized: String = key
        .bytes()
        .filter(u8::is_ascii_alphanumeric)
        .map(|byte| byte.to_ascii_lowercase() as char)
        .collect();
    matches!(
        normalized.as_str(),
        "targetid"
            | "sessionid"
            | "tabid"
            | "debuggerid"
            | "browserid"
            | "windowid"
            | "connectionid"
            | "chromeid"
            | "rawid"
    ) || (normalized.contains("target") && normalized.contains("id"))
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::direct::{DirectOptions, exit_code_for, failure, serialize_response};
    use tempfile::tempdir;

    fn context() -> (tempfile::TempDir, DirectContext) {
        let directory = tempdir().expect("temporary state directory");
        let context = DirectContext::open(&DirectOptions {
            state_dir: Some(directory.path().display().to_string()),
            principal: Some("principal_action_test".to_owned()),
            profile_binding_id: None,
            offline: true,
            json: true,
        })
        .expect("offline direct context");
        (directory, context)
    }

    fn execute_args(
        operation: &str,
        page_id: Option<String>,
        payload: Option<String>,
    ) -> ActionExecuteArgs {
        ActionExecuteArgs {
            request_id: "req_action_test".to_owned(),
            action_id: "action_test".to_owned(),
            idempotency_key: "idem_action_test".to_owned(),
            space_id: "space_test".to_owned(),
            page_id,
            lease_epoch: 1,
            operation: operation.to_owned(),
            payload,
            postcondition: None,
            now: Some(1),
        }
    }

    #[test]
    fn navigation_and_close_validate_logical_page_and_navigation_url() {
        let (_directory, context) = context();
        for (operation, payload) in [
            (
                "navigate",
                Some(r#"{"url":"https://example.test"}"#.to_owned()),
            ),
            ("close", None),
        ] {
            let error = execute(&context, execute_args(operation, None, payload))
                .expect_err("page-mutating operation needs a logical page");
            assert_eq!(error.code, agentyc_core::ErrorCode::InvalidArgument);
        }

        let error = execute(
            &context,
            execute_args("navigate", Some("page_test".to_owned()), None),
        )
        .expect_err("navigation needs a URL");
        assert_eq!(error.code, agentyc_core::ErrorCode::InvalidArgument);

        let error = execute(
            &context,
            execute_args(
                "navigate",
                Some("page_test".to_owned()),
                Some(format!(r#"{{"url":"{}"}}"#, "x".repeat(4_097))),
            ),
        )
        .expect_err("navigation URL must stay within the host bound");
        assert_eq!(error.code, agentyc_core::ErrorCode::InvalidArgument);
    }

    #[test]
    fn typed_actionability_payload_fields_accept_json_objects() {
        let payload = parse_payload(Some(
            r#"{
                "element_ref":{"ref_id":"ref_element","element_key":"element_target","space_id":"space_test","page_id":"page_test","frame_id":"frame_main","snapshot_version":1,"document_generation":1,"navigation_generation":1,"refs_epoch":1},
                "provenance":{"space_id":"space_test","page_id":"page_test","snapshot_version":1,"snapshot_hash":"sha256:0000000000000000000000000000000000000000000000000000000000000000","document_generation":1,"navigation_generation":1,"refs_epoch":1,"coherent":true,"coverage":"complete"},
                "actionability_evidence":{"connected":true,"visible":true,"disabled":false,"readonly":false,"covered":false,"overlay_present":false,"hit_target":true,"moving":false,"offscreen":false,"user_control":false,"target_generation":1,"navigation_generation":1,"document_generation":1}
            }"#,
        ))
        .expect("typed actionability payload");
        assert!(
            payload
                .get(ELEMENT_REF_PAYLOAD_KEY)
                .is_some_and(|value| value.starts_with('{'))
        );
        assert!(
            payload
                .get(PROVENANCE_PAYLOAD_KEY)
                .is_some_and(|value| value.starts_with('{'))
        );
        assert!(
            payload
                .get(ACTIONABILITY_PAYLOAD_KEY)
                .is_some_and(|value| value.starts_with('{'))
        );
    }

    #[test]
    fn action_error_serialization_and_exit_code_remain_stable() {
        let (_directory, context) = context();
        let error = execute(&context, execute_args("navigate", None, None))
            .expect_err("navigation without page must fail");
        let response = failure(&error);
        let encoded = serialize_response(&response, true).expect("compact JSON error");
        let decoded: Value = serde_json::from_str(&encoded).expect("valid JSON");
        assert_eq!(decoded["ok"], false);
        assert_eq!(decoded["error"]["code"], "invalid_argument");
        assert_eq!(exit_code_for(error.code), 2);
    }
}
