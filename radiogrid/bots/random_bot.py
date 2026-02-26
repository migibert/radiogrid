"""A simple random-walking bot for testing and demonstration."""

from __future__ import annotations

import random

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import Action, BotContext, BotOutput
from radiogrid.registry import TeamRegistry


class RandomBot(Bot):
    """Bot that moves in a random cardinal direction each turn.

    Does not scan or communicate — pure random exploration.
    """

    def __init__(self, rng_seed: int | None = None) -> None:
        super().__init__()
        self._rng = random.Random(rng_seed)

    def decide(self, context: BotContext) -> BotOutput:
        action = self._rng.choice(
            [Action.MOVE_UP, Action.MOVE_DOWN, Action.MOVE_LEFT, Action.MOVE_RIGHT]
        )
        return BotOutput(action=action)


@TeamRegistry.register(
    key="random",
    name="Random Walkers",
    description="5 bots that move in random cardinal directions — no scanning, no radio.",
)
class RandomTeam(Team):
    """Team of 5 RandomBots."""

    def __init__(
        self, default_frequency: int = 1, seed: int | None = None
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed

    def initialize(self) -> list[Bot]:
        return [
            RandomBot(rng_seed=(self._seed + i if self._seed is not None else None))
            for i in range(5)
        ]
