//! Bounded synchronous Chrome DevTools Protocol transport for the host.

use std::{
    collections::BTreeMap,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    sync::{Arc, Mutex, RwLock},
    thread,
    time::{Duration, Instant},
};

use agentyc_core::{
    ActionOperation, ActionReceipt, ActionRequest, BrokerEpoch, CacheState, Capability, ElementKey,
    ElementKind, FrameId, FrameVersion, Generation, LeaseEpoch, PageId, RefEpoch, SnapshotBody,
    SnapshotCoverage, SnapshotDocument, SnapshotElement, SnapshotEnvelope, SnapshotMode,
    SnapshotVersion, SpaceId, TopologyVersion, UnknownReason,
    errors::{CoreError, ErrorCode},
    states::DirtyReason,
};
use serde_json::{Value, json};
use tungstenite::{Message, WebSocket, client::IntoClientRequest};

use crate::{
    actionability::ActionabilityInput,
    bridge::{
        Bridge, BridgeDispatchResult, BridgeReconcileResult, BridgeStatus, ExtensionEpochs,
        FenceResult, ObservationSnapshot,
    },
};

pub const DEFAULT_CDP_PORT: u16 = 9222;
const CDP_TARGET_DISCOVERY_INTERVAL: Duration = Duration::from_millis(25);
const CDP_TARGET_DISCOVERY_TIMEOUT: Duration = Duration::from_secs(5);
const CDP_MAX_SNAPSHOT_BYTES: usize = 256 * 1024;
const CDP_MAX_SNAPSHOT_ELEMENTS: usize = 256;
const CDP_MAX_SNAPSHOT_TEXT_BYTES: usize = 16 * 1024;
const CDP_MAX_ATTRIBUTE_VALUE_BYTES: usize = 1024;
const MAX_CDP_HTTP_HEADER_BYTES: usize = 16 * 1024;
const MAX_CDP_HTTP_BODY_BYTES: usize = 64 * 1024;
const MAX_CDP_MESSAGE_BYTES: usize = 32 * 1024 * 1024;

/// The extension's only browser-control operation: create a tab at a host
/// supplied bootstrap URL without returning a browser identifier.
pub(crate) trait TabCreationTransport: Send + Sync {
    fn create_tab(&self, bootstrap_url: &str) -> Result<(), CoreError>;

    fn present_group(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        title: Option<&str>,
    ) -> Result<Value, CoreError> {
        let _ = (space_id, page_id, lease_epoch, title);
        Err(CoreError::new(
            ErrorCode::CapabilityUnavailable,
            "extension tab grouping is unavailable",
        ))
    }

    fn extension_epochs(&self) -> Option<ExtensionEpochs> {
        None
    }

    fn bridge_status(&self) -> Option<BridgeStatus> {
        None
    }
}

#[derive(Default)]
pub struct TabCreationTransportRouter {
    current: RwLock<Option<Arc<dyn TabCreationTransport>>>,
}

impl TabCreationTransportRouter {
    fn install(&self, transport: Arc<dyn TabCreationTransport>) -> Result<(), CoreError> {
        *self.current.write().map_err(|_| {
            CoreError::new(
                ErrorCode::NativeHostUnavailable,
                "tab creation transport router is poisoned",
            )
        })? = Some(transport);
        Ok(())
    }

    pub fn install_native_messaging_bridge(
        &self,
        bridge: crate::native_messaging::NativeMessagingBridge,
    ) -> Result<(), CoreError> {
        self.install(Arc::new(bridge))
    }

    fn current(&self) -> Result<Arc<dyn TabCreationTransport>, CoreError> {
        self.current
            .read()
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "tab creation transport router is poisoned",
                )
            })?
            .clone()
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::ExtensionNotConnected,
                    "extension tab creation transport is unavailable",
                )
            })
    }
}

impl TabCreationTransport for TabCreationTransportRouter {
    fn create_tab(&self, bootstrap_url: &str) -> Result<(), CoreError> {
        validate_bootstrap_url(bootstrap_url)?;
        self.current()?.create_tab(bootstrap_url)
    }

    fn extension_epochs(&self) -> Option<ExtensionEpochs> {
        self.current.read().ok()?.as_ref()?.extension_epochs()
    }

    fn present_group(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        title: Option<&str>,
    ) -> Result<Value, CoreError> {
        self.current()?
            .present_group(space_id, page_id, lease_epoch, title)
    }

    fn bridge_status(&self) -> Option<BridgeStatus> {
        self.current.read().ok()?.as_ref()?.bridge_status()
    }
}

fn bootstrap_url(page_id: &PageId, lease_epoch: LeaseEpoch) -> String {
    format!(
        "about:blank#agentyc-tab={}-{}",
        page_id.as_str(),
        lease_epoch.get()
    )
}

pub(crate) fn validate_bootstrap_url(bootstrap_url: &str) -> Result<(), CoreError> {
    if bootstrap_url.starts_with("about:blank#agentyc-tab=")
        && bootstrap_url.len() <= 1024
        && !bootstrap_url.chars().any(char::is_whitespace)
    {
        Ok(())
    } else {
        Err(CoreError::new(
            ErrorCode::InvalidArgument,
            "tab bootstrap URL is invalid",
        ))
    }
}

trait CdpWire: Send {
    fn send(&mut self, value: &Value) -> Result<(), CdpWireError>;
    fn receive(&mut self) -> Result<Value, CdpWireError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CdpWireError {
    Unavailable,
    Timeout,
    Protocol,
}

struct WebSocketWire {
    socket: WebSocket<TcpStream>,
}

impl CdpWire for WebSocketWire {
    fn send(&mut self, value: &Value) -> Result<(), CdpWireError> {
        let text = serde_json::to_string(value).map_err(|_| CdpWireError::Protocol)?;
        if text.len() > MAX_CDP_MESSAGE_BYTES {
            return Err(CdpWireError::Protocol);
        }
        self.socket
            .send(Message::Text(text.into()))
            .map_err(map_websocket_error)
    }

    fn receive(&mut self) -> Result<Value, CdpWireError> {
        loop {
            match self.socket.read().map_err(map_websocket_error)? {
                Message::Text(text) => {
                    if text.len() > MAX_CDP_MESSAGE_BYTES {
                        return Err(CdpWireError::Protocol);
                    }
                    return serde_json::from_str(text.as_str()).map_err(|_| CdpWireError::Protocol);
                }
                Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => {}
                Message::Close(_) | Message::Binary(_) => return Err(CdpWireError::Protocol),
            }
        }
    }
}

fn map_websocket_error(error: tungstenite::Error) -> CdpWireError {
    match error {
        tungstenite::Error::Io(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            ) =>
        {
            CdpWireError::Timeout
        }
        tungstenite::Error::ConnectionClosed | tungstenite::Error::AlreadyClosed => {
            CdpWireError::Unavailable
        }
        _ => CdpWireError::Protocol,
    }
}

struct CdpClient {
    wire: Box<dyn CdpWire>,
    next_id: u64,
    usable: bool,
}

/// Composition of host CDP control with the extension's tab-create-only seam.
pub struct CdpBridge {
    state: Mutex<CdpBridgeState>,
    tab_creation: Arc<dyn TabCreationTransport>,
}

struct CdpBridgeState {
    client: CdpClient,
    pages: BTreeMap<(SpaceId, PageId), ManagedPage>,
}

#[derive(Clone)]
struct ManagedPage {
    target_id: String,
    session_id: String,
    lease_epoch: LeaseEpoch,
    target_generation: u64,
    navigation_generation: u64,
    document_generation: u64,
    url: Option<String>,
    title: Option<String>,
    snapshot_version: u64,
    backend_nodes: BTreeMap<ElementKey, i64>,
}

impl CdpBridge {
    pub fn connect_local(
        tab_creation: Arc<TabCreationTransportRouter>,
        port: u16,
        timeout: Duration,
    ) -> Result<Self, CoreError> {
        Ok(Self {
            state: Mutex::new(CdpBridgeState {
                client: CdpClient::connect_local(port, timeout)?,
                pages: BTreeMap::new(),
            }),
            tab_creation,
        })
    }

    #[cfg(test)]
    fn with_client(client: CdpClient, tab_creation: Arc<dyn TabCreationTransport>) -> Self {
        Self {
            state: Mutex::new(CdpBridgeState {
                client,
                pages: BTreeMap::new(),
            }),
            tab_creation,
        }
    }

    #[cfg(test)]
    fn create_tab_and_find_target(
        &self,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        timeout: Duration,
    ) -> Result<String, CoreError> {
        let bootstrap_url = bootstrap_url(page_id, lease_epoch);
        self.state
            .lock()
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::NativeHostUnavailable,
                    "host CDP bridge state is poisoned",
                )
            })?
            .client
            .create_tab_and_find_target(self.tab_creation.as_ref(), &bootstrap_url, timeout)
    }

    fn lock_state(&self) -> Result<std::sync::MutexGuard<'_, CdpBridgeState>, CoreError> {
        self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::NativeHostUnavailable,
                "host CDP bridge state is poisoned",
            )
        })
    }

    fn invalidate_pages_if_client_unusable(&self) -> Result<(), CoreError> {
        let mut state = self.lock_state()?;
        if !state.client.is_usable() {
            state.pages.clear();
        }
        Ok(())
    }

    fn navigate_page(
        &self,
        request: &ActionRequest<std::collections::BTreeMap<String, String>>,
    ) -> Result<(), CoreError> {
        let page_id = request
            .page_id
            .as_ref()
            .ok_or_else(|| CoreError::new(ErrorCode::PageNotFound, "page is required"))?;
        let url = request
            .payload
            .get("url")
            .ok_or_else(|| CoreError::invalid_argument("navigation URL is required"))?;
        validate_page_url(url)?;

        let mut state = self.lock_state()?;
        let key = (request.space_id.clone(), page_id.clone());
        let page = state.pages.get(&key).cloned().ok_or_else(|| {
            CoreError::new(
                ErrorCode::PageNotFound,
                "managed browser page was not found",
            )
        })?;
        if page.lease_epoch != request.lease_epoch {
            return Err(CoreError::stale_lease(
                page.lease_epoch.get(),
                request.lease_epoch.get(),
            ));
        }
        let navigation_generation = page.navigation_generation.checked_add(1).ok_or_else(|| {
            CoreError::new(
                ErrorCode::TargetReplaced,
                "page navigation generation exhausted",
            )
        })?;
        let document_generation = page.document_generation.checked_add(1).ok_or_else(|| {
            CoreError::new(
                ErrorCode::TargetReplaced,
                "page document generation exhausted",
            )
        })?;
        state.client.navigate(&page.session_id, url)?;
        let updated = state.pages.get_mut(&key).ok_or_else(|| {
            CoreError::new(
                ErrorCode::TargetReplaced,
                "managed browser page changed during navigation",
            )
        })?;
        updated.url = Some(url.clone());
        updated.navigation_generation = navigation_generation;
        updated.document_generation = document_generation;
        Ok(())
    }

    fn dispatch_element_action(
        &self,
        request: &ActionRequest<std::collections::BTreeMap<String, String>>,
    ) -> Result<(), CoreError> {
        let page_id = request
            .page_id
            .as_ref()
            .ok_or_else(|| CoreError::new(ErrorCode::PageNotFound, "page is required"))?;
        let input = ActionabilityInput::from_payload(&request.payload)?.ok_or_else(|| {
            CoreError::invalid_argument("element action requires a complete issued ref")
        })?;
        let element_ref = input.element_ref;
        let mut state = self.lock_state()?;
        let key = (request.space_id.clone(), page_id.clone());
        let page = state.pages.get(&key).cloned().ok_or_else(|| {
            CoreError::new(
                ErrorCode::PageNotFound,
                "managed browser page was not found",
            )
        })?;
        if page.lease_epoch != request.lease_epoch {
            return Err(CoreError::stale_lease(
                page.lease_epoch.get(),
                request.lease_epoch.get(),
            ));
        }
        if element_ref.space_id != request.space_id
            || element_ref.page_id != *page_id
            || element_ref.frame_id != input.frame_id
            || element_ref.frame_id
                != FrameId::from_suffix("main")
                    .map_err(|_| CoreError::invalid_argument("main frame identity is invalid"))?
            || element_ref.snapshot_version.get() != page.snapshot_version
            || element_ref.navigation_generation.get() != page.navigation_generation
            || element_ref.document_generation.get() != page.document_generation
            || element_ref.refs_epoch.get() != page.snapshot_version
        {
            return Err(CoreError::stale_ref(
                "element ref no longer matches the managed page snapshot",
            ));
        }
        let backend_node_id = page
            .backend_nodes
            .get(&element_ref.element_key)
            .copied()
            .ok_or_else(|| CoreError::stale_ref("element key is not bound to a live DOM node"))?;

        state.client.command(
            "DOM.getDocument",
            json!({"depth": 1}),
            Some(&page.session_id),
        )?;
        let described = state.client.command(
            "DOM.describeNode",
            json!({"backendNodeId": backend_node_id, "depth": 0}),
            Some(&page.session_id),
        )?;
        let node = described.get("node").ok_or_else(|| {
            CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools node is missing",
            )
        })?;
        validate_live_node(node, backend_node_id)?;

        match request.operation {
            ActionOperation::Click => {
                reject_disabled_node(node)?;
                let target_node_id =
                    node.get("nodeId").and_then(Value::as_i64).ok_or_else(|| {
                        CoreError::new(
                            ErrorCode::ProtocolMismatch,
                            "Chrome DevTools node identity is missing",
                        )
                    })?;
                let model = state.client.command(
                    "DOM.getBoxModel",
                    json!({"backendNodeId": backend_node_id}),
                    Some(&page.session_id),
                )?;
                let (x, y) = box_center(&model)?;
                let hit = state.client.command(
                    "DOM.getNodeForLocation",
                    json!({"x": x, "y": y}),
                    Some(&page.session_id),
                )?;
                let hit_backend_node_id = hit
                    .get("backendNodeId")
                    .and_then(Value::as_i64)
                    .ok_or_else(|| {
                        CoreError::new(
                            ErrorCode::ProtocolMismatch,
                            "Chrome DevTools hit-test identity is missing",
                        )
                    })?;
                if !state.client.is_node_within(
                    &page.session_id,
                    hit_backend_node_id,
                    backend_node_id,
                    target_node_id,
                )? {
                    return Err(CoreError::new(
                        ErrorCode::PermissionDenied,
                        "the live hit target is outside the referenced element",
                    ));
                }
                state.client.command(
                    "Input.dispatchMouseEvent",
                    json!({
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "buttons": 1,
                        "clickCount": 1
                    }),
                    Some(&page.session_id),
                )?;
                state.client.command(
                    "Input.dispatchMouseEvent",
                    json!({
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "buttons": 0,
                        "clickCount": 1
                    }),
                    Some(&page.session_id),
                )?;
                Ok(())
            }
            ActionOperation::Input => {
                reject_disabled_node(node)?;
                validate_editable_node(node)?;
                let text = request
                    .payload
                    .get("text")
                    .ok_or_else(|| CoreError::invalid_argument("input action requires text"))?;
                state.client.command(
                    "DOM.focus",
                    json!({"backendNodeId": backend_node_id}),
                    Some(&page.session_id),
                )?;
                state.client.command(
                    "Input.insertText",
                    json!({"text": text}),
                    Some(&page.session_id),
                )?;
                Ok(())
            }
            _ => Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "element action is unavailable for this operation",
            )),
        }
    }
}

impl Bridge for CdpBridge {
    fn capabilities(&self) -> Vec<Capability> {
        vec![Capability::Snapshot, Capability::Action]
    }

    fn extension_epochs(&self) -> Option<ExtensionEpochs> {
        self.tab_creation.extension_epochs()
    }

    fn bridge_status(&self) -> Option<BridgeStatus> {
        self.tab_creation.bridge_status()
    }

    fn present_group(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        title: Option<&str>,
    ) -> Result<Value, CoreError> {
        self.tab_creation
            .present_group(space_id, page_id, lease_epoch, title)
    }

    fn dispatch(
        &self,
        request: &ActionRequest<std::collections::BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError> {
        let result = match request.operation {
            ActionOperation::Navigate => self.navigate_page(request),
            ActionOperation::Click | ActionOperation::Input => {
                self.dispatch_element_action(request)
            }
            ActionOperation::Close => request
                .page_id
                .as_ref()
                .ok_or_else(|| CoreError::new(ErrorCode::PageNotFound, "page is required"))
                .and_then(|page_id| {
                    self.close_page(&request.space_id, page_id, request.lease_epoch)
                }),
            _ => Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "the host CDP bridge does not support this action yet",
            )),
        };
        let result = match self.invalidate_pages_if_client_unusable() {
            Ok(()) => result,
            Err(error) => Err(error),
        };
        Ok(match result {
            Ok(()) => BridgeDispatchResult::Succeeded,
            Err(error)
                if matches!(
                    error.code,
                    ErrorCode::Timeout
                        | ErrorCode::NativeHostUnavailable
                        | ErrorCode::UnknownOutcome
                ) =>
            {
                BridgeDispatchResult::Unknown {
                    reason: UnknownReason::BridgeLost,
                }
            }
            Err(error) => BridgeDispatchResult::Failed {
                code: error.code,
                retryable: error.code.retryable(),
            },
        })
    }

    fn reconcile(&self, _receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError> {
        Ok(BridgeReconcileResult::StillUnknown)
    }

    fn observe(&self) -> Result<ObservationSnapshot, CoreError> {
        let state = self.lock_state()?;
        let pages = state
            .pages
            .iter()
            .map(|((space_id, page_id), page)| {
                json!({
                    "space_id": space_id.as_str(),
                    "page_id": page_id.as_str(),
                    "ownership": "agent",
                    "lifecycle": "managed",
                    "binding_state": "bound",
                    "lease_epoch": page.lease_epoch.get(),
                    "target_generation": page.target_generation,
                    "navigation_generation": page.navigation_generation,
                    "document_generation": page.document_generation,
                    "url": page.url,
                    "title": page.title,
                })
            })
            .collect();
        Ok(ObservationSnapshot {
            pages,
            ..ObservationSnapshot::default()
        })
    }

    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<SnapshotEnvelope, CoreError> {
        let mut state = self.lock_state()?;
        let key = (space_id.clone(), page_id.clone());
        let page = state.pages.get(&key).cloned().ok_or_else(|| {
            CoreError::new(
                ErrorCode::PageNotFound,
                "managed browser page was not found",
            )
        })?;
        if page.lease_epoch != lease_epoch {
            return Err(CoreError::stale_lease(
                page.lease_epoch.get(),
                lease_epoch.get(),
            ));
        }
        if page.url.as_deref().is_some_and(is_restricted_page_url) {
            return Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "snapshots are unavailable for browser-restricted URLs",
            ));
        }
        let result = state.client.command(
            "DOMSnapshot.captureSnapshot",
            json!({"computedStyles": []}),
            Some(&page.session_id),
        );
        if !state.client.is_usable() {
            state.pages.clear();
        }
        let result = result?;
        let (elements, backend_nodes, truncated) = parse_dom_snapshot(&result)?;
        let snapshot_version = page.snapshot_version.checked_add(1).ok_or_else(|| {
            CoreError::new(ErrorCode::TargetReplaced, "page snapshot version exhausted")
        })?;
        let (envelope, backend_nodes) = build_snapshot_envelope(
            space_id.clone(),
            page_id.clone(),
            page.navigation_generation,
            page.document_generation,
            snapshot_version,
            elements,
            backend_nodes,
            truncated,
        )?;
        let current = state.pages.get_mut(&key).ok_or_else(|| {
            CoreError::new(
                ErrorCode::TargetReplaced,
                "managed browser page changed during snapshot capture",
            )
        })?;
        current.snapshot_version = snapshot_version;
        current.backend_nodes = backend_nodes;
        Ok(envelope)
    }

    fn fence(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        _broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        let mut state = self.lock_state()?;
        if state.pages.iter().any(|((bound_space, _), page)| {
            bound_space == space_id && old_epoch.is_some_and(|epoch| epoch != page.lease_epoch)
        }) {
            return Ok(FenceResult {
                acknowledged: false,
            });
        }
        for ((bound_space, _), page) in &mut state.pages {
            if bound_space == space_id {
                page.lease_epoch = new_epoch;
            }
        }
        Ok(FenceResult { acknowledged: true })
    }

    fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError> {
        let mut state = self.lock_state()?;
        let key = (space_id.clone(), page_id.clone());
        let page = state.pages.get(&key).cloned().ok_or_else(|| {
            CoreError::new(
                ErrorCode::PageNotFound,
                "managed browser page was not found",
            )
        })?;
        if page.lease_epoch != lease_epoch {
            return Err(CoreError::stale_lease(
                page.lease_epoch.get(),
                lease_epoch.get(),
            ));
        }
        state.client.close_target(&page.target_id)?;
        state.pages.remove(&key);
        Ok(())
    }

    fn create_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        url: Option<&str>,
        title: Option<&str>,
        ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        validate_creation_proof(space_id, page_id, lease_epoch, &ownership_proof)?;
        if let Some(url) = url {
            validate_page_url(url)?;
        }

        let mut state = self.lock_state()?;
        let key = (space_id.clone(), page_id.clone());
        if state.pages.contains_key(&key) {
            return Err(CoreError::new(
                ErrorCode::TargetReplaced,
                "managed browser page is already bound",
            ));
        }

        let bootstrap_url = bootstrap_url(page_id, lease_epoch);
        let target_id = state.client.create_tab_and_find_target(
            self.tab_creation.as_ref(),
            &bootstrap_url,
            CDP_TARGET_DISCOVERY_TIMEOUT,
        )?;
        let session_id = match (|| {
            let session_id = state.client.attach_to_target(&target_id)?;
            state
                .client
                .command("Page.enable", json!({}), Some(&session_id))?;
            state
                .client
                .command("Runtime.enable", json!({}), Some(&session_id))?;
            state
                .client
                .navigate(&session_id, url.unwrap_or("about:blank"))?;
            Ok::<_, CoreError>(session_id)
        })() {
            Ok(session_id) => session_id,
            Err(error) => {
                return Err(cleanup_failed_creation(
                    &mut state.client,
                    &target_id,
                    error,
                ));
            }
        };

        state.pages.insert(
            key,
            ManagedPage {
                target_id,
                session_id,
                lease_epoch,
                target_generation: 1,
                navigation_generation: 1,
                document_generation: 1,
                url: url.map(str::to_owned),
                title: title.map(str::to_owned),
                snapshot_version: 0,
                backend_nodes: BTreeMap::new(),
            },
        );
        Ok(json!({
            "space_id": space_id.as_str(),
            "page_id": page_id.as_str(),
            "target_generation": 1,
            "navigation_generation": 1,
            "document_generation": 1,
            "frame_count": 1,
            "url": url,
            "title": title,
        }))
    }
}

impl CdpClient {
    fn connect_local(port: u16, timeout: Duration) -> Result<Self, CoreError> {
        let browser_ws_url = browser_websocket_url(port, timeout)?;
        let (address, request) = websocket_request(&browser_ws_url, port)?;
        let stream = TcpStream::connect_timeout(&address, timeout)
            .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
        stream
            .set_read_timeout(Some(timeout))
            .and_then(|()| stream.set_write_timeout(Some(timeout)))
            .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
        let (mut socket, _) = tungstenite::client::client(request, stream)
            .map_err(|_| cdp_error(CdpWireError::Protocol))?;
        socket
            .get_mut()
            .set_read_timeout(Some(timeout))
            .and_then(|()| socket.get_mut().set_write_timeout(Some(timeout)))
            .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
        Ok(Self {
            wire: Box::new(WebSocketWire { socket }),
            next_id: 1,
            usable: true,
        })
    }

    #[cfg(test)]
    fn with_wire(wire: impl CdpWire + 'static) -> Self {
        Self {
            wire: Box::new(wire),
            next_id: 1,
            usable: true,
        }
    }

    fn is_usable(&self) -> bool {
        self.usable
    }

    fn create_tab_and_find_target(
        &mut self,
        tab_creation: &dyn TabCreationTransport,
        bootstrap_url: &str,
        timeout: Duration,
    ) -> Result<String, CoreError> {
        validate_bootstrap_url(bootstrap_url)?;
        let deadline = Instant::now().checked_add(timeout).ok_or_else(|| {
            CoreError::new(
                ErrorCode::InvalidArgument,
                "tab target discovery timeout is invalid",
            )
        })?;

        tab_creation.create_tab(bootstrap_url)?;

        loop {
            let result = self
                .command("Target.getTargets", json!({}), None)
                .map_err(|_| unknown_tab_creation())?;
            let targets = result
                .get("targetInfos")
                .and_then(Value::as_array)
                .ok_or_else(unknown_tab_creation)?;
            let mut matching_target = None;
            for target in targets.iter().filter(|target| {
                target.get("type").and_then(Value::as_str) == Some("page")
                    && target.get("url").and_then(Value::as_str) == Some(bootstrap_url)
            }) {
                let target_id = target
                    .get("targetId")
                    .and_then(Value::as_str)
                    .filter(|target_id| !target_id.is_empty())
                    .ok_or_else(unknown_tab_creation)?;
                if matching_target.replace(target_id.to_owned()).is_some() {
                    return Err(unknown_tab_creation());
                }
            }
            if let Some(target_id) = matching_target {
                return Ok(target_id);
            }

            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(unknown_tab_creation());
            }
            thread::sleep(CDP_TARGET_DISCOVERY_INTERVAL.min(remaining));
        }
    }

    fn attach_to_target(&mut self, target_id: &str) -> Result<String, CoreError> {
        if target_id.is_empty() || target_id.len() > 1024 {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools target handle is invalid",
            ));
        }
        let result = self.command(
            "Target.attachToTarget",
            json!({"targetId": target_id, "flatten": true}),
            None,
        )?;
        result
            .get("sessionId")
            .and_then(Value::as_str)
            .filter(|session_id| !session_id.is_empty() && session_id.len() <= 1024)
            .map(str::to_owned)
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::ProtocolMismatch,
                    "Chrome DevTools attach response is invalid",
                )
            })
    }

    fn navigate(&mut self, session_id: &str, url: &str) -> Result<(), CoreError> {
        if session_id.is_empty() || session_id.len() > 1024 || url.is_empty() || url.len() > 4096 {
            return Err(CoreError::invalid_argument(
                "Chrome DevTools navigation input is invalid",
            ));
        }
        let result = self.command("Page.navigate", json!({"url": url}), Some(session_id))?;
        if result.get("errorText").and_then(Value::as_str).is_some() {
            return Err(CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "Chrome DevTools could not navigate the managed page",
            ));
        }
        Ok(())
    }

    fn close_target(&mut self, target_id: &str) -> Result<(), CoreError> {
        let result = self.command("Target.closeTarget", json!({"targetId": target_id}), None)?;
        match result.get("success").and_then(Value::as_bool) {
            Some(true) => Ok(()),
            Some(false) => Err(CoreError::new(
                ErrorCode::PageNotFound,
                "managed browser page could not be closed",
            )),
            None => Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools close response is invalid",
            )),
        }
    }

    fn command(
        &mut self,
        method: &str,
        params: Value,
        session_id: Option<&str>,
    ) -> Result<Value, CoreError> {
        if !self.usable {
            return Err(cdp_error(CdpWireError::Unavailable));
        }
        let id = self.next_id;
        self.next_id = id.checked_add(1).ok_or_else(|| {
            CoreError::new(ErrorCode::ProtocolMismatch, "CDP command IDs exhausted")
        })?;
        let mut command = json!({
            "id": id,
            "method": method,
            "params": params,
        });
        if let Some(session_id) = session_id {
            command["sessionId"] = Value::String(session_id.to_owned());
        }
        if self.wire.send(&command).is_err() {
            self.usable = false;
            return Err(unknown_cdp_command_outcome());
        }
        loop {
            let response = match self.wire.receive() {
                Ok(response) => response,
                Err(_) => {
                    self.usable = false;
                    return Err(unknown_cdp_command_outcome());
                }
            };
            match response.get("id").and_then(Value::as_u64) {
                Some(response_id) if response_id == id => {
                    if response.get("error").is_some() {
                        return Err(CoreError::new(
                            ErrorCode::CapabilityUnavailable,
                            "Chrome DevTools rejected the command",
                        ));
                    }
                    return Ok(response.get("result").cloned().unwrap_or(Value::Null));
                }
                None if response.get("method").and_then(Value::as_str).is_some() => {}
                _ => {
                    self.usable = false;
                    return Err(unknown_cdp_command_outcome());
                }
            }
        }
    }

    fn is_node_within(
        &mut self,
        session_id: &str,
        hit_backend_node_id: i64,
        target_backend_node_id: i64,
        target_node_id: i64,
    ) -> Result<bool, CoreError> {
        if hit_backend_node_id == target_backend_node_id {
            return Ok(true);
        }

        let described = self.command(
            "DOM.describeNode",
            json!({"backendNodeId": hit_backend_node_id, "depth": 0}),
            Some(session_id),
        )?;
        let mut node = described.get("node").cloned().ok_or_else(|| {
            CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools hit node is missing",
            )
        })?;

        for _ in 0..CDP_MAX_SNAPSHOT_ELEMENTS {
            if node.get("nodeId").and_then(Value::as_i64) == Some(target_node_id)
                || node.get("backendNodeId").and_then(Value::as_i64) == Some(target_backend_node_id)
            {
                return Ok(true);
            }
            let Some(parent_id) = node.get("parentId").and_then(Value::as_i64) else {
                return Ok(false);
            };
            if parent_id == target_node_id {
                return Ok(true);
            }
            let parent = self.command(
                "DOM.describeNode",
                json!({"nodeId": parent_id, "depth": 0}),
                Some(session_id),
            )?;
            node = parent.get("node").cloned().ok_or_else(|| {
                CoreError::new(
                    ErrorCode::ProtocolMismatch,
                    "Chrome DevTools parent node is missing",
                )
            })?;
        }
        Err(CoreError::stale_ref(
            "live hit-target ancestry exceeds the host verification limit",
        ))
    }
}

fn cdp_error(error: CdpWireError) -> CoreError {
    match error {
        CdpWireError::Unavailable => CoreError::new(
            ErrorCode::NativeHostUnavailable,
            "local Chrome DevTools endpoint is unavailable",
        ),
        CdpWireError::Timeout => {
            CoreError::new(ErrorCode::Timeout, "Chrome DevTools request timed out")
        }
        CdpWireError::Protocol => CoreError::new(
            ErrorCode::ProtocolMismatch,
            "Chrome DevTools protocol is invalid",
        ),
    }
}

fn unknown_cdp_command_outcome() -> CoreError {
    CoreError::new(
        ErrorCode::UnknownOutcome,
        "Chrome DevTools command outcome is unknown",
    )
}

fn browser_websocket_url(port: u16, timeout: Duration) -> Result<String, CoreError> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let stream = TcpStream::connect_timeout(&address, timeout)
        .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|()| stream.set_write_timeout(Some(timeout)))
        .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
    let mut stream = BufReader::new(stream);
    write!(
        stream.get_mut(),
        "GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .map_err(|_| cdp_error(CdpWireError::Unavailable))?;
    stream
        .get_mut()
        .flush()
        .map_err(|_| cdp_error(CdpWireError::Unavailable))?;

    let mut header = Vec::new();
    loop {
        let mut line = Vec::new();
        let count = stream
            .read_until(b'\n', &mut line)
            .map_err(|error| cdp_error(map_io_error(&error)))?;
        if count == 0 || header.len().saturating_add(line.len()) > MAX_CDP_HTTP_HEADER_BYTES {
            return Err(cdp_error(CdpWireError::Protocol));
        }
        let end = line == b"\r\n";
        header.extend_from_slice(&line);
        if end {
            break;
        }
    }
    let header_text =
        std::str::from_utf8(&header).map_err(|_| cdp_error(CdpWireError::Protocol))?;
    if !header_text.starts_with("HTTP/1.1 200 ") && !header_text.starts_with("HTTP/1.0 200 ") {
        return Err(cdp_error(CdpWireError::Unavailable));
    }
    let content_length = header_text
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .filter(|length| *length <= MAX_CDP_HTTP_BODY_BYTES)
        .ok_or_else(|| cdp_error(CdpWireError::Protocol))?;
    let mut body = vec![0; content_length];
    stream
        .read_exact(&mut body)
        .map_err(|error| cdp_error(map_io_error(&error)))?;
    let response: Value =
        serde_json::from_slice(&body).map_err(|_| cdp_error(CdpWireError::Protocol))?;
    response
        .get("webSocketDebuggerUrl")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| cdp_error(CdpWireError::Protocol))
}

fn websocket_request(
    url: &str,
    port: u16,
) -> Result<(SocketAddr, tungstenite::handshake::client::Request), CoreError> {
    let rest = url
        .strip_prefix("ws://127.0.0.1:")
        .ok_or_else(|| cdp_error(CdpWireError::Protocol))?;
    let (authority_port, path) = rest
        .split_once('/')
        .ok_or_else(|| cdp_error(CdpWireError::Protocol))?;
    let parsed_port = authority_port
        .parse::<u16>()
        .map_err(|_| cdp_error(CdpWireError::Protocol))?;
    if parsed_port != port
        || !path.starts_with("devtools/browser/")
        || path["devtools/browser/".len()..].is_empty()
        || path.contains(['?', '#', '\\'])
    {
        return Err(cdp_error(CdpWireError::Protocol));
    }
    let address = SocketAddr::from(([127, 0, 0, 1], parsed_port));
    let request = url
        .into_client_request()
        .map_err(|_| cdp_error(CdpWireError::Protocol))?;
    Ok((address, request))
}

fn map_io_error(error: &std::io::Error) -> CdpWireError {
    match error.kind() {
        std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock => CdpWireError::Timeout,
        _ => CdpWireError::Unavailable,
    }
}

fn validate_creation_proof(
    space_id: &SpaceId,
    page_id: &PageId,
    lease_epoch: LeaseEpoch,
    proof: &Value,
) -> Result<(), CoreError> {
    let valid = proof.get("issued_by_host").and_then(Value::as_bool) == Some(true)
        && proof.get("kind").and_then(Value::as_str) == Some("creation")
        && proof.get("space_id").and_then(Value::as_str) == Some(space_id.as_str())
        && proof.get("page_id").and_then(Value::as_str) == Some(page_id.as_str())
        && proof.get("lease_epoch").and_then(Value::as_u64) == Some(lease_epoch.get());
    if valid {
        Ok(())
    } else {
        Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "managed page creation proof is invalid",
        ))
    }
}

fn validate_page_url(url: &str) -> Result<(), CoreError> {
    if url.is_empty() || url.len() > 4096 || url.chars().any(char::is_whitespace) {
        return Err(CoreError::invalid_argument("page URL is invalid"));
    }
    if is_restricted_page_url(url) {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "browser-restricted pages cannot be managed",
        ));
    }
    Ok(())
}

fn is_restricted_page_url(url: &str) -> bool {
    let lowercase = url.to_ascii_lowercase();
    [
        "chrome:",
        "edge:",
        "about:",
        "devtools:",
        "view-source:",
        "chrome-extension:",
        "file:",
    ]
    .iter()
    .any(|scheme| lowercase.starts_with(scheme))
}

fn cleanup_failed_creation(
    client: &mut CdpClient,
    target_id: &str,
    creation_error: CoreError,
) -> CoreError {
    match client.close_target(target_id) {
        Ok(()) => creation_error,
        Err(error) if error.code == ErrorCode::PageNotFound => creation_error,
        Err(_) => CoreError::new(
            ErrorCode::UnknownOutcome,
            "managed page setup failed and tab cleanup could not be confirmed",
        ),
    }
}

fn unknown_tab_creation() -> CoreError {
    CoreError::new(
        ErrorCode::UnknownOutcome,
        "extension created a tab but the host could not identify its browser target",
    )
}

fn validate_live_node(node: &Value, expected_backend_node_id: i64) -> Result<(), CoreError> {
    if node.get("backendNodeId").and_then(Value::as_i64) != Some(expected_backend_node_id)
        || node.get("nodeId").and_then(Value::as_i64).is_none()
        || node.get("nodeType").and_then(Value::as_u64) != Some(1)
    {
        return Err(CoreError::stale_ref(
            "referenced backend node is no longer the same live element",
        ));
    }
    Ok(())
}

fn node_attribute<'a>(node: &'a Value, name: &str) -> Option<&'a str> {
    let attributes = node.get("attributes")?.as_array()?;
    attributes.chunks_exact(2).find_map(|pair| {
        (pair.first()?.as_str()? == name)
            .then(|| pair.get(1)?.as_str())
            .flatten()
    })
}

fn reject_disabled_node(node: &Value) -> Result<(), CoreError> {
    if node_attribute(node, "disabled").is_some()
        || node_attribute(node, "inert").is_some()
        || node_attribute(node, "aria-disabled").is_some_and(|value| value == "true")
    {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "the referenced element is currently disabled",
        ));
    }
    Ok(())
}

fn validate_editable_node(node: &Value) -> Result<(), CoreError> {
    if node_attribute(node, "readonly").is_some()
        || node_attribute(node, "aria-readonly").is_some_and(|value| value == "true")
    {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "the referenced element is currently read-only",
        ));
    }
    let tag = node
        .get("localName")
        .and_then(Value::as_str)
        .or_else(|| node.get("nodeName").and_then(Value::as_str))
        .unwrap_or_default()
        .to_ascii_lowercase();
    let editable = match tag.as_str() {
        "textarea" => true,
        "input" => matches!(
            node_attribute(node, "type")
                .unwrap_or("text")
                .to_ascii_lowercase()
                .as_str(),
            "text" | "search" | "email" | "url" | "tel" | "number"
        ),
        _ => matches!(
            node_attribute(node, "contenteditable")
                .map(str::to_ascii_lowercase)
                .as_deref(),
            Some("" | "true" | "plaintext-only")
        ),
    };
    if !editable {
        return Err(CoreError::new(
            ErrorCode::PermissionDenied,
            "the referenced element is not an editable control",
        ));
    }
    Ok(())
}

fn box_center(model: &Value) -> Result<(i32, i32), CoreError> {
    let quad = model
        .get("model")
        .and_then(|model| model.get("content"))
        .and_then(Value::as_array)
        .filter(|quad| quad.len() == 8)
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::TargetReplaced,
                "referenced element has no current content box",
            )
        })?;
    let coordinates = quad
        .iter()
        .map(Value::as_f64)
        .collect::<Option<Vec<_>>>()
        .filter(|coordinates| coordinates.iter().all(|coordinate| coordinate.is_finite()))
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools content box is invalid",
            )
        })?;
    let xs = [
        coordinates[0],
        coordinates[2],
        coordinates[4],
        coordinates[6],
    ];
    let ys = [
        coordinates[1],
        coordinates[3],
        coordinates[5],
        coordinates[7],
    ];
    let (min_x, max_x) = xs
        .iter()
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(min, max), value| {
            (min.min(*value), max.max(*value))
        });
    let (min_y, max_y) = ys
        .iter()
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(min, max), value| {
            (min.min(*value), max.max(*value))
        });
    if min_x < 0.0 || min_y < 0.0 || max_x <= min_x || max_y <= min_y {
        return Err(CoreError::new(
            ErrorCode::TargetReplaced,
            "referenced element is outside the live viewport",
        ));
    }
    let center_x = (xs.iter().sum::<f64>() / 4.0).round();
    let center_y = (ys.iter().sum::<f64>() / 4.0).round();
    if center_x > f64::from(i32::MAX) || center_y > f64::from(i32::MAX) {
        return Err(CoreError::new(
            ErrorCode::TargetReplaced,
            "referenced element is outside the live viewport",
        ));
    }
    Ok((center_x as i32, center_y as i32))
}

fn parse_dom_snapshot(
    result: &Value,
) -> Result<(Vec<SnapshotElement>, BTreeMap<ElementKey, i64>, bool), CoreError> {
    let document = result
        .get("documents")
        .and_then(Value::as_array)
        .and_then(|documents| documents.first())
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::CapabilityUnavailable,
                "Chrome DevTools returned no page document snapshot",
            )
        })?;
    let nodes = document.get("nodes").ok_or_else(|| {
        CoreError::new(
            ErrorCode::ProtocolMismatch,
            "Chrome DevTools document node table is invalid",
        )
    })?;
    let strings = result
        .get("strings")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    let node_names = nodes
        .get("nodeName")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::ProtocolMismatch,
                "Chrome DevTools document node names are invalid",
            )
        })?;
    let node_types = nodes.get("nodeType").and_then(Value::as_array);
    let node_values = nodes.get("nodeValue").and_then(Value::as_array);
    let parent_indices = nodes.get("parentIndex").and_then(Value::as_array);
    let raw_attributes = nodes.get("attributes").and_then(Value::as_array);
    let backend_ids = nodes.get("backendNodeId").and_then(Value::as_array);
    let mut elements = Vec::with_capacity(node_names.len().min(CDP_MAX_SNAPSHOT_ELEMENTS));
    let mut backend_nodes = BTreeMap::new();
    let mut truncated = node_names.len() > CDP_MAX_SNAPSHOT_ELEMENTS;

    for index in 0..node_names.len().min(CDP_MAX_SNAPSHOT_ELEMENTS) {
        let node_type = node_types
            .and_then(|node_types| node_types.get(index))
            .and_then(Value::as_u64);
        let kind = match node_type {
            Some(9) => ElementKind::Root,
            Some(3) => ElementKind::Text,
            Some(1) => ElementKind::Element,
            _ => continue,
        };
        let key = ElementKey::from_suffix(index.to_string()).map_err(|_| {
            CoreError::new(
                ErrorCode::InvalidJson,
                "Chrome DevTools node key is invalid",
            )
        })?;
        let parent = parent_indices
            .and_then(|parents| parents.get(index))
            .and_then(Value::as_u64)
            .map(|parent_index| {
                ElementKey::from_suffix(parent_index.to_string()).map_err(|_| {
                    CoreError::new(
                        ErrorCode::InvalidJson,
                        "Chrome DevTools parent node key is invalid",
                    )
                })
            })
            .transpose()?;
        let (text, text_truncated) = if kind == ElementKind::Text {
            let value = node_values
                .and_then(|values| values.get(index))
                .map(|value| snapshot_string(value, strings))
                .unwrap_or_default();
            bounded_snapshot_text(&value, CDP_MAX_SNAPSHOT_TEXT_BYTES)
        } else {
            (String::new(), false)
        };
        truncated |= text_truncated;

        let mut attributes = BTreeMap::new();
        if let Some(values) = raw_attributes
            .and_then(|attributes| attributes.get(index))
            .and_then(Value::as_array)
        {
            for pair in values.chunks_exact(2) {
                let (name, name_truncated) =
                    bounded_snapshot_text(&snapshot_string(&pair[0], strings), 128);
                if name.is_empty()
                    || matches!(name.as_str(), "__proto__" | "constructor" | "prototype")
                {
                    truncated |= name_truncated;
                    continue;
                }
                let (value, value_truncated) = bounded_snapshot_text(
                    &snapshot_string(&pair[1], strings),
                    CDP_MAX_ATTRIBUTE_VALUE_BYTES,
                );
                truncated |= name_truncated || value_truncated;
                if attributes.len() < 64 {
                    attributes.insert(name, value);
                } else {
                    truncated = true;
                }
            }
            if values.len() % 2 != 0 {
                truncated = true;
            }
        }
        let order = u32::try_from(index).map_err(|_| {
            CoreError::new(
                ErrorCode::MessageTooLarge,
                "Chrome DevTools document has too many nodes",
            )
        })?;
        elements.push(SnapshotElement {
            key: key.clone(),
            parent,
            kind,
            text: (kind == ElementKind::Text).then_some(text),
            attributes,
            order,
        });
        if let Some(backend_node_id) = backend_ids
            .and_then(|ids| ids.get(index))
            .and_then(Value::as_i64)
        {
            backend_nodes.insert(key, backend_node_id);
        }
    }
    if elements.is_empty() {
        return Err(CoreError::new(
            ErrorCode::CapabilityUnavailable,
            "Chrome DevTools DOM snapshot contained no logical nodes",
        ));
    }
    Ok((elements, backend_nodes, truncated))
}

fn snapshot_string(value: &Value, strings: &[Value]) -> String {
    if let Some(text) = value.as_str() {
        return text.to_owned();
    }
    value
        .as_u64()
        .and_then(|index| usize::try_from(index).ok())
        .and_then(|index| strings.get(index))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn bounded_snapshot_text(value: &str, max_bytes: usize) -> (String, bool) {
    let mut end = value.len().min(max_bytes);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    (value[..end].to_owned(), end < value.len())
}

fn build_snapshot_envelope(
    space_id: SpaceId,
    page_id: PageId,
    navigation_generation: u64,
    document_generation: u64,
    version: u64,
    mut elements: Vec<SnapshotElement>,
    mut backend_nodes: BTreeMap<ElementKey, i64>,
    mut truncated: bool,
) -> Result<(SnapshotEnvelope, BTreeMap<ElementKey, i64>), CoreError> {
    let frame_id = FrameId::from_suffix("main").map_err(|_| {
        CoreError::new(ErrorCode::InvalidArgument, "main frame identity is invalid")
    })?;
    loop {
        let document = SnapshotDocument::new(SnapshotVersion::new(version), elements.clone())
            .map_err(|error| error.core_error())?;
        let body = SnapshotBody::Elements {
            elements: document.elements.clone(),
        };
        let body_bytes = serde_json::to_vec(&body)
            .map_err(|_| CoreError::new(ErrorCode::InvalidJson, "snapshot body is invalid"))?
            .len();
        let mut envelope = SnapshotEnvelope {
            schema_version: 1,
            space_id: space_id.clone(),
            page_id: page_id.clone(),
            snapshot_version: document.snapshot_version,
            snapshot_hash: document.snapshot_hash.clone(),
            base_snapshot_version: None,
            base_hash: None,
            result_hash: document.snapshot_hash,
            delta_sequence: None,
            topology_version: TopologyVersion::new(1),
            navigation_generation: Generation::new(navigation_generation),
            document_generation: Generation::new(document_generation),
            frame_versions: BTreeMap::from([(frame_id.clone(), FrameVersion::new(1))]),
            changed: Vec::new(),
            delta_or_elements: body,
            mode: if truncated {
                SnapshotMode::Compact
            } else {
                SnapshotMode::Full
            },
            operation_count: 0,
            coherent: !truncated,
            coverage: if truncated {
                SnapshotCoverage::Partial
            } else {
                SnapshotCoverage::Complete
            },
            dirty_reasons: Vec::<DirtyReason>::new(),
            cache_state: CacheState::Fresh,
            resync_reason: None,
            transport_bytes: 0,
            utf8_bytes: body_bytes as u64,
            serialized_tokens: 0,
            model_context_tokens: 0,
            tokenizer: None,
            budget: None,
            omitted: if truncated {
                vec!["bounded logical elements".to_owned()]
            } else {
                Vec::new()
            },
            truncated,
            resync_required: false,
            refs_epoch: RefEpoch::new(version),
        };
        let serialized = serde_json::to_vec(&envelope)
            .map_err(|_| CoreError::new(ErrorCode::InvalidJson, "snapshot envelope is invalid"))?;
        envelope.transport_bytes = serialized.len() as u64;
        let serialized_size = serde_json::to_vec(&envelope)
            .map_err(|_| CoreError::new(ErrorCode::InvalidJson, "snapshot envelope is invalid"))?
            .len();
        if serialized_size <= CDP_MAX_SNAPSHOT_BYTES {
            envelope.validate().map_err(|error| error.core_error())?;
            backend_nodes
                .retain(|key, _| document.elements.iter().any(|element| &element.key == key));
            return Ok((envelope, backend_nodes));
        }
        if elements.len() <= 1 {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                "logical DOM snapshot exceeds the host byte limit",
            ));
        }
        elements.pop();
        truncated = true;
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        sync::{Arc, Mutex},
    };

    use agentyc_core::{ActionId, ContentHash, IdempotencyKey, RequestId};
    use agentyc_core::{ElementRef, SnapshotProvenance, SnapshotVersion};

    use super::*;
    use crate::actionability::ActionabilityEvidence;
    use agentyc_core::{LeaseEpoch, PageId};

    #[derive(Default)]
    struct FakeWire {
        sent: Arc<Mutex<Vec<Value>>>,
        incoming: VecDeque<Value>,
    }

    impl CdpWire for FakeWire {
        fn send(&mut self, value: &Value) -> Result<(), CdpWireError> {
            self.sent
                .lock()
                .map_err(|_| CdpWireError::Unavailable)?
                .push(value.clone());
            Ok(())
        }

        fn receive(&mut self) -> Result<Value, CdpWireError> {
            self.incoming.pop_front().ok_or(CdpWireError::Unavailable)
        }
    }

    #[derive(Default)]
    struct FakeTabCreator {
        bootstrap_urls: Mutex<Vec<String>>,
        presented_groups: Mutex<Vec<(String, String, u64, Option<String>)>>,
    }

    impl TabCreationTransport for FakeTabCreator {
        fn create_tab(&self, bootstrap_url: &str) -> Result<(), CoreError> {
            self.bootstrap_urls
                .lock()
                .map_err(|_| cdp_error(CdpWireError::Unavailable))?
                .push(bootstrap_url.to_owned());
            Ok(())
        }

        fn present_group(
            &self,
            space_id: &SpaceId,
            page_id: &PageId,
            lease_epoch: LeaseEpoch,
            title: Option<&str>,
        ) -> Result<Value, CoreError> {
            self.presented_groups
                .lock()
                .map_err(|_| cdp_error(CdpWireError::Unavailable))?
                .push((
                    space_id.to_string(),
                    page_id.to_string(),
                    lease_epoch.get(),
                    title.map(str::to_owned),
                ));
            Ok(json!({"grouped": true}))
        }
    }

    #[test]
    fn tab_creation_transport_router_forwards_required_group_presentation() {
        let router = TabCreationTransportRouter::default();
        let creator = Arc::new(FakeTabCreator::default());
        router.install(creator.clone()).expect("install transport");
        let space_id = SpaceId::from_suffix("space-one").expect("space id");
        let page_id = PageId::from_suffix("page-one").expect("page id");

        assert_eq!(
            router
                .present_group(&space_id, &page_id, LeaseEpoch::new(2), Some("one"))
                .expect("present group"),
            json!({"grouped": true})
        );
        assert_eq!(
            creator
                .presented_groups
                .lock()
                .expect("creator lock")
                .as_slice(),
            &[(
                "space_space-one".to_owned(),
                "page_page-one".to_owned(),
                2,
                Some("one".to_owned()),
            )]
        );
    }

    #[test]
    fn tab_creation_transport_router_replaces_only_the_create_transport() {
        let router = TabCreationTransportRouter::default();
        let first = Arc::new(FakeTabCreator::default());
        let second = Arc::new(FakeTabCreator::default());
        let bootstrap = "about:blank#agentyc-tab=page_router-1";

        assert_eq!(
            router
                .create_tab(bootstrap)
                .expect_err("router starts disconnected")
                .code,
            ErrorCode::ExtensionNotConnected
        );
        router
            .install(first.clone())
            .expect("install first transport");
        router.create_tab(bootstrap).expect("first transport call");
        router.install(second.clone()).expect("replace transport");
        router.create_tab(bootstrap).expect("replacement call");

        assert_eq!(
            first.bootstrap_urls.lock().expect("first calls").as_slice(),
            &[bootstrap.to_owned()]
        );
        assert_eq!(
            second
                .bootstrap_urls
                .lock()
                .expect("second calls")
                .as_slice(),
            &[bootstrap.to_owned()]
        );
    }

    fn element_action_fixture(
        operation: ActionOperation,
        responses: Vec<Value>,
    ) -> (
        CdpBridge,
        Arc<Mutex<Vec<Value>>>,
        ActionRequest<BTreeMap<String, String>>,
    ) {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let wire = FakeWire {
            sent: Arc::clone(&sent),
            incoming: responses.into(),
        };
        let space_id = SpaceId::from_suffix("action-space").expect("space");
        let page_id = PageId::from_suffix("action-page").expect("page");
        let frame_id = FrameId::from_suffix("main").expect("frame");
        let lease_epoch = LeaseEpoch::new(3);
        let element_key = ElementKey::from_suffix("target").expect("element key");
        let snapshot_hash = ContentHash::from_bytes(b"action snapshot");
        let provenance = SnapshotProvenance {
            space_id: space_id.clone(),
            page_id: page_id.clone(),
            snapshot_version: SnapshotVersion::new(1),
            snapshot_hash: snapshot_hash.clone(),
            document_generation: Generation::new(1),
            navigation_generation: Generation::new(1),
            refs_epoch: RefEpoch::new(1),
            coherent: true,
            coverage: SnapshotCoverage::Complete,
        };
        let element_ref = ElementRef {
            ref_id: agentyc_core::RefId::from_suffix("action-ref").expect("ref"),
            element_key: element_key.clone(),
            space_id: space_id.clone(),
            page_id: page_id.clone(),
            frame_id: frame_id.clone(),
            snapshot_version: SnapshotVersion::new(1),
            document_generation: Generation::new(1),
            navigation_generation: Generation::new(1),
            refs_epoch: RefEpoch::new(1),
        };
        let evidence = ActionabilityEvidence::proven_interactive().with_generations(
            Some(Generation::new(1)),
            Generation::new(1),
            Generation::new(1),
            Some(snapshot_hash),
        );
        let payload = BTreeMap::from([
            (
                "element_ref".to_owned(),
                serde_json::to_string(&element_ref).expect("element ref"),
            ),
            (
                "provenance".to_owned(),
                serde_json::to_string(&provenance).expect("provenance"),
            ),
            (
                "actionability_evidence".to_owned(),
                serde_json::to_string(&evidence).expect("evidence"),
            ),
            ("frame_scope".to_owned(), frame_id.to_string()),
            ("selector".to_owned(), "#must-not-be-used".to_owned()),
        ]);
        let bridge = CdpBridge::with_client(
            CdpClient::with_wire(wire),
            Arc::new(FakeTabCreator::default()),
        );
        bridge.state.lock().expect("bridge state").pages.insert(
            (space_id.clone(), page_id.clone()),
            ManagedPage {
                target_id: "opaque-target".to_owned(),
                session_id: "opaque-session".to_owned(),
                lease_epoch,
                target_generation: 1,
                navigation_generation: 1,
                document_generation: 1,
                url: Some("https://example.test/".to_owned()),
                title: Some("Example".to_owned()),
                snapshot_version: 1,
                backend_nodes: BTreeMap::from([(element_key, 101)]),
            },
        );
        let request = ActionRequest {
            request_id: RequestId::from_suffix("action-request").expect("request"),
            action_id: ActionId::from_suffix("action-id").expect("action"),
            idempotency_key: IdempotencyKey::from_suffix("action-key").expect("key"),
            request_hash: ContentHash::from_bytes(b"action"),
            space_id,
            page_id: Some(page_id),
            lease_epoch,
            operation,
            payload,
            postcondition: None,
        };
        (bridge, sent, request)
    }

    #[test]
    fn command_skips_events_and_correlates_the_result() {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let mut wire = FakeWire {
            sent: Arc::clone(&sent),
            ..FakeWire::default()
        };
        wire.incoming.push_back(json!({
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "page"}}
        }));
        wire.incoming.push_back(json!({
            "id": 1,
            "result": {"sessionId": "opaque-session"}
        }));
        let mut client = CdpClient::with_wire(wire);

        let result = client
            .command(
                "Target.attachToTarget",
                json!({"targetId": "opaque-target"}),
                Some("parent-session"),
            )
            .expect("command succeeds");

        assert_eq!(result["sessionId"], "opaque-session");
        assert_eq!(
            sent.lock().expect("sent").as_slice(),
            [json!({
                "id": 1,
                "method": "Target.attachToTarget",
                "params": {"targetId": "opaque-target"},
                "sessionId": "parent-session"
            })]
        );
    }

    #[test]
    fn command_returns_a_safe_error_for_cdp_failures() {
        let mut wire = FakeWire::default();
        wire.incoming.push_back(json!({
            "id": 1,
            "error": {"code": -32000, "message": "sensitive target detail"}
        }));
        let mut client = CdpClient::with_wire(wire);

        let error = client
            .command("Target.closeTarget", json!({}), None)
            .unwrap_err();

        assert_eq!(error.code, ErrorCode::CapabilityUnavailable);
        assert!(!error.message.contains("sensitive target detail"));
    }

    #[test]
    fn browser_websocket_url_is_restricted_to_the_configured_loopback_port() {
        assert!(
            websocket_request(
                "ws://127.0.0.1:9222/devtools/browser/opaque",
                DEFAULT_CDP_PORT
            )
            .is_ok()
        );
        assert!(
            websocket_request(
                "ws://192.0.2.1:9222/devtools/browser/opaque",
                DEFAULT_CDP_PORT
            )
            .is_err()
        );
        assert!(
            websocket_request(
                "ws://127.0.0.1:9223/devtools/browser/opaque",
                DEFAULT_CDP_PORT
            )
            .is_err()
        );
        assert!(
            websocket_request("ws://127.0.0.1:9222/devtools/page/opaque", DEFAULT_CDP_PORT)
                .is_err()
        );
    }

    #[test]
    fn tab_creation_uses_the_extension_only_for_bootstrap_and_cdp_to_resolve_target() {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let mut wire = FakeWire {
            sent: Arc::clone(&sent),
            ..FakeWire::default()
        };
        let page_id = PageId::from_suffix("one").expect("page");
        let bootstrap = bootstrap_url(&page_id, LeaseEpoch::new(3));
        wire.incoming.push_back(json!({
            "id": 1,
            "result": {
                "targetInfos": [{
                    "targetId": "opaque-target",
                    "type": "page",
                    "url": bootstrap
                }]
            }
        }));
        let creator = Arc::new(FakeTabCreator::default());
        let tab_creation: Arc<dyn TabCreationTransport> = creator.clone();
        let bridge = CdpBridge::with_client(CdpClient::with_wire(wire), tab_creation);

        let target_id = bridge
            .create_tab_and_find_target(&page_id, LeaseEpoch::new(3), Duration::ZERO)
            .expect("target");

        assert_eq!(target_id, "opaque-target");
        assert_eq!(
            creator
                .bootstrap_urls
                .lock()
                .expect("tab creation requests")
                .as_slice(),
            [bootstrap.as_str()]
        );
        assert_eq!(
            sent.lock().expect("CDP commands").as_slice(),
            [json!({
                "id": 1,
                "method": "Target.getTargets",
                "params": {}
            })]
        );
    }

    #[test]
    fn tab_creation_times_out_without_returning_or_retrying_a_browser_handle() {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let mut wire = FakeWire {
            sent: Arc::clone(&sent),
            ..FakeWire::default()
        };
        wire.incoming.push_back(json!({
            "id": 1,
            "result": {"targetInfos": []}
        }));
        let creator = Arc::new(FakeTabCreator::default());
        let tab_creation: Arc<dyn TabCreationTransport> = creator.clone();
        let bridge = CdpBridge::with_client(CdpClient::with_wire(wire), tab_creation);
        let page_id = PageId::from_suffix("one").expect("page");
        let bootstrap = bootstrap_url(&page_id, LeaseEpoch::new(3));

        let error = bridge
            .create_tab_and_find_target(&page_id, LeaseEpoch::new(3), Duration::ZERO)
            .expect_err("missing target");

        assert_eq!(error.code, ErrorCode::UnknownOutcome);
        assert_eq!(
            creator
                .bootstrap_urls
                .lock()
                .expect("tab creation requests")
                .as_slice(),
            [bootstrap]
        );
        assert_eq!(sent.lock().expect("CDP commands").len(), 1);
    }

    #[test]
    fn invalid_bootstrap_urls_are_rejected_before_asking_the_extension() {
        let result = validate_bootstrap_url("https://example.test/");

        assert_eq!(
            result.expect_err("invalid bootstrap").code,
            ErrorCode::InvalidArgument
        );
        assert!(validate_bootstrap_url("about:blank#agentyc-tab=page_one-3").is_ok());
    }

    #[test]
    fn page_navigation_and_close_use_only_internal_cdp_handles() {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let mut wire = FakeWire {
            sent: Arc::clone(&sent),
            ..FakeWire::default()
        };
        wire.incoming.extend([
            json!({"id": 1, "result": {"sessionId": "opaque-session"}}),
            json!({"id": 2, "result": {"frameId": "opaque-frame"}}),
            json!({"id": 3, "result": {"success": true}}),
        ]);
        let mut client = CdpClient::with_wire(wire);

        let session_id = client
            .attach_to_target("opaque-target")
            .expect("attached session");
        client
            .navigate(&session_id, "https://example.test/")
            .expect("navigation");
        client.close_target("opaque-target").expect("closed");

        assert_eq!(
            sent.lock().expect("CDP commands").as_slice(),
            [
                json!({
                    "id": 1,
                    "method": "Target.attachToTarget",
                    "params": {"targetId": "opaque-target", "flatten": true}
                }),
                json!({
                    "id": 2,
                    "method": "Page.navigate",
                    "params": {"url": "https://example.test/"},
                    "sessionId": "opaque-session"
                }),
                json!({
                    "id": 3,
                    "method": "Target.closeTarget",
                    "params": {"targetId": "opaque-target"}
                })
            ]
        );
    }

    #[test]
    fn cdp_bridge_creates_pages_without_exposing_browser_identifiers() {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let mut wire = FakeWire {
            sent: Arc::clone(&sent),
            ..FakeWire::default()
        };
        let space_id = SpaceId::from_suffix("space").expect("space");
        let page_id = PageId::from_suffix("page").expect("page");
        let lease_epoch = LeaseEpoch::new(3);
        let bootstrap = bootstrap_url(&page_id, lease_epoch);
        wire.incoming.extend([
            json!({
                "id": 1,
                "result": {
                    "targetInfos": [{
                        "targetId": "opaque-target",
                        "type": "page",
                        "url": bootstrap
                    }]
                }
            }),
            json!({"id": 2, "result": {"sessionId": "opaque-session"}}),
            json!({"id": 3, "result": {}}),
            json!({"id": 4, "result": {}}),
            json!({"id": 5, "result": {"frameId": "opaque-frame"}}),
            json!({
                "id": 6,
                "result": {
                    "documents": [{
                        "nodes": {
                            "nodeType": [9, 1, 3],
                            "nodeName": [0, 1, 2],
                            "nodeValue": [4, 4, 3],
                            "parentIndex": [-1, 0, 1],
                            "attributes": [[], [6, 7], []],
                            "backendNodeId": [101, 102, 103]
                        }
                    }],
                    "strings": ["#document", "HTML", "#text", "Hello", "", "unused", "id", "root"]
                }
            }),
            json!({"id": 7, "result": {"frameId": "opaque-frame"}}),
            json!({"id": 8, "result": {"success": true}}),
        ]);
        let creator = Arc::new(FakeTabCreator::default());
        let tab_creation: Arc<dyn TabCreationTransport> = creator.clone();
        let bridge = CdpBridge::with_client(CdpClient::with_wire(wire), tab_creation);
        assert_eq!(
            bridge.capabilities(),
            [Capability::Snapshot, Capability::Action]
        );

        let result = bridge
            .create_page(
                &space_id,
                &page_id,
                lease_epoch,
                Some("https://example.test/"),
                Some("Example"),
                json!({
                    "issued_by_host": true,
                    "kind": "creation",
                    "space_id": space_id.as_str(),
                    "page_id": page_id.as_str(),
                    "lease_epoch": lease_epoch.get()
                }),
            )
            .expect("managed page");

        assert_eq!(result["space_id"], space_id.as_str());
        assert_eq!(result["page_id"], page_id.as_str());
        assert_eq!(result["url"], "https://example.test/");
        assert!(!result.to_string().contains("opaque-target"));
        assert!(!result.to_string().contains("opaque-session"));
        let snapshot = bridge
            .snapshot(&space_id, &page_id, lease_epoch)
            .expect("snapshot");
        snapshot.validate().expect("valid snapshot");
        assert_eq!(snapshot.snapshot_version.get(), 1);
        let snapshot_json = serde_json::to_string(&snapshot).expect("snapshot JSON");
        assert!(!snapshot_json.contains("backendNodeId"));
        assert!(!snapshot_json.contains("targetId"));
        assert!(!snapshot_json.contains("sessionId"));
        assert_eq!(
            bridge
                .state
                .lock()
                .expect("bridge state")
                .pages
                .get(&(space_id.clone(), page_id.clone()))
                .expect("managed page")
                .backend_nodes
                .len(),
            3
        );
        let request = ActionRequest {
            request_id: RequestId::from_suffix("navigate-request").expect("request"),
            action_id: ActionId::from_suffix("navigate-action").expect("action"),
            idempotency_key: IdempotencyKey::from_suffix("navigate-key").expect("key"),
            request_hash: ContentHash::from_bytes(b"navigate"),
            space_id: space_id.clone(),
            page_id: Some(page_id.clone()),
            lease_epoch,
            operation: ActionOperation::Navigate,
            payload: std::collections::BTreeMap::from([(
                "url".to_owned(),
                "https://example.test/next".to_owned(),
            )]),
            postcondition: None,
        };
        assert_eq!(
            bridge.dispatch(&request).expect("navigate"),
            BridgeDispatchResult::Succeeded
        );
        let observed = bridge.observe().expect("observation");
        assert!(
            !serde_json::to_string(&observed.pages)
                .expect("serialized observation")
                .contains("opaque-target")
        );
        assert!(
            !serde_json::to_string(&observed.pages)
                .expect("serialized observation")
                .contains("opaque-session")
        );
        bridge
            .close_page(&space_id, &page_id, lease_epoch)
            .expect("close");
        assert!(
            bridge
                .observe()
                .expect("closed observation")
                .pages
                .is_empty()
        );
        assert_eq!(
            creator
                .bootstrap_urls
                .lock()
                .expect("tab creation requests")
                .as_slice(),
            [bootstrap.as_str()]
        );
        assert_eq!(
            sent.lock().expect("CDP commands").len(),
            8,
            "all snapshots, navigation, and close operations after creation use host CDP"
        );
    }

    #[test]
    fn navigation_failure_does_not_expose_raw_cdp_error_text() {
        let mut wire = FakeWire::default();
        wire.incoming.push_back(json!({
            "id": 1,
            "result": {"errorText": "sensitive browser diagnostic"}
        }));
        let mut client = CdpClient::with_wire(wire);

        let error = client
            .navigate("opaque-session", "https://example.test/")
            .expect_err("navigation failure");

        assert_eq!(error.code, ErrorCode::CapabilityUnavailable);
        assert!(!error.message.contains("sensitive browser diagnostic"));
    }

    #[test]
    fn lost_cdp_connection_makes_mutation_unknown_and_invalidates_page_sessions() {
        let (bridge, sent, mut request) =
            element_action_fixture(ActionOperation::Navigate, Vec::new());
        request
            .payload
            .insert("url".to_owned(), "https://example.test/next".to_owned());

        assert_eq!(
            bridge.dispatch(&request).expect("dispatch"),
            BridgeDispatchResult::Unknown {
                reason: UnknownReason::BridgeLost
            }
        );
        assert_eq!(
            sent.lock().expect("CDP commands").as_slice(),
            [json!({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": "https://example.test/next"},
                "sessionId": "opaque-session"
            })]
        );
        assert!(
            bridge.observe().expect("observation").pages.is_empty(),
            "a lost connection invalidates cached target/session bindings"
        );

        assert!(matches!(
            bridge.dispatch(&request).expect("retry dispatch"),
            BridgeDispatchResult::Failed {
                code: ErrorCode::PageNotFound,
                ..
            }
        ));
        assert_eq!(
            sent.lock().expect("CDP commands").len(),
            1,
            "the original mutation is not sent again after transport loss"
        );

        let reconnected_sent = Arc::new(Mutex::new(Vec::new()));
        let reconnected = CdpBridge::with_client(
            CdpClient::with_wire(FakeWire {
                sent: Arc::clone(&reconnected_sent),
                ..FakeWire::default()
            }),
            Arc::new(FakeTabCreator::default()),
        );
        assert!(matches!(
            reconnected
                .dispatch(&request)
                .expect("reconnected dispatch"),
            BridgeDispatchResult::Failed {
                code: ErrorCode::PageNotFound,
                ..
            }
        ));
        assert!(
            reconnected_sent
                .lock()
                .expect("reconnected commands")
                .is_empty(),
            "a fresh connection does not restore or reuse the old session binding"
        );
    }

    #[test]
    fn subframe_actions_are_deferred_without_host_frame_attribution() {
        let (bridge, sent, mut request) =
            element_action_fixture(ActionOperation::Click, Vec::new());
        let child_frame = FrameId::from_suffix("child").expect("child frame");
        let mut element_ref: ElementRef = serde_json::from_str(
            request
                .payload
                .get("element_ref")
                .expect("element ref payload"),
        )
        .expect("element ref");
        element_ref.frame_id = child_frame.clone();
        request.payload.insert(
            "element_ref".to_owned(),
            serde_json::to_string(&element_ref).expect("serialize element ref"),
        );
        request
            .payload
            .insert("frame_scope".to_owned(), child_frame.to_string());

        assert!(matches!(
            bridge.dispatch(&request).expect("dispatch"),
            BridgeDispatchResult::Failed {
                code: ErrorCode::StaleRef,
                ..
            }
        ));
        assert!(
            sent.lock().expect("CDP commands").is_empty(),
            "subframe/OOPIF actions are unsupported until host frame attribution exists"
        );
    }

    #[test]
    fn click_dispatch_uses_live_ref_geometry_and_verifies_the_hit_ancestry() {
        let (bridge, sent, request) = element_action_fixture(
            ActionOperation::Click,
            vec![
                json!({"id": 1, "result": {"root": {"nodeId": 1}}}),
                json!({"id": 2, "result": {"node": {
                    "nodeId": 11,
                    "backendNodeId": 101,
                    "nodeType": 1,
                    "nodeName": "BUTTON",
                    "localName": "button",
                    "attributes": []
                }}}),
                json!({"id": 3, "result": {"model": {
                    "content": [10, 10, 30, 10, 30, 30, 10, 30]
                }}}),
                json!({"id": 4, "result": {
                    "backendNodeId": 102,
                    "nodeId": 22,
                    "frameId": "opaque-frame"
                }}),
                json!({"id": 5, "result": {"node": {
                    "nodeId": 22,
                    "backendNodeId": 102,
                    "nodeType": 1,
                    "nodeName": "SPAN",
                    "parentId": 11
                }}}),
                json!({"id": 6, "result": {}}),
                json!({"id": 7, "result": {}}),
            ],
        );

        assert_eq!(
            bridge.dispatch(&request).expect("dispatch"),
            BridgeDispatchResult::Succeeded
        );
        let commands = sent.lock().expect("CDP commands");
        assert_eq!(
            commands
                .iter()
                .map(|command| command["method"].as_str().expect("method"))
                .collect::<Vec<_>>(),
            [
                "DOM.getDocument",
                "DOM.describeNode",
                "DOM.getBoxModel",
                "DOM.getNodeForLocation",
                "DOM.describeNode",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
            ]
        );
        assert_eq!(commands[2]["params"]["backendNodeId"], 101);
        assert_eq!(commands[3]["params"], json!({"x": 20, "y": 20}));
        assert_eq!(commands[5]["params"]["type"], "mousePressed");
        assert_eq!(commands[6]["params"]["type"], "mouseReleased");
        assert!(
            !commands
                .iter()
                .any(|command| command.to_string().contains("#must-not-be-used"))
        );
    }

    #[test]
    fn input_dispatch_uses_only_the_ref_bound_editable_control() {
        let (bridge, sent, mut request) = element_action_fixture(
            ActionOperation::Input,
            vec![
                json!({"id": 1, "result": {"root": {"nodeId": 1}}}),
                json!({"id": 2, "result": {"node": {
                    "nodeId": 11,
                    "backendNodeId": 101,
                    "nodeType": 1,
                    "nodeName": "INPUT",
                    "localName": "input",
                    "attributes": ["type", "text"]
                }}}),
                json!({"id": 3, "result": {}}),
                json!({"id": 4, "result": {}}),
            ],
        );
        request
            .payload
            .insert("text".to_owned(), "typed text".to_owned());

        assert_eq!(
            bridge.dispatch(&request).expect("dispatch"),
            BridgeDispatchResult::Succeeded
        );
        let commands = sent.lock().expect("CDP commands");
        assert_eq!(
            commands
                .iter()
                .map(|command| command["method"].as_str().expect("method"))
                .collect::<Vec<_>>(),
            [
                "DOM.getDocument",
                "DOM.describeNode",
                "DOM.focus",
                "Input.insertText",
            ]
        );
        assert_eq!(commands[2]["params"]["backendNodeId"], 101);
        assert_eq!(commands[3]["params"]["text"], "typed text");
        assert!(
            !commands
                .iter()
                .any(|command| command.to_string().contains("#must-not-be-used"))
        );
    }

    #[test]
    fn missing_or_stale_element_refs_fail_before_any_cdp_action() {
        let (bridge, sent, mut request) =
            element_action_fixture(ActionOperation::Click, Vec::new());
        request.payload.remove("element_ref");
        request.payload.remove("provenance");
        request.payload.remove("actionability_evidence");
        request.payload.remove("frame_scope");

        assert!(matches!(
            bridge.dispatch(&request).expect("dispatch result"),
            BridgeDispatchResult::Failed {
                code: ErrorCode::InvalidArgument,
                ..
            }
        ));
        assert!(sent.lock().expect("CDP commands").is_empty());
    }
}
