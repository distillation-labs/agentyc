from pathlib import Path


def test_distributed_skill_guide_mentions_shared_browser_reuse_and_full_tool_surface():
	guide = Path('agentyc/skills/SKILL.md').read_text(encoding='utf-8')

	assert '--reuse-local-browser' in guide
	assert 'AGENTYC_REUSE_LOCAL_BROWSER' in guide
	assert 'same **browser process and profile**' in guide
	assert 'browser_right_click' in guide
	assert 'browser_set_cookies' in guide
	assert 'browser_close_all' in guide
