//! agentyc-mcp: 77-tool browser automation MCP server.
#![allow(clippy::collapsible_if)]

mod state;
mod tools;

use agentyc_runtime::BrowserRuntime;
use anyhow::Result;
use rmcp::{
    ServerHandler, ServiceExt,
    handler::server::router::tool::ToolRouter,
    model::{ServerCapabilities, ServerInfo},
    tool_handler, tool_router,
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::Mutex;

use tools::{ServerState, SharedState};

// ── Schema-constrained enum types ─────────────────────────────────────────────

#[derive(Deserialize, JsonSchema, Default)]
#[serde(rename_all = "lowercase")]
enum StateMode {
    #[default]
    Auto,
    Full,
    Min,
    Focus,
}
impl StateMode {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Full => "full",
            Self::Min => "min",
            Self::Focus => "focus",
        }
    }
}

#[derive(Deserialize, JsonSchema, Default)]
#[serde(rename_all = "lowercase")]
enum ScrollDir {
    Down,
    #[default]
    Up,
}
impl ScrollDir {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Down => "down",
            Self::Up => "up",
        }
    }
}

/// agentyc browser automation MCP server with all 76 tools.
#[derive(Clone)]
pub struct BrowserServer {
    state: SharedState,
    tool_router: ToolRouter<Self>,
}

impl BrowserServer {
    pub fn new() -> Self {
        Self::with_cdp_url(None)
    }

    pub fn with_cdp_url(cdp_url: Option<String>) -> Self {
        let state = Arc::new(Mutex::new(ServerState::with_cdp_url(cdp_url)));
        let mut tr = Self::tool_router_nav()
            + Self::tool_router_state()
            + Self::tool_router_interaction()
            + Self::tool_router_inspection()
            + Self::tool_router_frames()
            + Self::tool_router_tabs();
        // Opt-in extended profile adds the observability tools. Kept out of the
        // default set so tools/list stays small and context-cheap for agents.
        if tools::extended_profile() {
            tr += Self::tool_router_observability();
        }
        // Strip verbose schemars-generated fields from every tool's inputSchema
        // to reduce tools/list payload size (~30% reduction).
        slim_tool_schemas(&mut tr);
        Self {
            state,
            tool_router: tr,
        }
    }

    /// Connect to an existing browser via CDP URL.
    pub async fn connect(&self, cdp_url: &str) -> Result<()> {
        let runtime = Arc::new(BrowserRuntime::connect(cdp_url).await?);
        let previous = { self.state.lock().await.runtime.clone() };
        if let Some(previous) = previous {
            previous.close().await.ok();
        }
        {
            let mut g = self.state.lock().await;
            g.clear_browser_scoped_state();
            g.cdp_url = Some(cdp_url.to_string());
            g.runtime = Some(runtime);
            g.dialog_handler_started = false;
            g.capture_started = false;
        }
        // Deterministic dialog handling + (extended) observability capture.
        tools::ensure_dialog_handler(&self.state).await;
        tools::ensure_capture(&self.state).await;
        Ok(())
    }
}

impl Default for BrowserServer {
    fn default() -> Self {
        Self::new()
    }
}

/// Strip verbose schemars fields from inputSchema to reduce tools/list payload.
/// Removes: "$schema", "title", "format" (per-property), "$defs".
fn slim_tool_schemas(tr: &mut rmcp::handler::server::router::tool::ToolRouter<BrowserServer>) {
    for route in tr.map.values_mut() {
        let schema = std::sync::Arc::make_mut(&mut route.attr.input_schema);
        schema.remove("$schema");
        schema.remove("title");
        schema.remove("$defs");
        if let Some(serde_json::Value::Object(props)) = schema.get_mut("properties") {
            for prop in props.values_mut() {
                if let serde_json::Value::Object(p) = prop {
                    p.remove("format");
                    p.remove("title");
                    // Simplify nullable types: ["T", "null"] -> just keep as-is but remove format
                }
            }
        }
    }
}

// ── Navigation tools ─────────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct NavigateParams {
    url: String,
    new_tab: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitParams {
    seconds: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForUrlParams {
    url_substring: Option<String>,
    url_regex: Option<String>,
    timeout_seconds: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForNetworkIdleParams {
    timeout_seconds: Option<f64>,
    idle_duration_ms: Option<u64>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForRequestParams {
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    timeout_seconds: Option<f64>,
    include_headers: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForResponseParams {
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    status: Option<u32>,
    timeout_seconds: Option<f64>,
    include_headers: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForStableDomParams {
    timeout_seconds: Option<f64>,
    quiet_ms: Option<u64>,
}

#[tool_router(router = tool_router_nav, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(description = "Navigate to a URL")]
    async fn browser_navigate(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<NavigateParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::navigation::browser_navigate(&self.state, p.0.url, p.0.new_tab).await)
    }
    #[rmcp::tool(description = "Go back in browser history.")]
    async fn browser_go_back(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::navigation::browser_go_back(&self.state).await)
    }
    #[rmcp::tool(description = "Go forward in browser history.")]
    async fn browser_go_forward(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::navigation::browser_go_forward(&self.state).await)
    }
    #[rmcp::tool(description = "Reload the current page.")]
    async fn browser_refresh(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::navigation::browser_refresh(&self.state).await)
    }
    #[rmcp::tool(description = "Wait N seconds. Prefer since_hash polling for dynamic content.")]
    async fn browser_wait(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::navigation::browser_wait(p.0.seconds).await)
    }
    #[rmcp::tool(
        description = "Wait until the current page URL matches a substring or regex. Use after delayed navigation or History API route changes."
    )]
    async fn browser_wait_for_url(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForUrlParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::navigation::browser_wait_for_url(
                &self.state,
                p.0.url_substring,
                p.0.url_regex,
                p.0.timeout_seconds,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Wait until no network requests are pending. Use after AJAX calls, form submissions, or SPA navigation."
    )]
    async fn browser_wait_for_network_idle(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForNetworkIdleParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::navigation::browser_wait_for_network_idle(
                &self.state,
                p.0.timeout_seconds,
                p.0.idle_duration_ms,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Wait until a matching network request is observed. Use after clicks or JS actions that trigger fetch/XHR."
    )]
    async fn browser_wait_for_request(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForRequestParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::navigation::browser_wait_for_request(
                &self.state,
                p.0.url_substring,
                p.0.url_regex,
                p.0.method,
                p.0.resource_type,
                p.0.timeout_seconds,
                p.0.include_headers,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Wait until a matching network response arrives, optionally filtered by status. More precise than network-idle for API debugging."
    )]
    async fn browser_wait_for_response(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForResponseParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::navigation::browser_wait_for_response(
                &self.state,
                p.0.url_substring,
                p.0.url_regex,
                p.0.method,
                p.0.resource_type,
                p.0.status,
                p.0.timeout_seconds,
                p.0.include_headers,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Wait until the DOM has been stable (no mutations) for a quiet period. Use after AJAX calls, form submissions, or SPA navigation to let the page finish rendering before reading state."
    )]
    async fn browser_wait_for_stable_dom(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForStableDomParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::navigation::browser_wait_for_stable_dom(
                &self.state,
                p.0.timeout_seconds,
                p.0.quiet_ms,
            )
            .await,
        )
    }
}

// ── State tools ───────────────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct GetStateParams {
    mode: Option<StateMode>,
    focus_ref: Option<String>,
    since_hash: Option<String>,
    include_screenshot: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct GetHtmlParams {
    selector: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct ScreenshotParams {
    full_page: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct SaveAsPdfParams {
    file_name: Option<String>,
    print_background: Option<bool>,
    landscape: Option<bool>,
    scale: Option<f64>,
    paper_format: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetViewportParams {
    width: u32,
    height: u32,
    device_scale_factor: Option<f64>,
}

#[tool_router(router = tool_router_state, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "Get the current page state: URL, title, interactive elements with stable refs. Pass since_hash to skip unchanged re-reads."
    )]
    async fn browser_get_state(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetStateParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::state_tools::browser_get_state(
                &self.state,
                p.0.mode.map(|m| m.as_str().to_string()),
                p.0.focus_ref,
                p.0.since_hash,
                p.0.include_screenshot,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Get raw HTML of the current page or a CSS-selected element.")]
    async fn browser_get_html(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetHtmlParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::state_tools::browser_get_html(&self.state, p.0.selector).await)
    }
    #[rmcp::tool(description = "Take a screenshot. Returns viewport metadata (text) and image.")]
    async fn browser_screenshot(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ScreenshotParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::state_tools::browser_screenshot(&self.state, p.0.full_page).await)
    }
    #[rmcp::tool(
        description = "Save the current page as a PDF file and return the file path. Uses CDP Page.printToPDF."
    )]
    async fn browser_save_as_pdf(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SaveAsPdfParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::state_tools::browser_save_as_pdf(
                &self.state,
                p.0.file_name,
                p.0.print_background,
                p.0.landscape,
                p.0.scale,
                p.0.paper_format,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Set the browser viewport size (width x height). Applies to the current tab."
    )]
    async fn browser_set_viewport(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetViewportParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::state_tools::browser_set_viewport(
                &self.state,
                p.0.width,
                p.0.height,
                p.0.device_scale_factor,
            )
            .await,
        )
    }
}

// ── Interaction tools ─────────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct ClickParams {
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
    wait_for_url_substring: Option<String>,
    wait_for_url_regex: Option<String>,
    url_timeout_seconds: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct MouseParams {
    r#ref: Option<String>,
    index: Option<u64>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct TypeParams {
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    text: String,
}
#[derive(Deserialize, JsonSchema)]
struct FillFormParams {
    fields: Vec<Value>,
}
#[derive(Deserialize, JsonSchema)]
struct PressKeyParams {
    key: String,
}
#[derive(Deserialize, JsonSchema)]
struct ScrollParams {
    direction: Option<ScrollDir>,
    pages: Option<f64>,
    r#ref: Option<String>,
    index: Option<u64>,
}
#[derive(Deserialize, JsonSchema)]
struct ScrollToTextParams {
    text: String,
}
#[derive(Deserialize, JsonSchema)]
struct SelectOptionParams {
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    text: String,
}
#[derive(Deserialize, JsonSchema)]
struct DropdownParams {
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct UploadParams {
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    path: String,
}
#[derive(Deserialize, JsonSchema)]
struct HandleDialogParams {
    accept: Option<bool>,
    prompt_text: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct DragToParams {
    source_ref: Option<String>,
    target_ref: Option<String>,
    source_x: Option<f64>,
    source_y: Option<f64>,
    target_x: Option<f64>,
    target_y: Option<f64>,
    steps: Option<u32>,
}

#[tool_router(router = tool_router_interaction, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "Click an element (ref preferred) or viewport coordinates. Optionally wait for a URL change triggered by the click."
    )]
    async fn browser_click(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ClickParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_click(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.label,
                p.0.coordinate_x,
                p.0.coordinate_y,
                p.0.wait_for_url_substring,
                p.0.wait_for_url_regex,
                p.0.url_timeout_seconds,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Right-click to open a context menu.")]
    async fn browser_right_click(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<MouseParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_right_click(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.coordinate_x,
                p.0.coordinate_y,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Double-click an element or coordinates. Use for text selection, file open, or double-click handlers."
    )]
    async fn browser_double_click(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<MouseParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_double_click(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.coordinate_x,
                p.0.coordinate_y,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Hover over an element to trigger :hover states and mouseover handlers. Use before browser_get_state to reveal dropdown menus."
    )]
    async fn browser_hover(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<MouseParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_hover(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.coordinate_x,
                p.0.coordinate_y,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Drag from source to target (kanban, sortable lists, sliders, file drop zones)."
    )]
    async fn browser_drag_to(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<DragToParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_drag_to(
                &self.state,
                p.0.source_ref,
                p.0.target_ref,
                p.0.source_x,
                p.0.source_y,
                p.0.target_x,
                p.0.target_y,
                p.0.steps,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Type text into a field. Clears existing text first. Use text=\"\" to clear only."
    )]
    async fn browser_type(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<TypeParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_type(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.label,
                p.0.text,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Fill multiple fields in a single round trip. Each field item targets a ref, index, or label and provides exactly one of text, option_text, path, or checked."
    )]
    async fn browser_fill_form(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<FillFormParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::interaction::browser_fill_form(&self.state, p.0.fields).await)
    }
    #[rmcp::tool(
        description = "Send a key or shortcut (e.g. \"Enter\", \"Tab\", \"Control+a\", \"Meta+r\")."
    )]
    async fn browser_press_key(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PressKeyParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::interaction::browser_press_key(&self.state, p.0.key).await)
    }
    #[rmcp::tool(description = "Scroll the page or an element. pages=10 reaches the bottom fast.")]
    async fn browser_scroll(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ScrollParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_scroll(
                &self.state,
                p.0.direction.map(|d| d.as_str().to_string()),
                p.0.pages,
                p.0.r#ref,
                p.0.index,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Scroll until the given text is visible in the viewport.")]
    async fn browser_scroll_to_text(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ScrollToTextParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::interaction::browser_scroll_to_text(&self.state, p.0.text).await)
    }
    #[rmcp::tool(description = "Select an option in a <select> dropdown by its visible label.")]
    async fn browser_select_option(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SelectOptionParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_select_option(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.label,
                p.0.text,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "List all options in a <select> or ARIA combobox.")]
    async fn browser_get_dropdown_options(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<DropdownParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_get_dropdown_options(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.label,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Upload a local file to a file input element.")]
    async fn browser_upload_file(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<UploadParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_upload_file(
                &self.state,
                p.0.r#ref,
                p.0.index,
                p.0.label,
                p.0.path,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Accept or dismiss a JavaScript dialog (alert, confirm, prompt, beforeunload). Use when a dialog is blocking further interaction."
    )]
    async fn browser_handle_dialog(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<HandleDialogParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::interaction::browser_handle_dialog(&self.state, p.0.accept, p.0.prompt_text)
                .await,
        )
    }
}

// ── Inspection tools ──────────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct ExtractContentParams {
    query: String,
    extract_links: Option<bool>,
    output_schema: Option<Value>,
}
#[derive(Deserialize, JsonSchema)]
struct FindElementsParams {
    selector: String,
    attributes: Option<Vec<String>>,
    max_results: Option<u32>,
}
#[derive(Deserialize, JsonSchema)]
struct SearchPageParams {
    pattern: String,
    regex: Option<bool>,
    max_results: Option<u32>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForElementParams {
    text: Option<String>,
    r#ref: Option<String>,
    appear: Option<bool>,
    timeout_seconds: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct GetAttributeParams {
    name: String,
    r#ref: Option<String>,
    index: Option<u64>,
}
#[derive(Deserialize, JsonSchema)]
struct EvaluateParams {
    code: String,
}

#[tool_router(router = tool_router_inspection, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "Deterministically extract structured content (tables, lists, links, images, form fields, key-values) by query. No LLM fallback."
    )]
    async fn browser_extract_content(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ExtractContentParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::inspection::browser_extract_content(
                &self.state,
                p.0.query,
                p.0.extract_links,
                p.0.output_schema,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Find elements by CSS selector. Returns tag, text, and requested attributes."
    )]
    async fn browser_find_elements(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<FindElementsParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::inspection::browser_find_elements(
                &self.state,
                p.0.selector,
                p.0.attributes,
                p.0.max_results,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Search for text or regex on the page with surrounding context (like Ctrl+F)."
    )]
    async fn browser_search_page(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SearchPageParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::inspection::browser_search_page(
                &self.state,
                p.0.pattern,
                p.0.regex,
                p.0.max_results,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Poll until an element (by text or ref) appears or disappears. Use for async content and action confirmation."
    )]
    async fn browser_wait_for_element(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForElementParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::inspection::browser_wait_for_element(
                &self.state,
                p.0.text,
                p.0.r#ref,
                p.0.appear,
                p.0.timeout_seconds,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Return the element with keyboard focus. Useful after Tab or click to confirm which field is active."
    )]
    async fn browser_get_focused_element(
        &self,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::inspection::browser_get_focused_element(&self.state).await)
    }
    #[rmcp::tool(
        description = "Get a specific attribute value from a page element by ref or index."
    )]
    async fn browser_get_attribute(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetAttributeParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::inspection::browser_get_attribute(&self.state, p.0.name, p.0.r#ref, p.0.index)
                .await,
        )
    }
    #[rmcp::tool(
        description = "Execute JavaScript in the page and return the result. Wrap in IIFE: (function(){ ... })()"
    )]
    async fn browser_evaluate(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<EvaluateParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::inspection::browser_evaluate(&self.state, p.0.code).await)
    }
}

// ── Frames & Storage tools ────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct GetFrameHtmlParams {
    frame_id: String,
}
#[derive(Deserialize, JsonSchema)]
struct GetStorageParams {
    origin: Option<String>,
    storage_type: Option<String>,
    key: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetStorageParams {
    origin: String,
    storage_type: String,
    key: String,
    value: String,
}
#[derive(Deserialize, JsonSchema)]
struct ClearStorageParams {
    origin: String,
    storage_type: Option<String>,
    key: Option<String>,
}

#[tool_router(router = tool_router_frames, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "List known page frames, including cross-origin frames and their IDs."
    )]
    async fn browser_list_frames(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::frames_storage::browser_list_frames(&self.state).await)
    }
    #[rmcp::tool(description = "Get raw HTML for a specific frame by frame_id.")]
    async fn browser_get_frame_html(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetFrameHtmlParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::frames_storage::browser_get_frame_html(&self.state, p.0.frame_id).await)
    }
    #[rmcp::tool(description = "Inspect localStorage and sessionStorage by origin, type, or key.")]
    async fn browser_get_storage(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetStorageParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::frames_storage::browser_get_storage(
                &self.state,
                p.0.origin,
                p.0.storage_type,
                p.0.key,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Set one localStorage or sessionStorage key for the current origin-scoped page context."
    )]
    async fn browser_set_storage(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetStorageParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::frames_storage::browser_set_storage(
                &self.state,
                p.0.origin,
                p.0.storage_type,
                p.0.key,
                p.0.value,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Clear storage for the current origin-scoped page context, optionally by area or key."
    )]
    async fn browser_clear_storage(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ClearStorageParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::frames_storage::browser_clear_storage(
                &self.state,
                p.0.origin,
                p.0.storage_type,
                p.0.key,
            )
            .await,
        )
    }
}

// ── Tabs & Session tools ──────────────────────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct NewTabParams {
    url: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct TabIdParams {
    tab_id: String,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForTabParams {
    url_substring: Option<String>,
    url_regex: Option<String>,
    timeout_seconds: Option<f64>,
    switch_focus: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct SetCookiesParams {
    cookies: Vec<Value>,
}
#[derive(Deserialize, JsonSchema)]
struct ClearCookiesParams {
    name: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct GrantPermissionsParams {
    permissions: Vec<String>,
    origin: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetGeolocationParams {
    latitude: f64,
    longitude: f64,
    accuracy: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct SetExtraHeadersParams {
    headers: std::collections::HashMap<String, String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetUserAgentParams {
    user_agent: String,
    accept_language: Option<String>,
    platform: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetTimezoneParams {
    timezone_id: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetLocaleParams {
    locale: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct EmulateMediaParams {
    media: Option<String>,
    color_scheme: Option<String>,
    reduced_motion: Option<String>,
    forced_colors: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SaveStateParams {
    path: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct LoadStateParams {
    path: String,
}
#[derive(Deserialize, JsonSchema)]
struct SessionIdParams {
    session_id: String,
}

#[tool_router(router = tool_router_tabs, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "Create a new browser tab and switch focus to it. Use for parallel automation: each subagent calls this to get its own tab without disturbing others."
    )]
    async fn browser_new_tab(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<NewTabParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_new_tab(&self.state, p.0.url).await)
    }
    #[rmcp::tool(description = "List all open tabs.")]
    async fn browser_list_tabs(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_list_tabs(&self.state).await)
    }
    #[rmcp::tool(description = "Switch to a tab by its 4-char tab_id.")]
    async fn browser_switch_tab(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<TabIdParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_switch_tab(&self.state, p.0.tab_id).await)
    }
    #[rmcp::tool(description = "Close a tab by its 4-char tab_id.")]
    async fn browser_close_tab(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<TabIdParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_close_tab(&self.state, p.0.tab_id).await)
    }
    #[rmcp::tool(description = "Wait until a new tab appears and optionally switch focus to it.")]
    async fn browser_wait_for_tab(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForTabParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::tabs_session::browser_wait_for_tab(
                &self.state,
                p.0.url_substring,
                p.0.url_regex,
                p.0.timeout_seconds,
                p.0.switch_focus,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Get cookies for the current page (name, value, domain, path, flags)."
    )]
    async fn browser_get_cookies(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_get_cookies(&self.state).await)
    }
    #[rmcp::tool(
        description = "Set cookies. Use to inject auth tokens before navigating to a protected URL."
    )]
    async fn browser_set_cookies(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetCookiesParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_set_cookies(&self.state, p.0.cookies).await)
    }
    #[rmcp::tool(
        description = "Clear cookies. Omit name to clear all for current domain; pass name to delete one."
    )]
    async fn browser_clear_cookies(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ClearCookiesParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_clear_cookies(&self.state, p.0.name).await)
    }
    #[rmcp::tool(
        description = "Grant browser permissions such as geolocation for the current origin or an explicit origin."
    )]
    async fn browser_grant_permissions(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GrantPermissionsParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::tabs_session::browser_grant_permissions(
                &self.state,
                p.0.permissions,
                p.0.origin,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Override browser geolocation for the current session.")]
    async fn browser_set_geolocation(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetGeolocationParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::tabs_session::browser_set_geolocation(
                &self.state,
                p.0.latitude,
                p.0.longitude,
                p.0.accuracy,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Set extra HTTP headers for the focused page target. Pass an empty object to clear."
    )]
    async fn browser_set_extra_headers(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetExtraHeadersParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_set_extra_headers(&self.state, p.0.headers).await)
    }
    #[rmcp::tool(description = "Override the user agent for the focused page target.")]
    async fn browser_set_user_agent(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetUserAgentParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::tabs_session::browser_set_user_agent(
                &self.state,
                p.0.user_agent,
                p.0.accept_language,
                p.0.platform,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Override the timezone for the focused page target. Pass an empty string to clear."
    )]
    async fn browser_set_timezone(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetTimezoneParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_set_timezone(&self.state, p.0.timezone_id).await)
    }
    #[rmcp::tool(
        description = "Override the locale for the focused page target. Omit or pass an empty string to clear."
    )]
    async fn browser_set_locale(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetLocaleParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_set_locale(&self.state, p.0.locale).await)
    }
    #[rmcp::tool(
        description = "Emulate CSS media type and key user-preference media features for the focused page target."
    )]
    async fn browser_emulate_media(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<EmulateMediaParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::tabs_session::browser_emulate_media(
                &self.state,
                p.0.media,
                p.0.color_scheme,
                p.0.reduced_motion,
                p.0.forced_colors,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Save cookies, localStorage, and sessionStorage to a file for auth persistence across sessions."
    )]
    async fn browser_save_state(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SaveStateParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_save_state(&self.state, p.0.path).await)
    }
    #[rmcp::tool(
        description = "Restore browser state (cookies, localStorage) from a file saved by browser_save_state."
    )]
    async fn browser_load_state(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LoadStateParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_load_state(&self.state, p.0.path).await)
    }
    #[rmcp::tool(description = "List active browser sessions with status and last activity.")]
    async fn browser_list_sessions(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_list_sessions(&self.state).await)
    }
    #[rmcp::tool(description = "Close a browser session by ID (from browser_list_sessions).")]
    async fn browser_close_session(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SessionIdParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_close_session(&self.state, p.0.session_id).await)
    }
    #[rmcp::tool(description = "Close all browser sessions.")]
    async fn browser_close_all(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::tabs_session::browser_close_all(&self.state).await)
    }
}

// ── Observability tools (extended profile) ────────────────────────────────────

#[derive(Deserialize, JsonSchema)]
struct GetConsoleLogsParams {
    level: Option<String>,
    max_entries: Option<usize>,
}
#[derive(Deserialize, JsonSchema)]
struct GetNetworkLogParams {
    type_filter: Option<String>,
    status_filter: Option<String>,
    max_entries: Option<usize>,
    include_headers: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct InspectNetworkEntryParams {
    request_id: Option<String>,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    status: Option<u32>,
    include_headers: Option<bool>,
    include_request_body: Option<bool>,
    include_response_body: Option<bool>,
    max_body_bytes: Option<usize>,
    decode_json: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct AddNetworkMockParams {
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    action: Option<String>,
    status: Option<u32>,
    headers: Option<Value>,
    body: Option<String>,
    error_reason: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct RemoveNetworkMockParams {
    mock_id: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct SetNetworkConditionsParams {
    offline: Option<bool>,
    latency_ms: Option<f64>,
    download_kbps: Option<f64>,
    upload_kbps: Option<f64>,
    connection_type: Option<String>,
    reset: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct ReplayRequestParams {
    request_id: Option<String>,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    body: Option<String>,
    headers: Option<Value>,
}
#[derive(Deserialize, JsonSchema)]
struct ExportDebugBundleParams {
    state_mode: Option<String>,
    focus_ref: Option<String>,
    since_hash: Option<String>,
    include_screenshot: Option<bool>,
    include_headers: Option<bool>,
    include_html: Option<bool>,
    html_selector: Option<String>,
    console_max_entries: Option<usize>,
    network_max_entries: Option<usize>,
    network_status_filter: Option<String>,
}
#[derive(Deserialize, JsonSchema)]
struct WaitForDownloadParams {
    expected_name: Option<String>,
    timeout_seconds: Option<f64>,
}
#[derive(Deserialize, JsonSchema)]
struct ClearLogsParams {
    console: Option<bool>,
    network: Option<bool>,
}
#[derive(Deserialize, JsonSchema)]
struct StartTraceParams {
    categories: Option<String>,
}

#[tool_router(router = tool_router_observability, vis = "pub")]
impl BrowserServer {
    #[rmcp::tool(
        description = "Return captured browser console messages (log/warn/error/info). Extended profile."
    )]
    async fn browser_get_console_logs(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetConsoleLogsParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_get_console_logs(&self.state, p.0.level, p.0.max_entries)
                .await,
        )
    }
    #[rmcp::tool(
        description = "Return captured network requests (XHR/Fetch/doc, status, timing, optional headers). Extended profile."
    )]
    async fn browser_get_network_log(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<GetNetworkLogParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_get_network_log(
                &self.state,
                p.0.type_filter,
                p.0.status_filter,
                p.0.max_entries,
                p.0.include_headers,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Inspect one captured network entry, including optional request/response bodies. Extended profile."
    )]
    async fn browser_inspect_network_entry(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<InspectNetworkEntryParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_inspect_network_entry(
                &self.state,
                p.0.request_id,
                p.0.url_substring,
                p.0.url_regex,
                p.0.method,
                p.0.resource_type,
                p.0.status,
                p.0.include_headers,
                p.0.include_request_body,
                p.0.include_response_body,
                p.0.max_body_bytes,
                p.0.decode_json,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Add a URL-matching network mock rule (fulfill/abort) for the active tab. Extended profile."
    )]
    async fn browser_add_network_mock(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<AddNetworkMockParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_add_network_mock(
                &self.state,
                p.0.url_substring,
                p.0.url_regex,
                p.0.method,
                p.0.resource_type,
                p.0.action,
                p.0.status,
                p.0.headers,
                p.0.body,
                p.0.error_reason,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Remove one network mock by mock_id, or all mocks when omitted. Extended profile."
    )]
    async fn browser_remove_network_mock(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<RemoveNetworkMockParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_remove_network_mock(&self.state, p.0.mock_id).await,
        )
    }
    #[rmcp::tool(
        description = "List active network mock rules and their match counts. Extended profile."
    )]
    async fn browser_list_network_mocks(
        &self,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::observability::browser_list_network_mocks(&self.state).await)
    }
    #[rmcp::tool(
        description = "Apply offline or throttling conditions to the active tab. Extended profile."
    )]
    async fn browser_set_network_conditions(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SetNetworkConditionsParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_set_network_conditions(
                &self.state,
                p.0.offline,
                p.0.latency_ms,
                p.0.download_kbps,
                p.0.upload_kbps,
                p.0.connection_type,
                p.0.reset,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Report active per-tab network conditions. Extended profile.")]
    async fn browser_get_network_conditions(
        &self,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::observability::browser_get_network_conditions(&self.state).await)
    }
    #[rmcp::tool(
        description = "Replay a captured request with optional header/body overrides. Extended profile."
    )]
    async fn browser_replay_request(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ReplayRequestParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_replay_request(
                &self.state,
                p.0.request_id,
                p.0.url_substring,
                p.0.url_regex,
                p.0.method,
                p.0.body,
                p.0.headers,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "Return a compact debug bundle: state + console + network + optional HTML/screenshot. Extended profile."
    )]
    async fn browser_export_debug_bundle(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ExportDebugBundleParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_export_debug_bundle(
                &self.state,
                p.0.state_mode,
                p.0.focus_ref,
                p.0.since_hash,
                p.0.include_screenshot,
                p.0.include_headers,
                p.0.include_html,
                p.0.html_selector,
                p.0.console_max_entries,
                p.0.network_max_entries,
                p.0.network_status_filter,
            )
            .await,
        )
    }
    #[rmcp::tool(
        description = "List files downloaded during the current browser session. Extended profile."
    )]
    async fn browser_get_downloads(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::observability::browser_get_downloads(&self.state).await)
    }
    #[rmcp::tool(
        description = "Wait until a download completes and return the matched file metadata. Extended profile."
    )]
    async fn browser_wait_for_download(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<WaitForDownloadParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_wait_for_download(
                &self.state,
                p.0.expected_name,
                p.0.timeout_seconds,
            )
            .await,
        )
    }
    #[rmcp::tool(description = "Clear console and/or network log buffers. Extended profile.")]
    async fn browser_clear_logs(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ClearLogsParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(
            tools::observability::browser_clear_logs(&self.state, p.0.console, p.0.network).await,
        )
    }
    #[rmcp::tool(description = "Start a CDP performance trace. Extended profile.")]
    async fn browser_start_trace(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<StartTraceParams>,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::observability::browser_start_trace(&self.state, p.0.categories).await)
    }
    #[rmcp::tool(
        description = "Stop the active CDP performance trace and return collected events. Extended profile."
    )]
    async fn browser_stop_trace(&self) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        tools::res(tools::observability::browser_stop_trace(&self.state).await)
    }
}

// ── ServerHandler ─────────────────────────────────────────────────────────────

#[tool_handler(router = self.tool_router)]
impl ServerHandler for BrowserServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_instructions("agentyc browser automation — 61 tools")
    }
}

/// Run the MCP server over stdio.
pub async fn run_stdio(cdp_url: Option<&str>) -> Result<()> {
    let server = BrowserServer::new();
    if let Some(url) = cdp_url {
        server.connect(url).await?;
    }
    let transport = rmcp::transport::stdio();
    let service = server.serve(transport).await?;
    service.waiting().await?;
    Ok(())
}
