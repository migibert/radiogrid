"""Abstract interfaces for Bot and Team implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from radiogrid.engine.models import BotContext, BotOutput


class Bot(ABC):
    """Abstract base class for a game bot.

    Subclass this to implement your bot's strategy.
    The game engine assigns `id` and `team_id` after construction.
    """

    def __init__(self) -> None:
        self.id: int = 0
        self.team_id: int = 0

    @abstractmethod
    def decide(self, context: BotContext) -> BotOutput:
        """Decide on an action for this turn.

        Args:
            context: Read-only information about the bot's current state.

        Returns:
            A BotOutput specifying the action, messages, and frequency changes.
        """
        ...


class Team(ABC):
    """Abstract base class for a team of bots.

    Subclass this to define your team's composition and default frequency.
    """

    def __init__(self, default_frequency: int = 1) -> None:
        self.team_id: int = 0
        self.default_frequency: int = default_frequency

    @abstractmethod
    def initialize(self) -> list[Bot]:
        """Create and return exactly 5 bot instances.

        Returns:
            A list of exactly 5 Bot instances.
        """
        ...
