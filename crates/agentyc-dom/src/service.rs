//! DOM capture service — calls CDP DOMSnapshot + Accessibility to build
//! an `EnhancedDOMTreeNode` tree for a given session/target.

use std::collections::HashMap;

use anyhow::{Context, Result};
use serde_json::Value;
use tracing::debug;

use crate::node::{AxNode, DomRect, EnhancedDOMTreeNode, NodeType, SnapshotData};

/// Required computed styles for visibility detection.
const REQUIRED_COMPUTED_STYLES: &[&str] = &[
    "display",
    "visibility",
    "opacity",
    "overflow",
    "overflow-x",
    "overflow-y",
    "cursor",
];

/// Thin façade around a CdpClient reference for DOM capture.
/// The caller must supply an `agentyc_cdp::CdpClient` and a target/session id.
pub struct DomService {
    pub cdp: agentyc_cdp::CdpClient,
}

impl DomService {
    pub fn new(cdp: agentyc_cdp::CdpClient) -> Self {
        Self { cdp }
    }

    /// Capture the full DOM tree for `session_id` (page-level session).
    ///
    /// Returns the root `EnhancedDOMTreeNode`.
    pub async fn capture(
        &self,
        session_id: Option<&str>,
        target_id: &str,
    ) -> Result<EnhancedDOMTreeNode> {
        // Fire DOM snapshot and AX tree in parallel.
        let (snapshot_val, dom_val, ax_val) = tokio::try_join!(
            self.cdp.send::<Value>(
                "DOMSnapshot.captureSnapshot",
                serde_json::json!({
                    "computedStyles": REQUIRED_COMPUTED_STYLES,
                    "includePaintOrder": true,
                    "includeDOMRects": true,
                    "includeBlendedBackgroundColors": false,
                    "includeTextColorOpacities": false,
                }),
                session_id,
            ),
            self.cdp.send::<Value>(
                "DOM.getDocument",
                serde_json::json!({ "depth": -1, "pierce": true }),
                session_id,
            ),
            self.cdp.send::<Value>(
                "Accessibility.getFullAXTree",
                serde_json::json!({}),
                session_id,
            ),
        )?;

        let snapshot_lookup = build_snapshot_lookup(&snapshot_val);
        let ax_lookup = build_ax_lookup(&ax_val);

        let root_node = dom_val
            .get("root")
            .context("DOM.getDocument missing 'root'")?;

        let tree = build_node(
            root_node,
            &snapshot_lookup,
            &ax_lookup,
            target_id,
            session_id,
        );
        Ok(tree)
    }
}

// ---------------------------------------------------------------------------
// Snapshot lookup helpers
// ---------------------------------------------------------------------------

fn build_snapshot_lookup(snapshot: &Value) -> HashMap<u64, SnapshotData> {
    let mut map = HashMap::new();

    let docs = match snapshot.get("documents").and_then(Value::as_array) {
        Some(d) => d,
        None => return map,
    };

    for doc in docs {
        let nodes = match doc.get("nodes") {
            Some(n) => n,
            None => continue,
        };

        // The snapshot uses parallel arrays.
        let backend_ids = int_arr(nodes, "backendNodeId");
        let bounds_arr = arr_of_quads(doc, "nodes", "boundingBox");
        let layout = doc.get("layout");
        let paint_orders = layout
            .and_then(|l| l.get("paintOrders"))
            .and_then(Value::as_array);
        let computed_styles_index = nodes.get("currentStyle").and_then(Value::as_array);
        let style_strings = doc.get("strings").and_then(Value::as_array);
        let computed_style_decls = doc.get("computedStyles").and_then(Value::as_array);

        for (i, &bid) in backend_ids.iter().enumerate() {
            let bounds = bounds_arr.get(i).cloned().flatten();
            let paint_order = paint_orders
                .and_then(|po| po.get(i))
                .and_then(Value::as_i64)
                .unwrap_or(0);

            let mut computed = HashMap::new();
            // Resolve computed styles from parallel string index arrays
            if let (Some(style_idx_arr), Some(strings), Some(style_decls)) =
                (computed_styles_index, style_strings, computed_style_decls)
            {
                if let Some(node_style_indices) = style_idx_arr.get(i).and_then(Value::as_array) {
                    for (si, idx_val) in node_style_indices.iter().enumerate() {
                        if let Some(str_idx) = idx_val.as_u64()
                            && let Some(val_str) =
                                strings.get(str_idx as usize).and_then(Value::as_str)
                        {
                            // style name from computedStyles declaration at position si
                            if let Some(prop_name) = REQUIRED_COMPUTED_STYLES.get(si) {
                                computed.insert(prop_name.to_string(), val_str.to_string());
                            }
                        }
                    }
                }
                // Also resolve cursor from named styles
                for decl in style_decls {
                    if let (Some(name), Some(val)) = (
                        decl.get("name").and_then(Value::as_str),
                        decl.get("value").and_then(Value::as_str),
                    ) && name == "cursor"
                    {
                        computed
                            .entry("cursor".to_string())
                            .or_insert_with(|| val.to_string());
                    }
                }
            }

            let cursor_style = computed.get("cursor").cloned();

            map.insert(
                bid,
                SnapshotData {
                    bounds,
                    computed_styles: computed,
                    paint_order,
                    cursor_style,
                },
            );
        }
    }

    map
}

/// Extract parallel `backendNodeId` int array from snapshot nodes object.
fn int_arr(nodes: &Value, key: &str) -> Vec<u64> {
    nodes
        .get(key)
        .and_then(Value::as_array)
        .map(|a| a.iter().map(|v| v.as_u64().unwrap_or(0)).collect())
        .unwrap_or_default()
}

/// Extract bounds quads: snapshot stores them as flat [x,y,w,h] arrays.
fn arr_of_quads(doc: &Value, obj_key: &str, arr_key: &str) -> Vec<Option<DomRect>> {
    let arr = match doc
        .get(obj_key)
        .and_then(|o| o.get(arr_key))
        .and_then(Value::as_array)
    {
        Some(a) => a,
        None => return vec![],
    };

    arr.iter()
        .map(|v| {
            let a = v.as_array()?;
            Some(DomRect {
                x: a.first()?.as_f64()?,
                y: a.get(1)?.as_f64()?,
                width: a.get(2)?.as_f64()?,
                height: a.get(3)?.as_f64()?,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// AX tree lookup
// ---------------------------------------------------------------------------

fn build_ax_lookup(ax: &Value) -> HashMap<u64, AxNode> {
    let mut map = HashMap::new();
    let nodes = match ax.get("nodes").and_then(Value::as_array) {
        Some(n) => n,
        None => return map,
    };

    for node in nodes {
        let bid = match node.get("backendDOMNodeId").and_then(Value::as_u64) {
            Some(b) => b,
            None => continue,
        };

        let role = node
            .get("role")
            .and_then(|r| r.get("value"))
            .and_then(Value::as_str)
            .map(str::to_string);

        let name = node
            .get("name")
            .and_then(|n| n.get("value"))
            .and_then(Value::as_str)
            .map(str::to_string);

        let description = node
            .get("description")
            .and_then(|d| d.get("value"))
            .and_then(Value::as_str)
            .map(str::to_string);

        let value = node
            .get("value")
            .and_then(|v| v.get("value"))
            .and_then(Value::as_str)
            .map(str::to_string);

        let mut properties = HashMap::new();
        if let Some(props) = node.get("properties").and_then(Value::as_array) {
            for prop in props {
                if let (Some(k), Some(v)) = (
                    prop.get("name").and_then(Value::as_str),
                    prop.get("value").and_then(|pv| pv.get("value")),
                ) {
                    properties.insert(k.to_string(), v.clone());
                }
            }
        }

        map.insert(
            bid,
            AxNode {
                role,
                name,
                description,
                value,
                properties,
            },
        );
    }

    map
}

// ---------------------------------------------------------------------------
// Recursive DOM node construction
// ---------------------------------------------------------------------------

fn build_node(
    raw: &Value,
    snapshot_lookup: &HashMap<u64, SnapshotData>,
    ax_lookup: &HashMap<u64, AxNode>,
    target_id: &str,
    session_id: Option<&str>,
) -> EnhancedDOMTreeNode {
    let node_id = raw.get("nodeId").and_then(Value::as_u64).unwrap_or(0);
    let backend_node_id = raw
        .get("backendNodeId")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let node_type_int = raw.get("nodeType").and_then(Value::as_u64).unwrap_or(0);
    let node_name = raw
        .get("nodeName")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_lowercase();
    let node_value = raw
        .get("nodeValue")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let frame_id = raw
        .get("frameId")
        .and_then(Value::as_str)
        .map(str::to_string);
    let is_scrollable = raw.get("isScrollable").and_then(Value::as_bool);
    let shadow_root_type = raw
        .get("shadowRootType")
        .and_then(Value::as_str)
        .map(str::to_string);

    // Attributes: CDP provides them as a flat [key, value, key, value, ...] array.
    let mut attributes = HashMap::new();
    if let Some(attrs) = raw.get("attributes").and_then(Value::as_array) {
        let mut it = attrs.iter();
        while let (Some(k), Some(v)) = (it.next(), it.next()) {
            if let (Some(k), Some(v)) = (k.as_str(), v.as_str()) {
                attributes.insert(k.to_string(), v.to_string());
            }
        }
    }

    let snapshot = snapshot_lookup.get(&backend_node_id).cloned();
    let ax_node = ax_lookup.get(&backend_node_id).cloned();

    let absolute_position = snapshot.as_ref().and_then(|s| s.bounds);

    // Determine basic visibility from computed styles
    let is_visible = snapshot.as_ref().map(|s| {
        let display = s
            .computed_styles
            .get("display")
            .map(|s| s.as_str())
            .unwrap_or("");
        let visibility = s
            .computed_styles
            .get("visibility")
            .map(|s| s.as_str())
            .unwrap_or("");
        let opacity_str = s
            .computed_styles
            .get("opacity")
            .map(|s| s.as_str())
            .unwrap_or("1");
        let opacity: f64 = opacity_str.parse().unwrap_or(1.0);
        display != "none" && visibility != "hidden" && opacity > 0.0
    });

    debug!(backend_node_id, tag = %node_name, "building node");

    // Children
    let children = raw
        .get("children")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .map(|c| build_node(c, snapshot_lookup, ax_lookup, target_id, session_id))
                .collect()
        })
        .unwrap_or_default();

    // Shadow roots
    let shadow_roots = raw
        .get("shadowRoots")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .map(|c| build_node(c, snapshot_lookup, ax_lookup, target_id, session_id))
                .collect()
        })
        .unwrap_or_default();

    // Content document (iframes)
    let content_document = raw
        .get("contentDocument")
        .filter(|v| !v.is_null())
        .map(|c| {
            Box::new(build_node(
                c,
                snapshot_lookup,
                ax_lookup,
                target_id,
                session_id,
            ))
        });

    EnhancedDOMTreeNode {
        node_id,
        backend_node_id,
        node_type: NodeType::from_int(node_type_int),
        node_name,
        node_value,
        attributes,
        is_visible,
        is_scrollable,
        frame_id,
        session_id: session_id.map(str::to_string),
        target_id: target_id.to_string(),
        shadow_root_type,
        ax_node,
        snapshot,
        has_js_click_listener: false, // set after JS listener detection
        absolute_position,
        children,
        shadow_roots,
        content_document,
    }
}
