//! agentyc-mcp: host-backed browser automation MCP servers.
//!
//! The direct-CDP compatibility server is available only with the
//! `legacy-cdp` feature. The host-backed adapters are part of the default API.

pub mod host_adapter;
mod host_server;
mod remote_host_server;

pub use host_adapter::HostAdapter;
pub use host_server::{HostBrowserServer, host_service, run_host_stdio};
pub use remote_host_server::{
    RemoteHostBrowserServer, RemoteHostServer, remote_host_service, run_remote_host_stdio,
};

#[cfg(feature = "legacy-cdp")]
mod state;
#[cfg(feature = "legacy-cdp")]
mod tools;

#[cfg(feature = "legacy-cdp")]
mod legacy;

#[cfg(feature = "legacy-cdp")]
pub use legacy::{BrowserServer, run_stdio};

#[cfg(all(test, feature = "legacy-cdp"))]
mod host_adapter_audit;
