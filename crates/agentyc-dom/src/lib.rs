//! agentyc-dom: DOM pipeline — capture, enrichment, clickable detection, markdown extraction.

pub mod clickable;
pub mod markdown;
pub mod node;
pub mod service;

pub use clickable::{is_interactive, is_search_entry_control};
pub use markdown::{MarkdownChunk, chunk_markdown, html_to_markdown};
pub use node::{AxNode, DomRect, EnhancedDOMTreeNode, NodeType, SnapshotData};
pub use service::DomService;
