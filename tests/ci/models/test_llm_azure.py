"""Test Azure OpenAI model button click."""

from tests.ci.models.model_test_helper import run_model_button_click_test

from traverse.llm.azure.chat import ChatAzureOpenAI


async def test_azure_gpt_4_1_mini(httpserver):
	"""Test Azure OpenAI gpt-4.1-mini can click a button."""
	await run_model_button_click_test(
		model_class=ChatAzureOpenAI,
		model_name='gpt-4.1-mini',
		api_key_env='AZURE_OPENAI_KEY',
		extra_kwargs={},  # Azure endpoint will be added by helper
		httpserver=httpserver,
	)
