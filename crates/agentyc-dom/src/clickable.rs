//! Clickable/interactive element detection.

use crate::node::{EnhancedDOMTreeNode, NodeType};

/// Heuristics for deciding whether an element is interactive.
pub fn is_interactive(node: &EnhancedDOMTreeNode) -> bool {
    if node.node_type != NodeType::ElementNode {
        return false;
    }
    let tag = node.node_name.to_lowercase();
    if tag == "html" || tag == "body" {
        return false;
    }

    // JS click listener detected via CDP
    if node.has_js_click_listener {
        return true;
    }

    // Large iframes
    if (tag == "iframe" || tag == "frame")
        && let Some(s) = &node.snapshot
        && let Some(b) = s.bounds
        && b.width > 100.0
        && b.height > 100.0
    {
        return true;
    }

    // Label wrapper logic
    if tag == "label" {
        if node.attributes.contains_key("for") {
            return false;
        }
        if has_form_control_descendant(node, 2) {
            return true;
        }
    }

    // Span wrapping form control
    if tag == "span" && has_form_control_descendant(node, 2) {
        return true;
    }

    // Search indicators in class/id/data attrs
    if has_search_indicator(node) {
        return true;
    }

    // AX property signals
    if let Some(ax) = &node.ax_node {
        for (k, v) in &ax.properties {
            match k.as_str() {
                "disabled" | "hidden" if v.as_bool() == Some(true) => return false,
                "focusable" | "editable" | "settable" if v.as_bool() == Some(true) => return true,
                "checked" | "expanded" | "pressed" | "selected" | "required" | "autocomplete"
                | "keyshortcuts"
                    if v != &serde_json::Value::Null =>
                {
                    return true;
                }
                _ => {}
            }
        }
    }

    // Core interactive tags
    const INTERACTIVE_TAGS: &[&str] = &[
        "button", "input", "select", "textarea", "a", "details", "summary", "option", "optgroup",
    ];
    if INTERACTIVE_TAGS.contains(&tag.as_str()) {
        return true;
    }

    // Event handler or tabindex attributes
    const EVENT_ATTRS: &[&str] = &[
        "onclick",
        "onmousedown",
        "onmouseup",
        "onkeydown",
        "onkeyup",
        "tabindex",
    ];
    for attr in EVENT_ATTRS {
        if node.attributes.contains_key(*attr) {
            return true;
        }
    }

    // ARIA role
    if let Some(role) = node.attributes.get("role")
        && is_interactive_role(role)
    {
        return true;
    }

    // AX role
    if let Some(ax) = &node.ax_node
        && let Some(role) = &ax.role
        && is_interactive_role(role)
    {
        return true;
    }

    // Icon-sized element with interactive attributes
    if let Some(s) = &node.snapshot
        && let Some(b) = s.bounds
        && (10.0..=50.0).contains(&b.width)
        && (10.0..=50.0).contains(&b.height)
    {
        const ICON_ATTRS: &[&str] = &["class", "role", "onclick", "data-action", "aria-label"];
        if ICON_ATTRS.iter().any(|a| node.attributes.contains_key(*a)) {
            return true;
        }
    }

    // Cursor pointer style
    if let Some(s) = &node.snapshot
        && s.cursor_style.as_deref() == Some("pointer")
    {
        return true;
    }

    false
}

fn has_form_control_descendant(node: &EnhancedDOMTreeNode, max_depth: u32) -> bool {
    if max_depth == 0 {
        return false;
    }
    let children_iter = node.children.iter().chain(node.shadow_roots.iter());
    for child in children_iter {
        if child.node_type != NodeType::ElementNode {
            continue;
        }
        let t = child.node_name.to_lowercase();
        if matches!(t.as_str(), "input" | "select" | "textarea") {
            return true;
        }
        if has_form_control_descendant(child, max_depth - 1) {
            return true;
        }
    }
    false
}

const SEARCH_INDICATORS: &[&str] = &[
    "search",
    "magnify",
    "glass",
    "lookup",
    "find",
    "query",
    "search-icon",
    "search-btn",
    "search-button",
    "searchbox",
];

fn has_search_indicator(node: &EnhancedDOMTreeNode) -> bool {
    if let Some(cls) = node.attributes.get("class") {
        let lower = cls.to_lowercase();
        if SEARCH_INDICATORS.iter().any(|s| lower.contains(s)) {
            return true;
        }
    }
    if let Some(id) = node.attributes.get("id") {
        let lower = id.to_lowercase();
        if SEARCH_INDICATORS.iter().any(|s| lower.contains(s)) {
            return true;
        }
    }
    for (k, v) in &node.attributes {
        if k.starts_with("data-") {
            let lower = v.to_lowercase();
            if SEARCH_INDICATORS.iter().any(|s| lower.contains(s)) {
                return true;
            }
        }
    }
    false
}

fn is_interactive_role(role: &str) -> bool {
    const ROLES: &[&str] = &[
        "button",
        "link",
        "menuitem",
        "option",
        "radio",
        "checkbox",
        "tab",
        "textbox",
        "combobox",
        "slider",
        "spinbutton",
        "search",
        "searchbox",
        "row",
        "cell",
        "gridcell",
        "listbox",
    ];
    ROLES.contains(&role)
}

/// Returns true if the node should be considered a search entry control.
pub fn is_search_entry_control(node: &EnhancedDOMTreeNode) -> bool {
    if node.node_type != NodeType::ElementNode {
        return false;
    }
    let tag = node.node_name.to_lowercase();
    if tag != "input" && tag != "textarea" {
        return false;
    }
    let input_type = node.attributes.get("type").map(|s| s.to_lowercase());
    if input_type.as_deref() == Some("search") {
        return true;
    }
    let role = node.attributes.get("role").map(|s| s.to_lowercase());
    if role.as_deref() == Some("searchbox") {
        return true;
    }
    if let Some(ax) = &node.ax_node
        && ax.role.as_deref().map(str::to_lowercase).as_deref() == Some("searchbox")
    {
        return true;
    }
    false
}
