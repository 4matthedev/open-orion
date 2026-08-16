"""Unit tests for the persistent memory store."""

from orion.core.memory import Memory


def _fresh(tmp_path):
    return Memory(tmp_path / "memory.json")


def test_empty_by_default(tmp_path):
    mem = _fresh(tmp_path)
    assert len(mem) == 0
    assert mem.render() == ""


def test_remember_assigns_incrementing_ids(tmp_path):
    mem = _fresh(tmp_path)
    assert mem.remember("first note") == 1
    assert mem.remember("second note") == 2
    assert [n["text"] for n in mem.items()] == ["first note", "second note"]


def test_remember_ignores_blank(tmp_path):
    mem = _fresh(tmp_path)
    assert mem.remember("   ") == -1
    assert len(mem) == 0


def test_persists_across_instances(tmp_path):
    path = tmp_path / "memory.json"
    mem1 = Memory(path)
    mem1.remember("sticky note")
    mem2 = Memory(path)
    assert mem2.items()[0]["text"] == "sticky note"


def test_forget(tmp_path):
    mem = _fresh(tmp_path)
    mem.remember("a")
    mem.remember("b")
    assert mem.forget(1) is True
    assert len(mem) == 1
    assert mem.forget(999) is False


def test_clear(tmp_path):
    mem = _fresh(tmp_path)
    mem.remember("a")
    mem.clear()
    assert len(mem) == 0


def test_to_prompt_embeds_notes(tmp_path):
    mem = _fresh(tmp_path)
    mem.remember("remember tea: darjeeling")
    prompt = mem.to_prompt()
    assert "Permanent memory" in prompt
    assert "darjeeling" in prompt
