use std::{env, io, path::PathBuf};

use agentyc_host::{Broker, FakeBridge, LocalHostServer, configured_socket_path};

fn option(name: &str) -> Option<PathBuf> {
    let mut arguments = env::args_os().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == name {
            return arguments.next().map(PathBuf::from);
        }
    }
    None
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let state_dir = option("--state-dir").ok_or("--state-dir is required")?;
    let socket_path = option("--socket-path").unwrap_or_else(|| configured_socket_path(&state_dir));
    let broker = Broker::open(&state_dir, FakeBridge::new())?;
    let server = LocalHostServer::start(broker, &socket_path)?;

    println!("READY");
    let mut command = String::new();
    io::stdin().read_line(&mut command)?;
    if command.trim() != "stop" {
        return Err("expected the explicit stop command".into());
    }
    server.stop();
    Ok(())
}
