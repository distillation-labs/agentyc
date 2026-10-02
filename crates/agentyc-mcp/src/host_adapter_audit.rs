#[cfg(test)]
mod tests {
    #[test]
    fn host_adapter_has_no_direct_browser_authority_imports_or_handles() {
        let source = include_str!("host_adapter.rs");
        for forbidden in [
            "agentyc_cdp",
            "agentyc-browser",
            "agentyc_browser",
            "agentyc_runtime",
            "BrowserRuntime",
            "chromiumoxide",
            "target_id",
            "session_id",
            "tab_id",
            "debugger_id",
        ] {
            assert!(
                !source.contains(forbidden),
                "host adapter must not contain direct browser authority token {forbidden:?}"
            );
        }
    }
}
