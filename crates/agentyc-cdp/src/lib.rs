//! agentyc-cdp: CDP transport layer
//!
//! Provides `CdpClient` — a WebSocket-based Chrome DevTools Protocol transport
//! with per-request timeouts, message-id correlation, and event subscriptions.

pub mod client;
pub mod session;

pub use client::CdpClient;
pub use session::SessionManager;
