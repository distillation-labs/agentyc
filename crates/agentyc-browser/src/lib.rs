//! agentyc-browser: Chrome lifecycle management.
//!
//! - `BrowserProfile`: launch configuration
//! - `launch_browser` / `wait_for_cdp`: subprocess + CDP readiness
//! - `BrowserSession`: owned browser process + `CdpClient` + `SessionManager`

pub mod launcher;
pub mod profile;
pub mod session;

pub use launcher::{find_chrome_binary, launch_browser, wait_for_cdp, LaunchedBrowser};
pub use profile::{BrowserProfile, OwnedUserDataDir, ProxySettings, ViewportSize};
pub use session::BrowserSession;
