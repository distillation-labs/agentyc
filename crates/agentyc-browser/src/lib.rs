//! agentyc-browser: Chrome lifecycle management.
//!
//! - `BrowserProfile`: launch configuration
//! - `launch_browser` / `wait_for_cdp`: subprocess + CDP readiness

pub mod launcher;
pub mod profile;
pub mod session;

pub use launcher::{LaunchedBrowser, find_chrome_binary, launch_browser, wait_for_cdp};
pub use profile::{BrowserProfile, OwnedUserDataDir, ProxySettings, ViewportSize};
pub use session::{BrowserSession, PageSession, TabInfo, tab_id_from};
