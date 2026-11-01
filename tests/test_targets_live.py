"""One real call per adapter, skipped unless its key is in the environment.

These are the only tests in the suite that leave the machine. `make check` does not run them;
`uv run pytest -m live` does.
"""

import os

import pytest

from lakmus.targets import (
    AnthropicTarget,
    OllamaTarget,
    OpenAITarget,
    RenderedCase,
    Target,
    TargetConfig,
)

CASE = RenderedCase(case_id="live-0001", prompt="Réponds par le seul mot: bonjour")

pytestmark = pytest.mark.live


async def _answer(target: Target, config: TargetConfig) -> str:
    await target.setup(config)
    try:
        output = await target.invoke(CASE)
    finally:
        await target.teardown()
    assert output.error is None, output.error
    assert isinstance(output.content, str)
    assert target.fingerprint().sha256
    return output.content


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY is not set")
async def test_openai_answers() -> None:
    model = os.environ.get("LAKMUS_LIVE_OPENAI_MODEL", "gpt-4o-mini")

    assert await _answer(
        OpenAITarget(), TargetConfig(identity=model, parameters={"temperature": 0.0})
    )


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY is not set")
async def test_anthropic_answers() -> None:
    model = os.environ.get("LAKMUS_LIVE_ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

    assert await _answer(
        AnthropicTarget(), TargetConfig(identity=model, parameters={"max_tokens": 64})
    )


@pytest.mark.skipif(
    not os.environ.get("LAKMUS_LIVE_OLLAMA_MODEL"),
    reason="LAKMUS_LIVE_OLLAMA_MODEL is not set; Ollama has no key to detect",
)
async def test_ollama_answers() -> None:
    model = os.environ["LAKMUS_LIVE_OLLAMA_MODEL"]

    assert await _answer(OllamaTarget(), TargetConfig(identity=model))
