"""An explorer bot that scans first, then moves toward unexplored directions."""

from __future__ import annotations

import random

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (Action, BotContext, BotOutput, Message,
                                     TileType)
from radiogrid.registry import TeamRegistry


class ExplorerBot(Bot):
    """Bot that alternates between scanning and moving toward empty tiles.

    Maintains a local memory of known tiles. Shares discovered tile info
    with teammates via radio messages.
    """

    def __init__(self, rng_seed: int | None = None) -> None:
        super().__init__()
        self._rng = random.Random(rng_seed)
        self._known_tiles: dict[tuple[int, int], TileType] = {}
        self._last_action: Action = Action.STAY

    def decide(self, context: BotContext) -> BotOutput:
        messages_out: list[Message] = []

        # Process scan results and update local map
        if context.scan_result is not None:
            px, py = context.position
            for (dx, dy), tile_info in context.scan_result.tiles.items():
                abs_pos = (px + dx, py + dy)
                self._known_tiles[abs_pos] = tile_info.tile_type

            # Share discoveries with team
            scan_summary = self._encode_scan(context)
            if scan_summary:
                messages_out.append(
                    Message(
                        frequency=context.broadcast_frequency,
                        content=scan_summary,
                    )
                )

        # Process incoming messages from teammates
        for msg in context.inbox:
            if msg.sender_team_id == self.team_id:
                self._decode_scan(msg.content)

        # Decide action: scan if we haven't recently, otherwise move
        if self._last_action != Action.SCAN:
            self._last_action = Action.SCAN
            return BotOutput(action=Action.SCAN, messages=messages_out)

        # Find best direction to move
        action = self._pick_move(context)
        self._last_action = action

        return BotOutput(action=action, messages=messages_out)

    def _pick_move(self, context: BotContext) -> Action:
        """Choose a movement direction, preferring unexplored tiles."""
        px, py = context.position
        candidates: list[tuple[Action, float]] = []

        moves = [
            (Action.MOVE_UP, (0, -1)),
            (Action.MOVE_DOWN, (0, 1)),
            (Action.MOVE_LEFT, (-1, 0)),
            (Action.MOVE_RIGHT, (1, 0)),
        ]

        for action, (dx, dy) in moves:
            target = (px + dx, py + dy)
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

    def _encode_scan(self, context: BotContext) -> str:
        """Encode scan results as a compact string for broadcasting."""
        if context.scan_result is None:
            return ""
        px, py = context.position
        parts = []
        for (dx, dy), tile_info in context.scan_result.tiles.items():
            ax, ay = px + dx, py + dy
            t = tile_info.tile_type.value[0]  # E, O, T, or S
            parts.append(f"{ax},{ay}:{t}")
        return "|".join(parts)

    def _decode_scan(self, content: str) -> None:
        """Decode a teammate's scan broadcast into local map knowledge."""
        if not content or "|" not in content and ":" not in content:
            return
        type_map = {"E": TileType.EMPTY, "O": TileType.OBSTACLE, "T": TileType.TRAP}
        for part in content.split("|"):
            if ":" not in part:
                continue
            try:
                coords, t = part.split(":")
                x_str, y_str = coords.split(",")
                pos = (int(x_str), int(y_str))
                if t in type_map and pos not in self._known_tiles:
                    self._known_tiles[pos] = type_map[t]
            except (ValueError, IndexError):
                continue


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
