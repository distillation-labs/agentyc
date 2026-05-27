from __future__ import annotations

from typing import Any

from agentyc.utils import get_agentyc_version

REPOSITORY_URL = 'https://github.com/distillation-labs/agentyc'
BUG_REPORT_URL = f'{REPOSITORY_URL}/issues/new?template=2_bug_report.yml'
FEATURE_REQUEST_URL = f'{REPOSITORY_URL}/issues/new?template=3_feature_request.yml'
SECURITY_POLICY_URL = f'{REPOSITORY_URL}/security/policy'


def build_feedback_config(session_id: str) -> dict[str, Any]:
	return {
		'sessionId': session_id,
		'version': get_agentyc_version(),
		'feedbackUrls': {
			'bug': BUG_REPORT_URL,
			'feature': FEATURE_REQUEST_URL,
			'security': SECURITY_POLICY_URL,
		},
	}


__all__ = [
	'BUG_REPORT_URL',
	'FEATURE_REQUEST_URL',
	'REPOSITORY_URL',
	'SECURITY_POLICY_URL',
	'build_feedback_config',
]
