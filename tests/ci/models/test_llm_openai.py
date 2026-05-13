"""Test OpenAI model button click."""

from tests.ci.models.model_test_helper import run_model_button_click_test

from traverse.llm.openai.chat import ChatOpenAI


async def test_openai_gpt_4_1_mini(httpserver):
	"""Test OpenAI gpt-4.1-mini can click a button."""
	await run_model_button_click_test(
		model_class=ChatOpenAI,
		model_name='gpt-4.1-mini',
		api_key_env='OPENAI_API_KEY',
		extra_kwargs={},
		httpserver=httpserver,
	)
