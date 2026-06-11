//! SessionManager: tracks CDP targets and sessions, handles auto-attach.

use std::{
    collections::HashMap,
    sync::Arc,
};

use anyhow::Result;
use serde::Deserialize;
use serde_json::json;
use tokio::sync::RwLock;
use tracing::debug;

use crate::CdpClient;

/// A target as reported by CDP (`Target.TargetInfo`).
#[derive(Debug, Clone, Deserialize)]
pub struct TargetInfo {
    #[serde(rename = "targetId")]
    pub target_id: String,
    #[serde(rename = "type")]
    pub target_type: String,
    pub url: String,
    pub title: String,
}

/// A flat CDP session attached to a target.
#[derive(Debug, Clone)]
pub struct CdpSession {
    pub target_id: String,
    pub session_id: String,
}

#[derive(Debug, Default)]
struct Inner {
    targets: HashMap<String, TargetInfo>,
    sessions: HashMap<String, CdpSession>,
}

/// Manages CDP targets and sessions.
///
/// After calling `setup()`, the manager:
/// 1. Enables `Target.setAutoAttach` so new pages are automatically attached.
/// 2. Listens for `Target.attachedToTarget` / `Target.detachedFromTarget` events.
/// 3. Exposes accessors for the current target/session map.
#[derive(Clone)]
pub struct SessionManager {
    client: CdpClient,
    inner: Arc<RwLock<Inner>>,
}

impl SessionManager {
    pub fn new(client: CdpClient) -> Self {
        Self {
            client,
            inner: Arc::new(RwLock::new(Inner::default())),
        }
    }

    /// Enable auto-attach and start listening for attach/detach events.
    pub async fn setup(&self) -> Result<()> {
        // Enable flat auto-attach so every new target gets a session immediately.
        self.client
            .send::<serde_json::Value>(
                "Target.setAutoAttach",
                json!({
                    "autoAttach": true,
                    "waitForDebuggerOnStart": false,
                    "flatten": true
                }),
                None,
            )
            .await?;
        debug!("Target.setAutoAttach enabled");

        // Populate initial targets.
        #[derive(Deserialize)]
        struct TargetList {
            #[serde(rename = "targetInfos")]
            target_infos: Vec<TargetInfo>,
        }
        let list: TargetList = self
            .client
            .send("Target.getTargets", json!({}), None)
            .await?;
        {
            let mut g = self.inner.write().await;
            for t in list.target_infos {
                g.targets.insert(t.target_id.clone(), t);
            }
        }

        // Subscribe to attach / detach events and drive the maps.
        self.spawn_event_listener();
        Ok(())
    }

    fn spawn_event_listener(&self) {
        let inner = Arc::clone(&self.inner);
        let client = self.client.clone();

        tokio::spawn(async move {
            let mut attach_rx = client.subscribe("Target.attachedToTarget").await;
            let mut detach_rx = client.subscribe("Target.detachedFromTarget").await;

            loop {
                tokio::select! {
                    Ok(ev) = attach_rx.recv() => {
                        #[derive(Deserialize)]
                        struct AttachEvent {
                            #[serde(rename = "sessionId")]
                            session_id: String,
                            #[serde(rename = "targetInfo")]
                            target_info: TargetInfo,
                        }
                        if let Ok(ev) = serde_json::from_value::<AttachEvent>(ev) {
                            debug!(target_id = %ev.target_info.target_id, session_id = %ev.session_id, "Target attached");
                            let mut g = inner.write().await;
                            let target_id = ev.target_info.target_id.clone();
                            g.targets.insert(target_id.clone(), ev.target_info);
                            g.sessions.insert(ev.session_id.clone(), CdpSession {
                                target_id,
                                session_id: ev.session_id,
                            });
                        }
                    }
                    Ok(ev) = detach_rx.recv() => {
                        #[derive(Deserialize)]
                        struct DetachEvent {
                            #[serde(rename = "sessionId")]
                            session_id: String,
                        }
                        if let Ok(ev) = serde_json::from_value::<DetachEvent>(ev) {
                            debug!(session_id = %ev.session_id, "Target detached");
                            let mut g = inner.write().await;
                            if let Some(s) = g.sessions.remove(&ev.session_id) {
                                g.targets.remove(&s.target_id);
                            }
                        }
                    }
                }
            }
        });
    }

    /// All currently known page targets.
    pub async fn page_targets(&self) -> Vec<TargetInfo> {
        self.inner
            .read()
            .await
            .targets
            .values()
            .filter(|t| t.target_type == "page")
            .cloned()
            .collect()
    }

    /// Look up a target by ID.
    pub async fn get_target(&self, target_id: &str) -> Option<TargetInfo> {
        self.inner.read().await.targets.get(target_id).cloned()
    }

    /// Look up the session for a target.
    pub async fn session_for_target(&self, target_id: &str) -> Option<CdpSession> {
        let g = self.inner.read().await;
        g.sessions.values().find(|s| s.target_id == target_id).cloned()
    }
}
