//! Explicit client facade for the canonical local agentyc host protocol.
//!
//! This path connects only to an already-running host socket. It never launches
//! or connects to Chrome; the legacy [`crate::BrowserRuntime`] API is unchanged.

use std::{collections::BTreeMap, path::Path};

use agentyc_host::agentyc_core::{HelloEnvelope, HelloOkEnvelope};
use agentyc_host::{HostError, LocalSocketClient};

/// Client for requests to an already-running local agentyc host.
///
/// `LocalSocketClient` serializes access to its blocking Unix socket internally.
/// Callers should avoid invoking this facade on an async executor's current
/// thread for operations that may wait on host work.
pub struct HostClient {
    client: LocalSocketClient,
}

impl std::fmt::Debug for HostClient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("HostClient")
            .field("client", &self.client)
            .finish()
    }
}

impl HostClient {
    /// Connect to the explicitly selected host socket and complete its hello.
    pub fn connect(socket_path: impl AsRef<Path>, hello: HelloEnvelope) -> Result<Self, HostError> {
        Ok(Self {
            client: LocalSocketClient::connect(socket_path, hello)?,
        })
    }

    /// Return metadata for the completed host handshake.
    pub fn hello_ok(&self) -> &HelloOkEnvelope {
        self.client.hello_ok()
    }

    /// Send one logical host request using the canonical local protocol.
    pub fn request(
        &self,
        method: impl Into<String>,
        params: BTreeMap<String, String>,
    ) -> Result<BTreeMap<String, String>, HostError> {
        self.client.request(method, params)
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use agentyc_host::agentyc_core::{
        ClientMetadata, ConnectionNonce, PROTOCOL_VERSION, PrincipalId,
    };
    use agentyc_host::{Broker, FakeBridge, LocalHostServer};

    fn hello(suffix: &str) -> HelloEnvelope {
        HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(suffix).expect("principal id"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("runtime-host-client-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(ConnectionNonce::from_suffix(suffix).expect("nonce")),
                profile_binding_id: None,
            }),
        }
    }

    #[test]
    fn explicit_host_client_connects_and_uses_canonical_requests() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let socket_path = directory.path().join("host.sock");
        let broker =
            Broker::open(directory.path().join("state"), FakeBridge::new()).expect("broker");
        let server = LocalHostServer::start(broker, &socket_path).expect("local host");

        let client = HostClient::connect(server.socket_path(), hello("runtime-client"))
            .expect("connect to host");
        assert_eq!(client.hello_ok().protocol, PROTOCOL_VERSION);

        let result = client
            .request("host.status", BTreeMap::new())
            .expect("host status request");
        assert_eq!(
            result.get("lifecycle").map(String::as_str),
            Some("\"ready\"")
        );

        server.stop();
    }
}
