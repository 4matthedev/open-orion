"""Unit tests for the bounded conversation-history ring buffer."""

from orion.context import ContextHistory


def test_messages_preserves_order():
    ctx = ContextHistory(max_turns=10, max_chars=100000)
    ctx.add("user", "a")
    ctx.add("assistant", "b")
    assert [m["role"] for m in ctx.messages()] == ["user", "assistant"]


def test_turn_limit_trims_oldest_pairs():
    ctx = ContextHistory(max_turns=4, max_chars=100000)
    for i in range(6):
        ctx.add("user", f"u{i}")
        ctx.add("assistant", f"a{i}")
    assert len(ctx) == 4
    assert ctx.messages()[0] == {"role": "user", "content": "u4"}
    assert ctx.messages()[1] == {"role": "assistant", "content": "a4"}


def test_char_limit_trims_pairs_not_orphans():
    ctx = ContextHistory(max_turns=100, max_chars=40)
    # 3 pairs of 20 chars each = 60 chars total; only 2 pairs fit.
    for _ in range(3):
        ctx.add("user", "u" * 10)
        ctx.add("assistant", "a" * 10)
    assert len(ctx) == 4
    roles = [m["role"] for m in ctx.messages()]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_clear_empties_history():
    ctx = ContextHistory()
    ctx.add("user", "x")
    ctx.clear()
    assert ctx.messages() == []
