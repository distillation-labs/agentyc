//! Phase 6: Deterministic extraction routes using scraper (html5ever).
//! Routes: links, link-collections, images, tables, lists, form-fields, key-value.

#![allow(clippy::collapsible_if)]

use scraper::{Html, Selector};
use serde_json::{Value, json};

/// Classify a query string and extract accordingly.
/// Returns `(route_name, data_json, error_json)` — exactly one of data/error is `Some`.
pub fn extract(html: &str, query: &str) -> Result<Value, String> {
    let q = query.to_lowercase();
    if q.contains("table") {
        Ok(extract_tables(html))
    } else if q.contains("link") {
        Ok(extract_links(html))
    } else if q.contains("image") || q.contains("img") {
        Ok(extract_images(html))
    } else if q.contains("form") || q.contains("field") || q.contains("input") {
        Ok(extract_form_fields(html))
    } else if q.contains("list") {
        Ok(extract_lists(html))
    } else if q.contains("key")
        || q.contains("value")
        || q.contains("definition")
        || q.contains("property")
    {
        Ok(extract_key_value(html))
    } else {
        Err(format!(
            "No extraction route matched query {:?}. \
            Supported queries: table rows, links, images, form fields, lists, key-value / definitions. \
            Try rephrasing or use browser_find_elements with a CSS selector.",
            query
        ))
    }
}

fn extract_tables(html: &str) -> Value {
    let document = Html::parse_document(html);
    let table_sel = Selector::parse("table").unwrap();
    let tr_sel = Selector::parse("tr").unwrap();
    let th_sel = Selector::parse("th").unwrap();
    let td_sel = Selector::parse("td").unwrap();

    let tables: Vec<Value> = document
        .select(&table_sel)
        .map(|table| {
            let headers: Vec<String> = table
                .select(&th_sel)
                .map(|th| th.text().collect::<String>().trim().to_string())
                .collect();
            let rows: Vec<Vec<String>> = table
                .select(&tr_sel)
                .map(|tr| {
                    tr.select(&td_sel)
                        .map(|td| td.text().collect::<String>().trim().to_string())
                        .collect()
                })
                .filter(|row: &Vec<String>| !row.is_empty())
                .collect();
            json!({ "headers": headers, "rows": rows })
        })
        .collect();

    json!({
        "route": "tables",
        "data": tables,
        "extraction_metadata": {
            "route": "tables",
            "count": tables.len(),
        }
    })
}

fn extract_links(html: &str) -> Value {
    let document = Html::parse_document(html);
    let a_sel = Selector::parse("a[href]").unwrap();

    let links: Vec<Value> = document
        .select(&a_sel)
        .map(|a| {
            let text = a.text().collect::<String>().trim().to_string();
            let href = a.value().attr("href").unwrap_or("").to_string();
            json!({ "text": text, "href": href })
        })
        .collect();

    json!({
        "route": "links",
        "data": links,
        "extraction_metadata": { "route": "links", "count": links.len() }
    })
}

fn extract_images(html: &str) -> Value {
    let document = Html::parse_document(html);
    let img_sel = Selector::parse("img").unwrap();

    let images: Vec<Value> = document
        .select(&img_sel)
        .map(|img| {
            let v = img.value();
            json!({
                "src": v.attr("src").unwrap_or(""),
                "alt": v.attr("alt").unwrap_or(""),
                "width": v.attr("width"),
                "height": v.attr("height"),
            })
        })
        .collect();

    json!({
        "route": "images",
        "data": images,
        "extraction_metadata": { "route": "images", "count": images.len() }
    })
}

fn extract_form_fields(html: &str) -> Value {
    let document = Html::parse_document(html);
    let input_sel = Selector::parse("input, select, textarea").unwrap();

    let fields: Vec<Value> = document
        .select(&input_sel)
        .map(|el| {
            let v = el.value();
            json!({
                "tag": el.value().name(),
                "name": v.attr("name"),
                "type": v.attr("type"),
                "id": v.attr("id"),
                "placeholder": v.attr("placeholder"),
                "value": v.attr("value"),
                "required": v.attr("required").is_some(),
            })
        })
        .collect();

    json!({
        "route": "form-fields",
        "data": fields,
        "extraction_metadata": { "route": "form-fields", "count": fields.len() }
    })
}

fn extract_lists(html: &str) -> Value {
    let document = Html::parse_document(html);
    let list_sel = Selector::parse("ul, ol").unwrap();
    let li_sel = Selector::parse("li").unwrap();

    let lists: Vec<Value> = document
        .select(&list_sel)
        .map(|list| {
            let items: Vec<String> = list
                .select(&li_sel)
                .map(|li| li.text().collect::<String>().trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            let tag = list.value().name().to_string();
            json!({ "type": tag, "items": items })
        })
        .collect();

    json!({
        "route": "lists",
        "data": lists,
        "extraction_metadata": { "route": "lists", "count": lists.len() }
    })
}

fn extract_key_value(html: &str) -> Value {
    let document = Html::parse_document(html);
    let dl_sel = Selector::parse("dl").unwrap();

    let mut pairs: Vec<Value> = Vec::new();

    // Extract from dl/dt/dd
    for dl in document.select(&dl_sel) {
        let mut dt_text: Option<String> = None;
        for child in dl.children() {
            if let Some(el) = scraper::ElementRef::wrap(child) {
                let name = el.value().name();
                if name == "dt" {
                    dt_text = Some(el.text().collect::<String>().trim().to_string());
                } else if name == "dd" {
                    if let Some(key) = dt_text.take() {
                        let value = el.text().collect::<String>().trim().to_string();
                        pairs.push(json!({ "key": key, "value": value }));
                    }
                }
            }
        }
    }

    // Also extract from common property-panel patterns: label + adjacent value
    if pairs.is_empty() {
        let label_sel = Selector::parse("label").unwrap();
        for label in document.select(&label_sel) {
            let key = label.text().collect::<String>().trim().to_string();
            if !key.is_empty() {
                pairs.push(json!({ "key": key, "value": null }));
            }
        }
    }

    json!({
        "route": "key-value",
        "data": pairs,
        "extraction_metadata": { "route": "key-value", "count": pairs.len() }
    })
}

/// Project extracted data through an output_schema (basic subset matching).
pub fn apply_output_schema(data: &Value, schema: &Value) -> Value {
    // Simple: if schema has "properties", filter the data to those fields
    if let Some(props) = schema.get("properties").and_then(Value::as_object) {
        let keys: Vec<&str> = props.keys().map(String::as_str).collect();
        match data {
            Value::Array(arr) => Value::Array(
                arr.iter()
                    .map(|item| {
                        if let Value::Object(obj) = item {
                            let filtered: serde_json::Map<String, Value> = obj
                                .iter()
                                .filter(|(k, _)| keys.contains(&k.as_str()))
                                .map(|(k, v)| (k.clone(), v.clone()))
                                .collect();
                            Value::Object(filtered)
                        } else {
                            item.clone()
                        }
                    })
                    .collect(),
            ),
            other => other.clone(),
        }
    } else {
        data.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TABLE_HTML: &str = r#"<table><thead><tr><th>Name</th><th>Age</th></tr></thead><tbody><tr><td>Alice</td><td>30</td></tr><tr><td>Bob</td><td>25</td></tr></tbody></table>"#;
    const LINKS_HTML: &str = r#"<a href="/page1">Page 1</a><a href="/page2">Page 2</a>"#;

    #[test]
    fn test_extract_tables() {
        let r = extract(TABLE_HTML, "table rows").unwrap();
        assert_eq!(r["route"], "tables");
        let data = r["data"].as_array().unwrap();
        assert_eq!(data.len(), 1);
        assert_eq!(data[0]["headers"][0], "Name");
        assert_eq!(data[0]["rows"][0][0], "Alice");
    }

    #[test]
    fn test_extract_links() {
        let r = extract(LINKS_HTML, "all links").unwrap();
        assert_eq!(r["route"], "links");
        let data = r["data"].as_array().unwrap();
        assert_eq!(data.len(), 2);
        assert_eq!(data[0]["text"], "Page 1");
        assert_eq!(data[0]["href"], "/page1");
    }

    #[test]
    fn test_unknown_query_returns_error() {
        let r = extract("<div>hello</div>", "something random xyz");
        assert!(r.is_err());
        let msg = r.unwrap_err();
        assert!(msg.contains("No extraction route matched"));
    }

    #[test]
    fn test_extract_form_fields() {
        let html = r#"<input type="text" name="email" placeholder="Email"><input type="password" name="pass">"#;
        let r = extract(html, "form fields").unwrap();
        assert_eq!(r["route"], "form-fields");
        let data = r["data"].as_array().unwrap();
        assert_eq!(data.len(), 2);
        assert_eq!(data[0]["name"], "email");
    }

    #[test]
    fn test_extract_lists() {
        let html = "<ul><li>Item 1</li><li>Item 2</li></ul>";
        let r = extract(html, "list items").unwrap();
        assert_eq!(r["route"], "lists");
        let data = r["data"].as_array().unwrap();
        assert_eq!(data[0]["items"].as_array().unwrap().len(), 2);
    }
}
