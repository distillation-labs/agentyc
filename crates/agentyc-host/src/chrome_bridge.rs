//! Compatibility name for the transport-neutral Chrome bridge boundary.
//!
//! The implementation lives in [`crate::bridge`] so Phase 3 and later extension
//! adapters share one host-owned trait. This module keeps the planned
//! `chrome_bridge.rs` surface stable without creating a second authority.

pub use crate::bridge::*;
