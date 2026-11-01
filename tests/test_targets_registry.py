"""Reading `--target` and `-P`, and finding the adapter named."""

import pytest

from lakmus.errors import TargetConfigError
from lakmus.results.models import ParameterValue
from lakmus.targets import (
    OpenAITarget,
    TargetReference,
    adapter_names,
    create,
    parse_parameter,
    parse_target,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("openai:gpt-x", TargetReference("openai", "gpt-x"), id="model-name"),
        pytest.param(
            "ollama:mistral-small", TargetReference("ollama", "mistral-small"), id="local-model"
        ),
        pytest.param(
            "openai-compatible:https://host/v1#llama-3",
            TargetReference("openai-compatible", "https://host/v1", "llama-3"),
            id="endpoint-and-model",
        ),
        pytest.param(
            "openai-compatible:https://host/v1",
            TargetReference("openai-compatible", "https://host/v1"),
            id="endpoint-without-a-model",
        ),
        pytest.param(
            "http://localhost:8000/agent",
            TargetReference("http", "http://localhost:8000/agent"),
            id="bare-url-infers-the-adapter",
        ),
        pytest.param(
            'mcp-stdio:"python server.py"',
            TargetReference("mcp-stdio", "python server.py"),
            id="quoted-command-line",
        ),
        pytest.param(
            "  openai:gpt-x  ", TargetReference("openai", "gpt-x"), id="surrounding-space"
        ),
    ],
)
def test_a_target_is_read_as_written(text: str, expected: TargetReference) -> None:
    assert parse_target(text) == expected


def test_the_model_is_taken_out_of_the_endpoint() -> None:
    """An endpoint must not carry a fragment: it would be sent nowhere."""
    reference = parse_target("openai-compatible:https://host/v1#llama-3")

    assert "#" not in reference.identity
    assert reference.model == "llama-3"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("gpt-x", id="no-adapter"),
        pytest.param("openai:", id="adapter-with-nothing-to-run"),
        pytest.param(":gpt-x", id="empty-adapter"),
    ],
)
def test_something_that_is_not_a_target_is_refused_with_the_list(text: str) -> None:
    """Never silently treated as a model name."""
    with pytest.raises(TargetConfigError):
        parse_target(text)


def test_the_refusal_names_the_adapters_that_exist() -> None:
    with pytest.raises(TargetConfigError, match="openai-compatible"):
        parse_target("gpt-x")


@pytest.mark.parametrize(
    ("text", "name", "value"),
    [
        pytest.param("top_p=0.9", "top_p", 0.9, id="float"),
        pytest.param("max_tokens=512", "max_tokens", 512, id="int"),
        pytest.param("stream=false", "stream", False, id="boolean"),
        pytest.param("style=concise", "style", "concise", id="bare-string"),
        pytest.param('style="concise"', "style", "concise", id="quoted-string"),
        pytest.param("stop=", "stop", "", id="empty-value"),
    ],
)
def test_a_parameter_keeps_the_type_it_was_written_with(
    text: str, name: str, value: ParameterValue
) -> None:
    """Otherwise everything arrives as a string and a temperature of 0 is the text '0'."""
    assert parse_parameter(text) == (name, value)


@pytest.mark.parametrize("text", ["nope", "=0.9", "   "])
def test_something_that_is_not_a_parameter_is_refused(text: str) -> None:
    with pytest.raises(TargetConfigError, match="not a parameter"):
        parse_parameter(text)


def test_a_parameter_that_is_not_a_scalar_is_refused() -> None:
    """A fingerprint holds scalars, and a parameter outside it would not reach the digest."""
    with pytest.raises(TargetConfigError, match="string, number or boolean"):
        parse_parameter('stop=["\\n\\n"]')


def test_the_built_in_adapters_are_all_there() -> None:
    assert adapter_names() == ["anthropic", "ollama", "openai", "openai-compatible"]


def test_an_adapter_is_built_by_name() -> None:
    assert isinstance(create("openai"), OpenAITarget)


def test_an_adapter_this_version_does_not_have_is_refused_with_the_list() -> None:
    """Parsing accepts the syntax; building is what knows what exists."""
    with pytest.raises(TargetConfigError, match="no adapter named 'http'"):
        create(parse_target("http://localhost:8000/agent").adapter)


def test_every_adapter_satisfies_the_protocol() -> None:
    for name in adapter_names():
        target = create(name)
        assert isinstance(target.adapter, str)
        assert target.kind in {"model", "agent", "mcp"}
        assert callable(target.setup)
        assert callable(target.invoke)
        assert callable(target.teardown)
        assert callable(target.fingerprint)


@pytest.mark.parametrize("name", ["api_key", "authorization", "secret", "access_token"])
def test_a_credential_cannot_be_passed_as_a_parameter(name: str) -> None:
    """It would reach every request body and the published fingerprint."""
    with pytest.raises(TargetConfigError, match=r"through the environment"):
        parse_parameter(f"{name}=sk-live-1")


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_a_parameter_that_is_not_a_finite_number_is_refused(raw: str) -> None:
    """A run file has no way to write one, and the refusal belongs here, not in a validator."""
    with pytest.raises(TargetConfigError, match="not a finite number"):
        parse_parameter(f"temperature={raw}")


@pytest.mark.parametrize(
    "text",
    [
        "HTTPS://host/v1#llama-3",
        "openai-compatible:HTTPS://host/v1#llama-3",
        "HTTP://localhost:8000/agent",
    ],
)
def test_an_endpoint_written_in_upper_case_is_not_taken_for_a_model(text: str) -> None:
    """The silent fallback the decision record rules out: it would fail later with 'no endpoint'."""
    reference = parse_target(text)

    assert reference.identity.lower().startswith(("http://", "https://"))
