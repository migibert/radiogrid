"""Abstract interfaces for Bot and Team implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from radiogrid.engine.models import BotContext, BotOutput, TileType


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

    Discovery scoring
    -----------------
    At the end of every turn the engine calls :meth:`get_discovered_tiles`
    to obtain the team's current map knowledge.  Each correctly reported
    tile earns **+1 point**; each *incorrect* report incurs a **-1
    penalty**.  Tiles that are omitted (unknown) carry no penalty.
    The score is floored at 0 (never negative).

    Physical visits do **not** contribute to the score automatically.
    It is up to the team to collect discoveries from its bots and
    consolidate them into the map returned by ``get_discovered_tiles``.
    A team that does not implement this method scores **0**.

    The engine never reveals whether reported tiles are correct or not,
    so teams must rely on their own confidence in the data.
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

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        """Report tiles this team believes it has discovered.

        Return a mapping of ``(x, y)`` positions (absolute coordinates)
        to :class:`TileType`.  Only include tiles you are confident
        about — each correct entry earns **+1** but each wrong entry
        costs **-1**.  Omitting a tile is always safe (no penalty).

        The default implementation returns an empty dict, resulting in
        a score of **0**.  Teams **must** override this to score.

        Returns:
            Mapping from tile position to the believed tile type.
        """
        return {}
