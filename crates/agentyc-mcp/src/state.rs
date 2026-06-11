//! Phase 4: state protocol — hashing, min-mode selection, element serialisation.

#![allow(clippy::collapsible_if, clippy::collapsible_match)]

use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashMap;

pub const DEFAULT_MIN_ELEMENTS: usize = 9;
const AUTO_FULL_THRESHOLD: usize = 10;
const MAX_DUPS_PER_SIG: usize = 3;
const MIN_KEEP: usize = 4;
const SCORE_FLOOR: f64 = 0.7;

// ── hashing ──────────────────────────────────────────────────────────────────

/// Compute a 16-hex-char MD5 digest matching Python's `compute_browser_state_hash`.
pub fn compute_state_hash(parts: &[&str]) -> String {
    let key = parts.join("|");
    let digest = md5::compute(key.as_bytes());
    format!("{:x}", digest)[..16].to_string()
}

// ── text helpers ─────────────────────────────────────────────────────────────

pub fn normalize_text(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

pub fn truncate_text(s: &str, max_len: usize) -> String {
    let c: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if c.len() <= max_len {
        c
    } else {
        format!("{}...", c[..max_len.saturating_sub(3)].trim_end())
    }
}

fn normalize_sig_text(s: &str) -> String {
    let re = Regex::new(r"\d+").unwrap();
    re.replace_all(&normalize_text(s), "#").to_string()
}

// ── mode resolution ──────────────────────────────────────────────────────────

pub fn resolve_effective_mode(mode: &str, count: usize) -> &'static str {
    match mode {
        "auto" => {
            if count >= AUTO_FULL_THRESHOLD {
                "min"
            } else {
                "full"
            }
        }
        "focus" => "focus",
        "min" => "min",
        _ => "full",
    }
}

// ── implicit ARIA role omission ───────────────────────────────────────────────

fn implicit_role(tag: &str, input_type: &str) -> Option<&'static str> {
    Some(match (tag, input_type) {
        ("button", _) => "button",
        ("a", _) => "link",
        ("select", _) => "combobox",
        ("textarea", _) => "textbox",
        ("input", "" | "text" | "email" | "password" | "tel" | "url") => "textbox",
        ("input", "number") => "spinbutton",
        ("input", "range") => "slider",
        ("input", "checkbox") => "checkbox",
        ("input", "radio") => "radio",
        ("input", "search") => "searchbox",
        ("img", _) => "img",
        _ => return None,
    })
}

// ── element summary ───────────────────────────────────────────────────────────

/// Compact element descriptor built from live DOM / AX data.
#[derive(Debug, Clone)]
pub struct ElemSummary {
    pub backend_node_id: u64,
    pub tag: String,
    pub text: String,
    pub role: Option<String>,
    pub placeholder: Option<String>,
    pub href: Option<String>,
    pub input_type: Option<String>,
    pub value: Option<String>,
    pub disabled: bool,
    pub score: f64,
    #[allow(dead_code)]
    pub order: usize,
    pub rect_y: Option<f64>,
    pub off_screen: Option<String>,
}

impl ElemSummary {
    pub fn to_json(&self, include_index: bool) -> Value {
        let tag_lc = self.tag.to_lowercase();
        let itype = self.input_type.as_deref().unwrap_or("").to_lowercase();

        let mut obj = json!({
            "ref": format!("e{}", self.backend_node_id),
            "tag": self.tag,
        });
        if include_index {
            obj["index"] = json!(self.backend_node_id);
        }
        let text = truncate_text(&self.text, 100);
        if !text.is_empty() {
            obj["text"] = json!(text);
        }
        if let Some(r) = &self.role {
            if implicit_role(&tag_lc, &itype) != Some(r.as_str()) {
                obj["role"] = json!(r);
            }
        }
        if let Some(p) = &self.placeholder {
            obj["placeholder"] = json!(p);
        }
        if let Some(h) = &self.href {
            obj["href"] = json!(h);
        }
        if let Some(t) = &self.input_type {
            obj["type"] = json!(t);
        }
        if let Some(v) = &self.value {
            let vt = v.trim();
            if !vt.is_empty() {
                obj["value"] = json!(truncate_text(vt, 200));
            }
        }
        if self.disabled {
            obj["disabled"] = json!(true);
        }
        if let Some(side) = &self.off_screen {
            obj["off_screen"] = json!(side);
        }
        obj
    }
}

fn compaction_sig(el: &ElemSummary) -> String {
    let ph = el.placeholder.as_deref().unwrap_or("");
    let sig_text = normalize_sig_text(if !ph.is_empty() { ph } else { &el.text });
    let role = el.role.as_deref().unwrap_or("");
    let itype = el.input_type.as_deref().unwrap_or("");
    format!("{}|{}|{}|{}", normalize_text(&el.tag), normalize_text(role), normalize_text(itype), sig_text)
}

// ── min-mode selection ────────────────────────────────────────────────────────

/// Returns indices into `elements` selected for min mode.
pub fn select_min_elements(
    elements: &[ElemSummary],
    max: usize,
    scroll_y: Option<f64>,
    vh: Option<f64>,
) -> Vec<usize> {
    let sy = scroll_y.unwrap_or(0.0);
    let viewport_h = vh.unwrap_or(900.0);
    let prox_near = sy + viewport_h;
    let prox_far = sy + viewport_h * 2.0;

    let mut sig_counts: HashMap<String, usize> = HashMap::new();
    let mut scored: Vec<(f64, usize)> = elements
        .iter()
        .enumerate()
        .map(|(i, el)| {
            let sig = compaction_sig(el);
            let dup = *sig_counts.get(&sig).unwrap_or(&0);
            sig_counts.insert(sig, dup + 1);

            let mut s = el.score + (16.0 - (i as f64 * 0.35)).max(0.0);
            if dup == 0 {
                s += 4.0;
            }
            s -= dup as f64 * 6.0;
            if dup >= MAX_DUPS_PER_SIG {
                s -= 50.0;
            }
            if let Some(y) = el.rect_y {
                let abs_y = sy + y;
                if abs_y <= prox_near {
                    s += 18.0;
                } else if abs_y <= prox_far {
                    s += 9.0;
                }
            }
            (s, i)
        })
        .collect();

    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    let mut selected: Vec<usize> = Vec::new();
    let mut sel_sigs: HashMap<String, usize> = HashMap::new();
    for (_, i) in &scored {
        if selected.len() >= max {
            break;
        }
        let sig = compaction_sig(&elements[*i]);
        let cnt = *sel_sigs.get(&sig).unwrap_or(&0);
        if cnt >= MAX_DUPS_PER_SIG {
            continue;
        }
        selected.push(*i);
        sel_sigs.insert(sig, cnt + 1);
    }

    // Trim by relative score floor
    if selected.len() > MIN_KEEP && max <= DEFAULT_MIN_ELEMENTS {
        let top = scored.first().map(|(s, _)| *s).unwrap_or(0.0);
        let threshold = top * SCORE_FLOOR;
        let trimmed: Vec<usize> = selected
            .iter()
            .copied()
            .filter(|&i| {
                scored.iter().find(|(_, idx)| *idx == i).map(|(s, _)| *s).unwrap_or(0.0) >= threshold
            })
            .collect();
        if trimmed.len() >= MIN_KEEP {
            selected = trimmed;
        }
    }

    selected.sort_unstable();
    selected
}

// ── state payload builder ─────────────────────────────────────────────────────

pub struct StateBuilder<'a> {
    pub url: &'a str,
    pub title: &'a str,
    pub mode: &'a str,
    pub since_hash: Option<&'a str>,
    pub focus_ref: Option<&'a str>,
    pub max_min: usize,
    pub elements: &'a [ElemSummary],
    pub viewport: Option<(u32, u32)>,
    pub page_size: Option<(u32, u32)>,
    pub scroll: Option<(i64, i64)>,
    pub current_tab_id: Option<String>,
    pub tabs: Vec<Value>,
}

impl<'a> StateBuilder<'a> {
    pub fn build(self) -> Value {
        let count = self.elements.len();
        let eff = resolve_effective_mode(self.mode, count);
        let scroll_y = self.scroll.map(|(_, y)| y as f64);
        let vh = self.viewport.map(|(_, h)| h as f64);

        // Compute hash
        let mut hash_parts: Vec<String> = vec![self.url.to_string(), self.title.to_string()];
        if let Some((vw, vh_)) = self.viewport {
            hash_parts.push(vw.to_string());
            hash_parts.push(vh_.to_string());
        }
        if let Some((_, pw)) = self.page_size {
            // page_width and page_height — not in Python hash, only viewport+scroll+elements
            let _ = pw;
        }
        if let Some((sx, sy)) = self.scroll {
            hash_parts.push(sx.to_string());
            hash_parts.push(sy.to_string());
        }
        for el in self.elements {
            hash_parts.push(el.backend_node_id.to_string());
            hash_parts.push(format!("{}|{}", el.tag, truncate_text(&el.text, 100)));
        }
        let hash_refs: Vec<&str> = hash_parts.iter().map(String::as_str).collect();
        let state_hash = compute_state_hash(&hash_refs);

        let changed = self.since_hash.map(|h| h != state_hash).unwrap_or(true);

        let mut obj = json!({
            "url": self.url,
            "title": self.title,
            "tabs": self.tabs,
            "mode": self.mode,
            "effective_mode": eff,
            "state_hash": state_hash,
            "changed": changed,
            "interactive_element_count": count,
            "interactive_elements": [],
        });
        if let Some(tid) = &self.current_tab_id {
            obj["current_tab_id"] = json!(tid);
        }
        if let Some(fr) = self.focus_ref {
            obj["focus_ref"] = json!(fr);
        }

        if !changed {
            return obj;
        }

        if let Some((vw, vh_)) = self.viewport {
            obj["viewport"] = json!({"width": vw, "height": vh_});
        }
        if let Some((pw, ph)) = self.page_size {
            if let Some((vw, vh_)) = self.viewport {
                if pw > vw || ph > vh_ {
                    obj["page"] = json!({"width": pw, "height": ph});
                }
            }
        }
        if let Some((sx, sy)) = self.scroll {
            if sx != 0 || sy != 0 {
                obj["scroll"] = json!({"x": sx, "y": sy});
            }
        }

        let include_index = eff != "min";
        let selected_elements: Vec<&ElemSummary> = match eff {
            "focus" => {
                // focus_ref → find matching element
                if let Some(fr) = self.focus_ref {
                    let id: u64 = fr.trim_start_matches('e').parse().unwrap_or(0);
                    self.elements.iter().filter(|e| e.backend_node_id == id).collect()
                } else {
                    self.elements.iter().collect()
                }
            }
            "min" => {
                let indices = select_min_elements(self.elements, self.max_min, scroll_y, vh);
                let chosen: Vec<&ElemSummary> = indices.iter().map(|&i| &self.elements[i]).collect();
                if count > chosen.len() {
                    obj["interactive_elements_truncated"] = json!(true);
                    obj["interactive_elements_remaining"] = json!(count - chosen.len());
                    obj["compaction_strategy"] = json!("ranked-min");
                }
                chosen
            }
            _ => self.elements.iter().collect(),
        };

        obj["interactive_elements"] =
            json!(selected_elements.iter().map(|e| e.to_json(include_index)).collect::<Vec<_>>());
        obj
    }
}
