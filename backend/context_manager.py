import os
import re
from typing import Any

from backend.llm import generate_summary
from backend.database import (
    compare_and_swap_summary,
    get_summary_state
)


def _configured_recent_message_limit(value: str | None) -> int:
    if value is None:
        return 10
    try:
        limit = int(value)
    except ValueError as error:
        raise ValueError(
            "MAX_RECENT_MESSAGES must be a positive integer."
        ) from error
    if limit < 1:
        raise ValueError("MAX_RECENT_MESSAGES must be a positive integer.")
    return limit


MAX_RECENT_MESSAGES = _configured_recent_message_limit(
    os.getenv("MAX_RECENT_MESSAGES")
)
MAX_SUMMARY_UPDATE_ATTEMPTS = 5

_SUMMARY_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_SUMMARY_STOP_WORDS = {
    "a", "about", "after", "again", "all", "also", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "before", "being", "but",
    "by", "can", "could", "did", "do", "does", "during", "each", "for",
    "from", "had", "has", "have", "he", "her", "here", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "may", "might", "more",
    "most", "must", "my", "no", "not", "of", "on", "or", "our", "she",
    "should", "so", "some", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your"
}


def manage_context(
    history: list[dict[str, Any]],
    max_recent_messages: int | None = None
) -> dict[str, Any]:
    """Separate older messages from recent messages."""
    limit = (
        MAX_RECENT_MESSAGES
        if max_recent_messages is None
        else max_recent_messages
    )
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("max_recent_messages must be a positive integer.")

    return {
        "recent_messages": history[-limit:],
        "older_messages": history[:-limit]
    }


def _meaningful_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _SUMMARY_TOKEN.finditer(text):
        token = match.group(0).casefold()
        if (
            (len(token) > 2 or token.isdigit())
            and token not in _SUMMARY_STOP_WORDS
        ):
            terms.add(token)
    return terms


def _valid_summary(
    updated_summary: str,
    previous_summary: str,
    new_messages: list[dict[str, Any]]
) -> bool:
    if not isinstance(updated_summary, str) or not updated_summary.strip():
        return False

    updated_terms = _meaningful_terms(updated_summary)
    previous_terms = _meaningful_terms(previous_summary)
    if not previous_terms.issubset(updated_terms):
        return False

    new_terms: set[str] = set()
    for message in new_messages:
        new_terms.update(_meaningful_terms(message["content"]))
    new_terms.difference_update(previous_terms)
    return not new_terms or bool(updated_terms.intersection(new_terms))


def summarize_older_messages(
    older_messages: list[dict[str, Any]],
    previous_summary: str = ""
) -> str:
    """Summarise older messages while preserving previous context."""
    if not older_messages and not previous_summary:
        return ""

    conversation_text = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in older_messages
    )

    prompt = f"""
    You are maintaining a conversation memory for Nova AI.

    Previous summary:
    {previous_summary or "No previous summary."}

    Additional older messages:
    {conversation_text or "No additional messages."}

    Create an updated, concise summary. Retain all useful facts from the
    previous summary, including names, numbers, preferences, decisions, goals,
    ongoing tasks, and constraints. Add relevant facts from the new messages.
    Do not invent information or replace specific facts with vague wording.
    """

    return generate_summary(prompt)


def get_managed_context(
    conversation_id: str,
    history: list[dict[str, Any]],
    max_recent_messages: int | None = None
) -> dict[str, Any]:
    """Prepare recent messages and update memory using a checkpoint CAS."""
    context = manage_context(history, max_recent_messages)
    context["summary"] = ""

    if not all("message_id" in message for message in history):
        context["summary"] = get_summary_state(conversation_id)[0]
        return context

    previous_summary, checkpoint = get_summary_state(conversation_id)
    for _ in range(MAX_SUMMARY_UPDATE_ATTEMPTS):
        older_messages = [
            message for message in context["older_messages"]
            if message["message_id"] > checkpoint
        ]
        if not older_messages:
            context["summary"] = previous_summary
            return context

        updated_summary = summarize_older_messages(
            older_messages,
            previous_summary
        )
        if not _valid_summary(
            updated_summary,
            previous_summary,
            older_messages
        ):
            latest_summary, latest_checkpoint = get_summary_state(
                conversation_id
            )
            if (
                latest_summary == previous_summary
                and latest_checkpoint == checkpoint
            ):
                context["summary"] = previous_summary
                return context
            previous_summary, checkpoint = latest_summary, latest_checkpoint
            continue

        new_checkpoint = max(
            message["message_id"] for message in older_messages
        )
        if compare_and_swap_summary(
            conversation_id,
            previous_summary,
            checkpoint,
            updated_summary,
            new_checkpoint
        ):
            context["summary"] = updated_summary
            return context

        previous_summary, checkpoint = get_summary_state(conversation_id)

    raise RuntimeError(
        "Conversation memory changed repeatedly; retry the request."
    )
