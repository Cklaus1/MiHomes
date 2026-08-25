"""Agentic AI loop — multi-round tool calling with live DB access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generator

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment

MAX_TOOL_ROUNDS = 5

_PROVIDER_SYSTEM_SUFFIX = """
Answer directly and specifically using the estate data provided.
Cite actual numbers, names, and dates from the data when relevant.
"""


def provider_stream(
    session: Session,
    query: str,
    *,
    system_prompt: str,
    provider_name: str,
    api_key: str,
    model: str,
    roles: list,
    property_slug: str | None = None,
    attachments: list["Attachment"] | None = None,
) -> Generator[tuple[str, str], None, None]:
    """
    Streaming for non-Claude providers via context injection.
    Yields same (event_type, data) tuples as agent_stream.
    """
    from mihomes.services.ai.context import assemble_context
    from mihomes.services.ai.provider import get_provider

    full_system = system_prompt.rstrip() + "\n" + _PROVIDER_SYSTEM_SUFFIX
    context_data = assemble_context(session, roles, query, property_slug=property_slug)
    provider = get_provider(provider_name, api_key=api_key, model=model, entry_point="web.stream")

    try:
        for chunk in provider.stream(full_system, query, context_data=context_data, attachments=attachments):
            yield ("token", chunk)
    except Exception as e:
        yield ("error", str(e))


_AGENT_SYSTEM_SUFFIX = """
You have access to database tools that let you query the estate's live records.
Use them whenever the user asks about specific data (counts, lists, names, dates, amounts).
Call multiple tools in parallel when you need data from different areas.
After fetching data, answer directly and specifically — cite actual numbers and names.
Do not say "I don't have access to that data" without first trying the relevant tool.
"""


def agent_stream(
    session: Session,
    query: str,
    *,
    system_prompt: str,
    api_key: str,
    model: str,
    property_slug: str | None = None,
    attachments: list[Attachment] | None = None,
) -> Generator[tuple[str, str], None, None]:
    """
    Agentic generator. Yields (event_type, data) tuples:
      ("status", "Checking library records...")  — during tool execution
      ("token",  "Here are the books...")        — final response tokens
      ("error",  "message")                      — on failure
    """
    from mihomes.services.ai.provider import get_provider
    from mihomes.services.ai.tools import TOOL_SCHEMAS, execute_tool, tool_label

    # **The factory, not `anthropic.Anthropic(...)` — SPEC-004 D17/F8.**
    #
    # This line used to construct its own SDK client, which made it the one AI path in the tree
    # that never touched `get_provider()`. Step 10 wraps the factory's return value in
    # `MeteredProvider`, so leaving the bypass here would have produced a green suite and an
    # uncapped bill: every other dispatch metered, and the **highest-token path in the app**
    # — an agentic loop that can make six API calls per question — silently free. That is why
    # Step 9 is ordered before Step 10 rather than folded into it.
    #
    # The client is borrowed from the provider rather than the provider being used directly,
    # because this loop needs the raw `messages.create` / `messages.stream` surface for tool
    # calling, which the `AIProvider` Protocol deliberately does not expose. Borrowing keeps
    # construction — and therefore metering — in one place while leaving the loop unchanged.
    provider = get_provider("claude", api_key=api_key, model=model, entry_point="web.agent")
    client = provider.client
    full_system = system_prompt.rstrip() + "\n" + _AGENT_SYSTEM_SUFFIX

    # Build initial user content
    content: list[dict] | str
    if attachments:
        content = []
        for att in attachments:
            if att.is_image:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": att.media_type, "data": att.base64_data},
                })
                content.append({"type": "text", "text": f"[Image: {att.filename}]"})
            elif att.text_content:
                content.append({
                    "type": "text",
                    "text": f"--- Attached file: {att.filename} ---\n{att.text_content}\n---",
                })
        content.append({"type": "text", "text": query})
    else:
        content = query

    messages: list[dict] = [{"role": "user", "content": content}]

    # ── Tool-call rounds (non-streaming) ──────────────────────────────────────
    did_tool_calls = False

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=full_system,
                messages=messages,
                tools=TOOL_SCHEMAS,
            )
        except Exception as e:
            yield ("error", str(e))
            return

        if resp.stop_reason == "end_turn":
            # Claude answered without needing (more) tools
            final_text = "".join(
                b.text for b in resp.content if hasattr(b, "text") and b.text
            )
            yield ("token", final_text)
            return

        if resp.stop_reason == "tool_use":
            did_tool_calls = True
            tool_calls = [b for b in resp.content if b.type == "tool_use"]
            tool_results = []

            for tc in tool_calls:
                label = tool_label(tc.name)
                yield ("status", f"Checking {label}…")
                result = execute_tool(session, tc.name, dict(tc.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })

            messages.append({"role": "assistant", "content": list(resp.content)})
            messages.append({"role": "user", "content": tool_results})
            # Continue loop to let Claude process results or call more tools
            continue

        # Unexpected stop reason — fall through to final streaming
        break

    # ── Final streaming response after tool calls ─────────────────────────────
    if did_tool_calls:
        yield ("status", "Preparing response…")

    try:
        # Final call: keep `tools` present (the history still contains tool_use /
        # tool_result blocks, which the API requires a tools definition to accept),
        # but pin tool_choice=none so Claude answers in prose instead of looping.
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=full_system,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice={"type": "none"},
        ) as stream:
            for chunk in stream.text_stream:
                yield ("token", chunk)
    except Exception as e:
        yield ("error", str(e))
