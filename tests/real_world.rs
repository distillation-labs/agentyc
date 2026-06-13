//! Real-world battle-test suite (generated).
//!
//! This file is intentionally tiny: `build.rs` reads the scenario catalog in
//! `src/scenario.rs` and generates one `#[ignore] #[test]` per scenario into
//! `$OUT_DIR/generated_real_world.rs`, included below.
//!
//! These are heavy, end-to-end browser journeys that share one headless Chrome
//! and one local fixtures server. They are `#[ignore]` by default so the fast
//! suite stays fast. Run the campaign with:
//!
//! ```bash
//! AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test real_world -- --ignored --test-threads=1
//! ```
//!
//! Scenarios skip automatically when no Chrome/Chromium is installed.

include!(concat!(env!("OUT_DIR"), "/generated_real_world.rs"));
