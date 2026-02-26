"""An explorer bot that scans first, then moves toward unexplored directions.

Bots do *not* know their absolute position.  Each bot maintains a local
map in coordinates relative to its spawn point and tracks its own
movement via the ``move_succeeded`` feedback.
"""

from __future__ import annotations

import random

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotOutput, Message, TileType)
from radiogrid.registry import TeamRegistry


class ExplorerBot(Bot):
    """Bot that alternates between scanning and moving toward empty tiles.

    Maintains a local memory of known tiles in *relative* coordinates
    (with the spawn point as origin).  Uses ``move_succeeded`` to keep
    an accurate relative position.
    """

    def __init__(self, rng_seed: int | None = None) -> None:
        super().__init__()
        self._rng = random.Random(rng_seed)
        self._known_tiles: dict[tuple[int, int], TileType] = {}
        self._last_action: Action = Action.STAY
        # Relative position (spawn = origin)
        self._rel_x: int = 0
        self._rel_y: int = 0
        # Pending movement delta — set when a move action is chosen,
        # resolved on the next turn via move_succeeded.
        self._pending_move: tuple[int, int] | None = None

    def decide(self, context: BotContext) -> BotOutput:
        messages_out: list[Message] = []

        # --- Update relative position from last move attempt ---
        if self._pending_move is not None:
            if context.move_succeeded:
                dx, dy = self._pending_move
                self._rel_x += dx
                self._rel_y += dy
            self._pending_move = None

        # --- Process scan results (stored in relative coords) ---
        if context.scan_result is not None:
            for (dx, dy), tile_info in context.scan_result.tiles.items():
                rel_pos = (self._rel_x + dx, self._rel_y + dy)
                self._known_tiles[rel_pos] = tile_info.tile_type

        # --- Decide action: scan if we haven't recently, otherwise move ---
        if self._last_action != Action.SCAN:
            self._last_action = Action.SCAN
            return BotOutput(action=Action.SCAN, messages=messages_out)

        # Find best direction to move
        action = self._pick_move()
        self._last_action = action

        # Track the pending move so we can update position next turn
        if action in DIRECTION_VECTORS:
            self._pending_move = DIRECTION_VECTORS[action]

        return BotOutput(action=action, messages=messages_out)

    def _pick_move(self) -> Action:
        """Choose a movement direction, preferring unexplored tiles."""
        candidates: list[tuple[Action, float]] = []

        moves = [
            (Action.MOVE_UP, (0, -1)),
            (Action.MOVE_DOWN, (0, 1)),
            (Action.MOVE_LEFT, (-1, 0)),
            (Action.MOVE_RIGHT, (1, 0)),
        ]

        for action, (dx, dy) in moves:
            target = (self._rel_x + dx, self._rel_y + dy)
            tile = self._known_tiles.get(target)

            if tile == TileType.OBSTACLE or tile == TileType.OUT_OF_BOUNDS:
                continue  # skip impassable

            if tile == TileType.TRAP:
                candidates.append((action, 0.1))  # low priority
            elif tile is None:
                candidates.append((action, 2.0))  # unknown = high priority
            else:
                candidates.append((action, 1.0))  # known empty

        if not candidates:
            return Action.STAY

        # Weighted random selection
        total = sum(w for _, w in candidates)
        r = self._rng.random() * total
        cumulative = 0.0
        for action, weight in candidates:
            cumulative += weight
            if r <= cumulative:
                return action

        return candidates[-1][0]


@TeamRegistry.register(
    key="explorer",
    name="Explorers",
    description="Scan-and-move strategy with shared map via radio.",
)
class ExplorerTeam(Team):
    """Team of 5 ExplorerBots."""

    def __init__(
        self, default_frequency: int = 100, seed: int | None = None
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed

    def initialize(self) -> list[Bot]:
        return [
            ExplorerBot(
                rng_seed=(self._seed + i if self._seed is not None else None)
            )
            for i in range(5)
        ]
