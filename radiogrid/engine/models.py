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

    All fields are set by the sending bot.  The game engine does **not**
    inject or verify any field — ``sender_id`` and ``sender_team_id``
    are provided for convenience but may contain any value the sender
    chooses.  ``None`` means the sender chose not to declare its
    identity.  Teams that need authentication must implement their own
    protocol (e.g. a shared secret embedded in ``content``).
    """

    frequency: int
    content: str
    sender_id: Optional[int] = None
    sender_team_id: Optional[int] = None


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
    Bots do **not** receive their absolute position — they must infer
    it by tracking their own movements (via ``move_succeeded``) and
    scanning for map borders (``OUT_OF_BOUNDS`` tiles).

    ``map_width`` and ``map_height`` are provided so that, once a bot
    detects a border, it can compute its absolute coordinate on that
    axis.

    ``total_explorable_tiles`` is the number of non-obstacle tiles on
    the map.  The goal is to be the first team to visit all of them.
    ``team_explored_count`` tells the bot how many distinct tiles its
    team has explored so far.
    """

    frozen_turns_remaining: int
    move_succeeded: bool = True
    map_width: int = 0
    map_height: int = 0
    inbox: list[Message] = field(default_factory=list)
    scan_result: Optional[ScanResult] = None
    broadcast_frequency: int = 0
    listen_frequency: int = 0
    turn_number: int = 0
    total_explorable_tiles: int = 0
    team_explored_count: int = 0


@dataclass
class BotOutput:
    """Output returned by a bot each turn."""

    action: Action = Action.STAY
    messages: list[Message] = field(default_factory=list)
    new_broadcast_frequency: Optional[int] = None
    new_listen_frequency: Optional[int] = None


@dataclass
class TeamStats:
    """Cumulative per-team telemetry collected by the game engine.

    All counters are updated as a side-effect during turn execution and
    are never exposed to bots.  They are intended for post-game analysis
    and the visualisation UI.

    Attributes:
        messages_sent: Total messages broadcast by this team's bots.
        messages_received_own: Unique messages received from bots of the
            same team.  A single message heard by multiple bots on the
            team counts once.
        messages_received_cross: Unique messages received from bots of
            another team (eavesdropping via shared frequency).  Counted
            once per message per receiving team, not per bot.
        spoofed_messages_sent: Messages where the declared ``sender_team_id``
            is set to another team's valid id — i.e. the bot is actively
            impersonating another team.  Leaving ``sender_team_id`` at
            ``None`` (or setting it to the bot's own team) does NOT count.
        spoofed_messages_delivered: Subset of spoofed messages that were
            actually delivered to an opponent's inbox (the target team
            matches the fake ``sender_team_id``).
        traps_triggered: Number of times a team bot stepped on a trap tile.
        turns_frozen: Total bot-turns spent frozen (across all 5 bots).
        scans_performed: Number of SCAN actions taken.
        moves_attempted: Number of movement actions attempted.
        moves_failed: Movement actions that failed (wall / OOB).
        frequency_changes: Number of broadcast or listen frequency changes.
        idle_turns: Turns where a bot chose STAY while *not* frozen.
        exploration_curve: Team explored-tile count recorded at the end of
            each turn (index 0 = after turn 1).
    """

    messages_sent: int = 0
    messages_received_own: int = 0
    messages_received_cross: int = 0
    spoofed_messages_sent: int = 0
    spoofed_messages_delivered: int = 0
    traps_triggered: int = 0
    turns_frozen: int = 0
    scans_performed: int = 0
    moves_attempted: int = 0
    moves_failed: int = 0
    frequency_changes: int = 0
    idle_turns: int = 0
    exploration_curve: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        return {
            "messages_sent": self.messages_sent,
            "messages_received_own": self.messages_received_own,
            "messages_received_cross": self.messages_received_cross,
            "spoofed_messages_sent": self.spoofed_messages_sent,
            "spoofed_messages_delivered": self.spoofed_messages_delivered,
            "traps_triggered": self.traps_triggered,
            "turns_frozen": self.turns_frozen,
            "scans_performed": self.scans_performed,
            "moves_attempted": self.moves_attempted,
            "moves_failed": self.moves_failed,
            "frequency_changes": self.frequency_changes,
            "idle_turns": self.idle_turns,
            "exploration_curve": self.exploration_curve,
        }

    def snapshot_dict(self) -> dict:
        """Return a snapshot without the exploration curve.

        Used per-turn in the history so the UI can show live stats
        during replay without duplicating the curve in every frame.
        """
        return {
            "messages_sent": self.messages_sent,
            "messages_received_own": self.messages_received_own,
            "messages_received_cross": self.messages_received_cross,
            "spoofed_messages_sent": self.spoofed_messages_sent,
            "spoofed_messages_delivered": self.spoofed_messages_delivered,
            "traps_triggered": self.traps_triggered,
            "turns_frozen": self.turns_frozen,
            "scans_performed": self.scans_performed,
            "moves_attempted": self.moves_attempted,
            "moves_failed": self.moves_failed,
            "frequency_changes": self.frequency_changes,
            "idle_turns": self.idle_turns,
        }
