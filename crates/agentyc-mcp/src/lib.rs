//! agentyc-mcp: host-backed browser task-space MCP servers.

pub mod host_adapter;
mod host_server;
mod remote_host_server;

pub use host_adapter::HostAdapter;
pub use host_server::{HostBrowserServer, host_service, run_host_stdio};
pub use remote_host_server::{
    RemoteHostBrowserServer, RemoteHostServer, remote_host_service, run_remote_host_stdio,
};

#[cfg(test)]
mod host_adapter_audit;
