from __future__ import annotations

from .agent import Policy, PolicyError, PolicyRequest
from .models import AgentAction, Usage


class ReferencePolicy:
    name = "reference"

    def __init__(self, actions: tuple[AgentAction, ...]) -> None:
        self.actions = actions
        self.index = 0

    def next_turn(self, request: PolicyRequest) -> tuple[AgentAction, Usage]:
        del request
        if self.index >= len(self.actions):
            raise PolicyError("reference plan exhausted without finish")
        action = self.actions[self.index]
        self.index += 1
        return action, Usage()

    def cost(self, usage: Usage) -> float:
        del usage
        return 0.0


__all__ = ["Policy", "PolicyError", "PolicyRequest", "ReferencePolicy"]
