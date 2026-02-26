"""Data models for the RadioGrid game engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TileType(Enum):
    """Types of tiles on the game map."""

    EMPTY = "EMPTY"
    OBSTACLE = "OBSTACLE"
    TRAP = "TRAP"
    SPAWN = "SPAWN"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"


class Action(Enum):
    """Actions a bot can take each turn."""

    MOVE_UP = "MOVE_UP"
    MOVE_DOWN = "MOVE_DOWN"
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    SCAN = "SCAN"
    STAY = "STAY"


# Direction vectors for movement actions
DIRECTION_VECTORS: dict[Action, tuple[int, int]] = {
    Action.MOVE_UP: (0, -1),
    Action.MOVE_DOWN: (0, 1),
    Action.MOVE_LEFT: (-1, 0),
    Action.MOVE_RIGHT: (1, 0),
}


@dataclass(frozen=True)
class Message:
    """A radio message sent between bots.

    When sending: bot sets frequency and content only.
    When receiving: sender_id and sender_team_id are set by the engine.
    """

    frequency: int
    content: str
    sender_id: int = 0
    sender_team_id: int = 0


@dataclass(frozen=True)
class BotInfo:
    """Public information about a bot, as seen in scan results."""

    id: int
    team_id: int
    broadcast_frequency: int
    listen_frequency: int
    frozen_turns_remaining: int


@dataclass(frozen=True)
class TileInfo:
    """Information about a single tile from a scan result."""

    tile_type: TileType
    bots: list[BotInfo] = field(default_factory=list)


@dataclass(frozen=True)
class ScanResult:
    """Result of a SCAN action, containing the 8 surrounding tiles.

    Keys are (dx, dy) offsets where dx, dy each in {-1, 0, +1},
    excluding (0, 0) which is the bot's own tile.
    """

    tiles: dict[tuple[int, int], TileInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class BotContext:
    """Read-only context provided to a bot each turn.

    This is the only information a bot receives about the game state.
    """

    position: tuple[int, int]
    frozen_turns_remaining: int
    inbox: list[Message] = field(default_factory=list)
    scan_result: Optional[ScanResult] = None
    broadcast_frequency: int = 0
    listen_frequency: int = 0
    turn_number: int = 0


@dataclass
class BotOutput:
    """Output returned by a bot each turn."""

    action: Action = Action.STAY
    messages: list[Message] = field(default_factory=list)
    new_broadcast_frequency: Optional[int] = None
    new_listen_frequency: Optional[int] = None
