from agentyc.tools.actions.completion import register_done_action
from agentyc.tools.actions.exploration import register_exploration_actions
from agentyc.tools.actions.extraction import register_extraction_actions
from agentyc.tools.actions.file_eval import register_file_and_eval_actions
from agentyc.tools.actions.interactions import register_click_action, register_interaction_actions
from agentyc.tools.actions.navigation import register_navigation_actions
from agentyc.tools.actions.rendering import register_rendering_actions

__all__ = [
	'register_click_action',
	'register_done_action',
	'register_exploration_actions',
	'register_extraction_actions',
	'register_file_and_eval_actions',
	'register_interaction_actions',
	'register_navigation_actions',
	'register_rendering_actions',
]
