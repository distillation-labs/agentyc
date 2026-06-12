//! Core DOM node types for the agentyc DOM pipeline.

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

/// Maps to the W3C `nodeType` integer values used by CDP.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(u8)]
pub enum NodeType {
    ElementNode = 1,
    TextNode = 3,
    CdataSectionNode = 4,
    ProcessingInstructionNode = 7,
    CommentNode = 8,
    DocumentNode = 9,
    DocumentTypeNode = 10,
    DocumentFragmentNode = 11,
    Unknown = 0,
}

impl NodeType {
    pub fn from_int(v: u64) -> Self {
        match v {
            1 => Self::ElementNode,
            3 => Self::TextNode,
            4 => Self::CdataSectionNode,
            7 => Self::ProcessingInstructionNode,
            8 => Self::CommentNode,
            9 => Self::DocumentNode,
            10 => Self::DocumentTypeNode,
            11 => Self::DocumentFragmentNode,
            _ => Self::Unknown,
        }
    }
}

/// Accessibility node information extracted from the AX tree.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AxNode {
    pub role: Option<String>,
    pub name: Option<String>,
    pub description: Option<String>,
    pub value: Option<String>,
    /// Extra AX properties (checked, expanded, disabled, etc.)
    pub properties: HashMap<String, serde_json::Value>,
}

/// Bounding rectangle from snapshot data.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct DomRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Per-node computed style data extracted from the DOM snapshot.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SnapshotData {
    pub bounds: Option<DomRect>,
    pub computed_styles: HashMap<String, String>,
    pub paint_order: i64,
    pub cursor_style: Option<String>,
}

/// A fully-enriched DOM tree node, combining CDP DOM tree, AX tree, and snapshot data.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnhancedDOMTreeNode {
    pub node_id: u64,
    pub backend_node_id: u64,
    pub node_type: NodeType,
    pub node_name: String,
    pub node_value: String,
    pub attributes: HashMap<String, String>,
    pub is_visible: Option<bool>,
    pub is_scrollable: Option<bool>,
    pub frame_id: Option<String>,
    pub session_id: Option<String>,
    pub target_id: String,
    pub shadow_root_type: Option<String>,
    pub ax_node: Option<AxNode>,
    pub snapshot: Option<SnapshotData>,
    pub has_js_click_listener: bool,
    pub absolute_position: Option<DomRect>,
    pub children: Vec<EnhancedDOMTreeNode>,
    pub shadow_roots: Vec<EnhancedDOMTreeNode>,
    /// Content document for iframes.
    pub content_document: Option<Box<EnhancedDOMTreeNode>>,
}

impl EnhancedDOMTreeNode {
    /// Tag name in lowercase (empty string for non-element nodes).
    pub fn tag_name(&self) -> &str {
        if self.node_type == NodeType::ElementNode {
            &self.node_name
        } else {
            ""
        }
    }

    /// Stable ref used in MCP state payloads: `e` + last 4 hex chars of `backend_node_id`.
    pub fn stable_ref(&self) -> String {
        format!("e{:04x}", self.backend_node_id & 0xFFFF)
    }

    /// Whether this node has any dimension in its snapshot bounds.
    pub fn has_nonzero_size(&self) -> bool {
        self.snapshot
            .as_ref()
            .and_then(|s| s.bounds)
            .map(|b| b.width > 0.0 && b.height > 0.0)
            .unwrap_or(false)
    }

    /// Collect all visible text from this node's subtree.
    pub fn all_text(&self) -> String {
        let mut parts = Vec::new();
        self.collect_text(&mut parts);
        parts.join(" ").trim().to_string()
    }

    fn collect_text(&self, out: &mut Vec<String>) {
        if self.node_type == NodeType::TextNode {
            let v = self.node_value.trim();
            if !v.is_empty() {
                out.push(v.to_string());
            }
        }
        for child in &self.children {
            child.collect_text(out);
        }
        for sr in &self.shadow_roots {
            sr.collect_text(out);
        }
    }
}
