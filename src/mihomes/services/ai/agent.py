"""Agentic AI loop — multi-round tool calling with live DB access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generator

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment

MAX_TOOL_ROUNDS = 5

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
    import anthropic
    from mihomes.services.ai.tools import TOOL_SCHEMAS, execute_tool, tool_label

    client = anthropic.Anthropic(api_key=api_key)
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
        # Final call without tools so Claude can't loop further
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=full_system,
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                yield ("token", chunk)
    except Exception as e:
        yield ("error", str(e))
