"""Bounded conversation history used to feed context to the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextHistory:
    """Ring buffer of chat turns; trims whole user/assistant pairs."""

    max_turns: int = 20
    max_chars: int = 12000
    _turns: list[dict] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self._turns.append({"role": role, "content": content})
        self._trim()

    def messages(self) -> list[dict]:
        return [dict(turn) for turn in self._turns]

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)

    def _trim(self) -> None:
        while len(self._turns) > self.max_turns:
            self._drop_front()
        total = sum(len(turn["content"]) for turn in self._turns)
        while len(self._turns) >= 2 and total > self.max_chars:
            self._drop_front()
            total = sum(len(turn["content"]) for turn in self._turns)

    def _drop_front(self) -> None:
        """Drop the oldest complete user/assistant exchange.

        Trimming starts at the oldest ``user`` turn so an assistant reply is
        never orphaned and a user question is never paired with a *different*
        assistant reply.
        """
        for i, turn in enumerate(self._turns):
            if turn["role"] == "user":
                del self._turns[i : min(len(self._turns), i + 2)]
                return
        if self._turns:
            del self._turns[0]
