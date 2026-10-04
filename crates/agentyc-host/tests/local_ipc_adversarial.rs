#![cfg(unix)]

use std::{
    fs,
    io::{Read, Write},
    net::Shutdown,
    os::unix::net::{UnixListener, UnixStream},
    path::{Path, PathBuf},
    time::Duration,
};

use agentyc_core::{
    ClientMetadata, ConnectionNonce, DEFAULT_MAX_FRAME_PAYLOAD_BYTES, HelloEnvelope,
    PROTOCOL_VERSION, PrincipalId, encode_frame,
};
use agentyc_host::{Broker, FakeBridge, HostError, LocalHostServer, LocalSocketClient};
use tempfile::{TempDir, tempdir};

fn start_server() -> (TempDir, LocalHostServer, PathBuf) {
    let directory = tempdir().expect("temporary directory");
    let socket_path = directory.path().join("host.sock");
    let broker = Broker::open(directory.path().join("state"), FakeBridge::new()).expect("broker");
    let server = LocalHostServer::start(broker, &socket_path).expect("local host server");
    (directory, server, socket_path)
}

fn assert_peer_closed(mut stream: UnixStream) {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .expect("read timeout");
    let mut byte = [0_u8; 1];
    match stream.read(&mut byte) {
        Ok(0) => {}
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::UnexpectedEof
                    | std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::BrokenPipe
            ) => {}
        other => panic!("malformed client was not closed: {other:?}"),
    }
}

fn send_raw_frame(stream: &mut UnixStream, payload: &[u8]) {
    let frame = encode_frame(payload, DEFAULT_MAX_FRAME_PAYLOAD_BYTES).expect("bounded frame");
    stream.write_all(&frame).expect("send frame");
}

fn client_hello(suffix: &str) -> HelloEnvelope {
    HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: PrincipalId::from_suffix(suffix).expect("principal"),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: None,
            client_name: Some("local-eof-test".to_owned()),
            client_version: Some("1".to_owned()),
            connection_nonce: Some(ConnectionNonce::from_suffix(suffix).expect("nonce")),
            profile_binding_id: None,
        }),
    }
}

#[test]
fn local_client_distinguishes_clean_eof_from_truncated_frame() {
    let directory = tempdir().expect("temporary directory");
    let clean_path = directory.path().join("c");
    let clean_listener = UnixListener::bind(&clean_path).expect("clean listener");
    let clean_thread = std::thread::spawn(move || {
        let (mut stream, _) = clean_listener.accept().expect("clean accept");
        let mut input = [0_u8; 16];
        let _ = stream.read(&mut input);
        stream.shutdown(Shutdown::Write).expect("clean shutdown");
    });
    let clean = LocalSocketClient::connect(&clean_path, client_hello("clean-eof"));
    assert!(
        matches!(clean, Err(HostError::Transport(error)) if error.kind() == std::io::ErrorKind::UnexpectedEof)
    );
    clean_thread.join().expect("clean thread");

    let truncated_path = directory.path().join("t");
    let truncated_listener = UnixListener::bind(&truncated_path).expect("truncated listener");
    let truncated_thread = std::thread::spawn(move || {
        let (mut stream, _) = truncated_listener.accept().expect("truncated accept");
        let mut input = [0_u8; 16];
        let _ = stream.read(&mut input);
        stream
            .write_all(&5_u32.to_be_bytes())
            .expect("truncated prefix");
        stream.write_all(b"{}").expect("truncated payload");
        stream
            .shutdown(Shutdown::Write)
            .expect("truncated shutdown");
    });
    let truncated = LocalSocketClient::connect(&truncated_path, client_hello("truncated-eof"));
    assert!(
        matches!(truncated, Err(HostError::Frame(error)) if error.code() == agentyc_core::ErrorCode::TruncatedFrame)
    );
    truncated_thread.join().expect("truncated thread");
}

#[test]
fn invalid_utf8_is_rejected_before_json_dispatch() {
    let (_directory, server, socket_path) = start_server();
    let mut client = UnixStream::connect(&socket_path).expect("connect");
    send_raw_frame(&mut client, &[0xff, 0xfe]);
    assert_peer_closed(client);
    server.stop();
}

#[test]
fn malformed_json_is_rejected_without_a_response() {
    let (_directory, server, socket_path) = start_server();
    let mut client = UnixStream::connect(&socket_path).expect("connect");
    send_raw_frame(&mut client, b"{");
    assert_peer_closed(client);
    server.stop();
}

#[test]
fn oversized_frame_is_rejected_before_payload_allocation() {
    let (_directory, server, socket_path) = start_server();
    let mut client = UnixStream::connect(&socket_path).expect("connect");
    client
        .write_all(&((DEFAULT_MAX_FRAME_PAYLOAD_BYTES as u32) + 1).to_be_bytes())
        .expect("send oversized length");
    assert_peer_closed(client);
    server.stop();
}

#[test]
fn truncated_frame_is_closed_at_stream_end() {
    let (_directory, server, socket_path) = start_server();
    let mut client = UnixStream::connect(&socket_path).expect("connect");
    client.write_all(&5_u32.to_be_bytes()).expect("send length");
    client.write_all(b"{}").expect("send partial payload");
    client
        .shutdown(std::net::Shutdown::Write)
        .expect("shutdown writer");
    assert_peer_closed(client);
    server.stop();
}

#[test]
fn stopping_does_not_delete_a_replaced_socket_path() {
    let (_directory, server, socket_path) = start_server();
    fs::remove_file(&socket_path).expect("remove original socket inode");
    fs::write(&socket_path, b"replacement-owned-by-test").expect("install replacement");

    server.stop();

    assert_eq!(
        fs::read(&socket_path).expect("replacement remains"),
        b"replacement-owned-by-test"
    );
}

#[test]
fn stale_socket_inode_is_replaced_but_active_socket_is_not() {
    let directory = tempdir().expect("temporary directory");
    let socket_path = directory.path().join("host.sock");
    let stale_listener = UnixListener::bind(&socket_path).expect("stale socket");
    drop(stale_listener);

    let broker = Broker::open(directory.path().join("state"), FakeBridge::new()).expect("broker");
    let server = LocalHostServer::start(broker, &socket_path).expect("replace stale socket");
    assert!(Path::new(&socket_path).exists());

    let duplicate_broker =
        Broker::open(directory.path().join("duplicate-state"), FakeBridge::new())
            .expect("duplicate broker");
    assert!(LocalHostServer::start(duplicate_broker, &socket_path).is_err());

    server.stop();
}
