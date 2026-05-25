#!/usr/bin/env bash
# This script runs the deterministic MCP/browser-core regression suite.
# Usage:
#   $ ./bin/test.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR/.." || exit 1

exec uv run pytest --numprocesses auto --confcutdir=tests/ci \
	tests/ci/browser/test_cdp_headers.py \
	tests/ci/browser/test_autonomous_agent_workflows.py \
	tests/ci/browser/test_cross_origin_click.py \
	tests/ci/browser/test_new_mcp_tools.py \
	tests/ci/browser/test_proxy.py \
	tests/ci/browser/test_true_cross_origin_click.py \
	tests/ci/infrastructure/test_config.py \
	tests/ci/infrastructure/test_filesystem.py \
	tests/ci/interactions/test_radio_buttons.py \
	tests/ci/security/test_domain_filtering.py \
	tests/ci/security/test_ip_blocking.py \
	tests/ci/security/test_security_flags.py \
	tests/ci/test_cdp_timeout.py \
	tests/ci/test_coordinate_clicking.py \
	tests/ci/test_extension_config.py \
	tests/ci/test_extract_images.py \
	tests/ci/test_file_system_docx.py \
	tests/ci/test_llm_retries.py \
	tests/ci/test_markdown_chunking.py \
	tests/ci/test_markdown_extractor.py \
	tests/ci/test_mcp_runtime_optimizations.py \
	"$@"
