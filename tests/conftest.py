"""Shared test fixtures and helpers for RadioGrid tests."""

from __future__ import annotations

from typing import Callable

import pytest

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.game import Game
from radiogrid.engine.models import Action, BotContext, BotOutput, Message

# ---------------------------------------------------------------------------
# Utility bots for testing
# ---------------------------------------------------------------------------


class RecorderBot(Bot):
    """Records every BotContext it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[BotContext] = []
        self.action_queue: list[Action] = []

    def decide(self, context: BotContext) -> BotOutput:
        self.contexts.append(context)
        action = self.action_queue.pop(0) if self.action_queue else Action.STAY
        return BotOutput(action=action)


class StayBot(Bot):
    """Always returns STAY."""

    def decide(self, context: BotContext) -> BotOutput:
        return BotOutput(action=Action.STAY)


# ---------------------------------------------------------------------------
# Utility teams
# ---------------------------------------------------------------------------


class RecorderTeam(Team):
    """Team of RecorderBots for introspecting game behaviour."""

    def __init__(self, default_frequency: int = 1) -> None:
        super().__init__(default_frequency=default_frequency)
        self.bots: list[RecorderBot] = []

    def initialize(self) -> list[Bot]:
        self.bots = [RecorderBot() for _ in range(5)]
        return self.bots


class StayTeam(Team):
    """Team of StayBots that do nothing."""

    def __init__(self, default_frequency: int = 1) -> None:
        super().__init__(default_frequency=default_frequency)

    def initialize(self) -> list[Bot]:
        return [StayBot() for _ in range(5)]


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_small_game(
    teams: list[Team],
    width: int = 10,
    height: int = 10,
    max_turns: int = 5,
    obstacle_ratio: float = 0.0,
    trap_ratio: float = 0.0,
    seed: int | None = 42,
) -> Game:
    """Create a small, deterministic game useful for unit tests."""
    return Game(
        teams=teams,
        width=width,
        height=height,
        max_turns=max_turns,
        obstacle_ratio=obstacle_ratio,
        trap_ratio=trap_ratio,
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_stay_team() -> Callable[..., StayTeam]:
    """Factory fixture returning StayTeam instances."""

    def _factory(default_frequency: int = 1) -> StayTeam:
        return StayTeam(default_frequency=default_frequency)

    return _factory
