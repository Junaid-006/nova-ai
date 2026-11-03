from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from backend import context_manager, database


def _create_conversation():
    user_id = "context-user"
    database.create_user(user_id, user_id, "hashed-password")
    return database.create_conversation(user_id)


def _create_history(conversation_id, count):
    for index in range(count):
        database.save_message(
            conversation_id,
            "user" if index % 2 == 0 else "assistant",
            f"message-{index}"
        )
    return database.get_conversation_history(conversation_id, "context-user")


def test_context_under_limit_does_not_summarize(
    temporary_database, monkeypatch
):
    conversation_id = _create_conversation()
    history = _create_history(conversation_id, 8)
    calls = []
    monkeypatch.setattr(
        context_manager,
        "generate_summary",
        lambda prompt: calls.append(prompt) or "summary"
    )

    context = context_manager.get_managed_context(conversation_id, history)

    assert context["recent_messages"] == history
    assert context["summary"] == ""
    assert calls == []


def test_incremental_summary_checkpoint_survives_reloads(
    temporary_database, monkeypatch
):
    conversation_id = _create_conversation()
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "memory message-0 message-1"
        return "memory message-0 message-1 message-2 message-3"

    monkeypatch.setattr(context_manager, "generate_summary", summarize)
    history = _create_history(conversation_id, 12)
    first = context_manager.get_managed_context(conversation_id, history)

    assert [message["content"] for message in history[:-10]] == [
        "message-0", "message-1"
    ]
    assert first["summary"] == "memory message-0 message-1"
    checkpoint = database.get_summary_checkpoint(conversation_id)
    assert checkpoint == history[1]["message_id"]

    _create_history(conversation_id, 2)
    expanded_history = database.get_conversation_history(
        conversation_id,
        "context-user"
    )
    second = context_manager.get_managed_context(
        conversation_id,
        expanded_history
    )

    assert "memory message-0 message-1" in prompts[1]
    assert "user: message-0" not in prompts[1]
    assert "user: message-1" not in prompts[1]
    assert "message-2" in prompts[1]
    assert "message-3" in prompts[1]
    assert second["summary"] == (
        "memory message-0 message-1 message-2 message-3"
    )
    assert database.get_summary_checkpoint(conversation_id) == (
        expanded_history[3]["message_id"]
    )

    reloaded_history = database.get_conversation_history(
        conversation_id,
        "context-user"
    )
    calls_before = len(prompts)
    context_manager.get_managed_context(conversation_id, reloaded_history)
    assert len(prompts) == calls_before


def test_exactly_ten_messages_remain_recent(temporary_database, monkeypatch):
    conversation_id = _create_conversation()
    history = _create_history(conversation_id, 10)
    monkeypatch.setattr(
        context_manager,
        "generate_summary",
        lambda prompt: (_ for _ in ()).throw(AssertionError(prompt))
    )

    context = context_manager.get_managed_context(conversation_id, history)

    assert context["recent_messages"] == history
    assert database.get_summary_checkpoint(conversation_id) == 0


def test_context_limit_is_configurable_and_validated(monkeypatch):
    assert context_manager._configured_recent_message_limit("3") == 3
    with pytest.raises(ValueError, match="positive integer"):
        context_manager._configured_recent_message_limit("0")
    with pytest.raises(ValueError, match="positive integer"):
        context_manager._configured_recent_message_limit("not-a-number")

    history = [{"message_id": index} for index in range(7)]
    monkeypatch.setattr(context_manager, "MAX_RECENT_MESSAGES", 3)
    context = context_manager.manage_context(history)
    assert context["recent_messages"] == history[-3:]
    assert context["older_messages"] == history[:-3]


def test_invalid_summary_preserves_summary_and_checkpoint(
    temporary_database, monkeypatch
):
    conversation_id = _create_conversation()
    history = _create_history(conversation_id, 12)
    old_checkpoint = history[0]["message_id"]
    assert database.compare_and_swap_summary(
        conversation_id,
        "",
        0,
        "Project Orion NOVA-7429",
        old_checkpoint
    )
    monkeypatch.setattr(
        context_manager,
        "generate_summary",
        lambda prompt: "An unrelated summary."
    )

    context = context_manager.get_managed_context(conversation_id, history)

    assert context["summary"] == "Project Orion NOVA-7429"
    assert database.get_summary_state(conversation_id) == (
        "Project Orion NOVA-7429",
        old_checkpoint
    )


def test_failed_summarisation_does_not_advance_checkpoint(
    temporary_database, monkeypatch
):
    conversation_id = _create_conversation()
    history = _create_history(conversation_id, 12)
    old_summary = "Existing project context."
    old_checkpoint = history[0]["message_id"]
    assert database.compare_and_swap_summary(
        conversation_id,
        "",
        0,
        old_summary,
        old_checkpoint
    )

    def fail_summary(_prompt):
        raise RuntimeError("summary service unavailable")

    monkeypatch.setattr(context_manager, "generate_summary", fail_summary)
    with pytest.raises(RuntimeError, match="summary service unavailable"):
        context_manager.get_managed_context(conversation_id, history)

    assert database.get_summary_state(conversation_id) == (
        old_summary,
        old_checkpoint
    )


def test_compare_and_swap_allows_only_one_writer(
    temporary_database
):
    conversation_id = _create_conversation()
    barrier = Barrier(2)

    def update(summary):
        barrier.wait(timeout=5)
        return database.compare_and_swap_summary(
            conversation_id,
            "",
            0,
            summary,
            12
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, ("summary alpha", "summary bravo")))

    assert sorted(results) == [False, True]
    assert database.get_summary_state(conversation_id) in {
        ("summary alpha", 12),
        ("summary bravo", 12)
    }


def test_concurrent_context_updates_retry_without_losing_context(
    temporary_database, monkeypatch
):
    conversation_id = _create_conversation()
    complete_history = _create_history(conversation_id, 14)
    first_snapshot = complete_history[:12]
    barrier = Barrier(2)
    call_lock = Lock()
    initial_calls = 0

    def summarize(messages, previous_summary=""):
        nonlocal initial_calls
        with call_lock:
            initial_calls += 1
            wait_for_other_request = initial_calls <= 2
        if wait_for_other_request:
            barrier.wait(timeout=5)
        additions = " ".join(message["content"] for message in messages)
        return f"{previous_summary} {additions}".strip()

    monkeypatch.setattr(
        context_manager,
        "summarize_older_messages",
        summarize
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda history: context_manager.get_managed_context(
                conversation_id,
                history
            ),
            (first_snapshot, complete_history)
        ))

    saved_summary, checkpoint = database.get_summary_state(conversation_id)
    assert all(result["summary"] for result in results)
    assert "message-0" in saved_summary
    assert "message-1" in saved_summary
    assert checkpoint == complete_history[3]["message_id"]


def test_summaries_and_checkpoints_are_conversation_scoped(
    temporary_database, monkeypatch
):
    first_conversation = _create_conversation()
    second_conversation = database.create_conversation("context-user")
    first_history = _create_history(first_conversation, 12)
    for index in range(12):
        database.save_message(
            second_conversation,
            "user" if index % 2 == 0 else "assistant",
            f"other-{index}"
        )
    second_history = database.get_conversation_history(
        second_conversation,
        "context-user"
    )

    def summarize(prompt):
        if "other-0" in prompt:
            return "second conversation other-0 other-1"
        if "message-0" in prompt:
            return "first conversation message-0 message-1"
        return "unexpected summary"

    monkeypatch.setattr(context_manager, "generate_summary", summarize)
    context_manager.get_managed_context(first_conversation, first_history)
    context_manager.get_managed_context(second_conversation, second_history)

    assert database.get_summary(first_conversation) == (
        "first conversation message-0 message-1"
    )
    assert database.get_summary(second_conversation) == (
        "second conversation other-0 other-1"
    )
    assert database.get_summary_checkpoint(first_conversation) == (
        first_history[1]["message_id"]
    )
    assert database.get_summary_checkpoint(second_conversation) == (
        second_history[1]["message_id"]
    )
