"""Regression test for round-limit exhaustion (spec H11).

When the agentic loop exhausts `MAX_TOOL_ROUNDS`, it falls through to a final
streaming call. The message history at that point still contains `tool_use` /
`tool_result` blocks, so the API *requires* a `tools` definition to be present;
omitting it returns a 400. The fix keeps `tools=TOOL_SCHEMAS` on the final call
but pins `tool_choice={"type": "none"}` so Claude answers in prose instead of
looping further.

This test forces every round to return `tool_use` so the loop exhausts, then
asserts the final `messages.stream(...)` call was made with both `tools` and
`tool_choice={"type": "none"}`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mihomes.services.ai.agent import agent_stream, MAX_TOOL_ROUNDS


class _ToolUseBlock(SimpleNamespace):
    """Stand-in for an anthropic tool_use content block."""
    type = "tool_use"


def _tool_use_response():
    """A response whose stop_reason drives one more tool round."""
    block = _ToolUseBlock(name="list_tasks", id="tu_1", input={})
    return SimpleNamespace(stop_reason="tool_use", content=[block])


class _FakeStream:
    """Context-manager stand-in for client.messages.stream()."""
    def __enter__(self):
        return SimpleNamespace(text_stream=iter(["final ", "answer"]))

    def __exit__(self, *exc):
        return False


def test_final_call_after_round_exhaustion_keeps_tools_with_none_choice():
    fake_client = MagicMock()
    # Every create() call returns tool_use → the loop exhausts MAX_TOOL_ROUNDS.
    fake_client.messages.create.side_effect = [
        _tool_use_response() for _ in range(MAX_TOOL_ROUNDS)
    ]
    fake_client.messages.stream.return_value = _FakeStream()

    with patch("anthropic.Anthropic", return_value=fake_client), \
         patch("mihomes.services.ai.tools.execute_tool", return_value="ok"), \
         patch("mihomes.services.ai.tools.tool_label", return_value="tasks"):
        events = list(agent_stream(
            session=MagicMock(),
            query="how many tasks?",
            system_prompt="sys",
            api_key="test-key",
            model="claude-sonnet-5",
        ))

    # The loop exhausted, so exactly MAX_TOOL_ROUNDS create() calls happened...
    assert fake_client.messages.create.call_count == MAX_TOOL_ROUNDS
    # ...and the final streaming call must carry tools + tool_choice=none.
    assert fake_client.messages.stream.call_count == 1
    _, kwargs = fake_client.messages.stream.call_args
    assert "tools" in kwargs and kwargs["tools"], "final call dropped tools → 400"
    assert kwargs["tool_choice"] == {"type": "none"}

    # And the stream still yields the model's prose answer.
    tokens = [data for kind, data in events if kind == "token"]
    assert "".join(tokens) == "final answer"
