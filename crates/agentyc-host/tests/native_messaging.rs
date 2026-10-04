//! Phase 3 Native Messaging topology and forwarding gates.

#[cfg(unix)]
mod unix_tests {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;
    use std::time::Duration;

    use agentyc_host::{
        NativeForwardServer, NativeHostError, NativeMessagingConfig, native_forward_socket_path,
    };
    use tempfile::tempdir_in;

    #[test]
    fn duplicate_shims_forward_bytes_to_the_single_broker_owner() {
        let directory = tempdir_in("/tmp").expect("state directory");
        let server = NativeForwardServer::start(directory.path()).expect("owner endpoint");
        let mut client = UnixStream::connect(server.socket_path()).expect("shim connection");
        client
            .write_all(b"bounded-native-stream")
            .expect("send bytes");
        let mut owner_stream = server
            .accept_forwarded(Duration::from_secs(1))
            .expect("owner receives shim");
        let mut bytes = [0_u8; 21];
        owner_stream.read_exact(&mut bytes).expect("read bytes");
        assert_eq!(&bytes, b"bounded-native-stream");
        server.stop();
        assert!(!native_forward_socket_path(directory.path()).exists());
    }

    #[test]
    fn origin_configuration_is_exact_and_never_accepts_a_wildcard() {
        let config =
            NativeMessagingConfig::new("chrome-extension://jgbllikljnllangilfgkhncepiockppj/")
                .expect("trusted origin");
        assert_eq!(
            config.expected_origin,
            "chrome-extension://jgbllikljnllangilfgkhncepiockppj"
        );
        assert!(matches!(
            NativeMessagingConfig::new("chrome-extension://*"),
            Err(NativeHostError::OriginInvalid)
        ));
    }
}
