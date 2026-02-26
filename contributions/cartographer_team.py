"""Collaborative Cartographer Team — coordinated frontier exploration.

Each bot maintains a shared map built from its own scans and teammate
radio broadcasts.  Every turn the bots share their positions and
discoveries so that each one can pick an unexplored *frontier* tile
that is as far as possible from its peers, maximising the area the
team covers.

Strategy highlights:
  • Shared knowledge — scan results are compressed and broadcast.
  • Position awareness — bots know where teammates are each turn.
  • Frontier targeting — move toward the nearest cluster of unknown
    tiles that no teammate is already heading for.
  • BFS pathfinding — efficient obstacle-aware navigation.
  • Trap avoidance — known traps are treated as costly detours.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Optional

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (Action, BotContext, BotOutput, Message,
                                     TileType)
from radiogrid.registry import TeamRegistry

# ── movement helpers ─────────────────────────────────────────────────
_MOVES: list[tuple[Action, tuple[int, int]]] = [
    (Action.MOVE_UP, (0, -1)),
    (Action.MOVE_DOWN, (0, 1)),
    (Action.MOVE_LEFT, (-1, 0)),
    (Action.MOVE_RIGHT, (1, 0)),
]

_TILE_CHAR = {
    TileType.EMPTY: "E",
    TileType.OBSTACLE: "O",
    TileType.TRAP: "T",
    TileType.SPAWN: "S",
}
_CHAR_TILE = {v: k for k, v in _TILE_CHAR.items()}


# ── helpers ──────────────────────────────────────────────────────────
def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _action_for_step(
    src: tuple[int, int], dst: tuple[int, int]
) -> Action:
    """Return the single-step action to move from *src* to *dst*."""
    dx = dst[0] - src[0]
    dy = dst[1] - src[1]
    for action, (adx, ady) in _MOVES:
        if adx == dx and ady == dy:
            return action
    return Action.STAY


# ── CartographerBot ──────────────────────────────────────────────────
class CartographerBot(Bot):
    """A bot that collaboratively maps the grid with its teammates.

    The bot cycles between scanning and moving.  It shares everything
    it learns with its team and steers toward the frontier of the
    unknown that is farthest from its peers.
    """

    def __init__(self, bot_index: int, rng_seed: int | None = None) -> None:
        super().__init__()
        self._index = bot_index          # 0-4, used for scan staggering
        self._rng = random.Random(rng_seed)

        # Collaborative map: (x, y) → TileType
        self._known: dict[tuple[int, int], TileType] = {}
        # Tiles visited by *this* bot (for "am I making progress?" check)
        self._visited: set[tuple[int, int]] = set()
        # Teammate positions (bot_id → (x, y)), updated via radio
        self._peer_positions: dict[int, tuple[int, int]] = {}
        # Current navigation target
        self._target: Optional[tuple[int, int]] = None
        # Path cache (list of tiles to walk through)
        self._path: list[tuple[int, int]] = []
        # Turn counter for scan cadence
        self._turns_since_scan: int = 100  # force an early scan
        # Stuck detection
        self._last_positions: list[tuple[int, int]] = []

    # ── main entry point ─────────────────────────────────────────────
    def decide(self, context: BotContext) -> BotOutput:
        pos = context.position
        self._visited.add(pos)
        self._known.setdefault(pos, TileType.EMPTY)

        # Track recent positions for stuck detection
        self._last_positions.append(pos)
        if len(self._last_positions) > 8:
            self._last_positions.pop(0)

        # ── absorb information ───────────────────────────────────────
        self._process_scan(context)
        self._process_inbox(context)

        # ── prepare outgoing messages ────────────────────────────────
        messages = self._build_messages(context)

        # ── decide action ────────────────────────────────────────────
        # Frozen? Just scan if possible (scanning is free while frozen).
        if context.frozen_turns_remaining > 0:
            return BotOutput(action=Action.SCAN, messages=messages)

        # Should we scan this turn?
        if self._should_scan(context):
            self._turns_since_scan = 0
            return BotOutput(action=Action.SCAN, messages=messages)

        self._turns_since_scan += 1

        # Pick a movement action toward our exploration target.
        action = self._navigate(context)
        return BotOutput(action=action, messages=messages)

    # ── information gathering ────────────────────────────────────────
    def _process_scan(self, context: BotContext) -> None:
        if context.scan_result is None:
            return
        px, py = context.position
        for (dx, dy), tile_info in context.scan_result.tiles.items():
            self._known[(px + dx, py + dy)] = tile_info.tile_type

    def _process_inbox(self, context: BotContext) -> None:
        for msg in context.inbox:
            if msg.sender_team_id != self.team_id:
                continue  # ignore enemy chatter
            self._decode_message(msg.content)

    def _decode_message(self, content: str) -> None:
        """Parse position (P) and scan (S) messages from teammates."""
        for segment in content.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            tag = segment[0]
            body = segment[2:] if len(segment) > 2 else ""
            if tag == "P":
                # P<id>:<x>,<y>
                try:
                    bid_str, coords = body.split(":")
                    xs, ys = coords.split(",")
                    self._peer_positions[int(bid_str)] = (int(xs), int(ys))
                except (ValueError, IndexError):
                    pass
            elif tag == "S":
                # S<x>,<y>:<c>|<x>,<y>:<c>|...
                for part in body.split("|"):
                    if ":" not in part:
                        continue
                    try:
                        coords, c = part.split(":")
                        xs, ys = coords.split(",")
                        p = (int(xs), int(ys))
                        if c in _CHAR_TILE and p not in self._known:
                            self._known[p] = _CHAR_TILE[c]
                    except (ValueError, IndexError):
                        pass

    # ── message building ─────────────────────────────────────────────
    def _build_messages(self, context: BotContext) -> list[Message]:
        freq = context.broadcast_frequency
        msgs: list[Message] = []

        # Always broadcast own position.
        pos_str = f"P{self.id}:{context.position[0]},{context.position[1]}"

        # Compact scan data if we just received scan results.
        scan_str = ""
        if context.scan_result is not None:
            px, py = context.position
            parts: list[str] = []
            for (dx, dy), ti in context.scan_result.tiles.items():
                c = _TILE_CHAR.get(ti.tile_type, "E")
                parts.append(f"{px+dx},{py+dy}:{c}")
            scan_str = "S" + "|".join(parts)

        # Combine into ≤ 256-char messages (max 3 messages).
        combined = pos_str
        if scan_str:
            candidate = combined + ";" + scan_str
            if len(candidate) <= 256:
                combined = candidate
            else:
                # Send scan separately if too long.
                msgs.append(Message(frequency=freq, content=scan_str[:256]))
        msgs.insert(0, Message(frequency=freq, content=combined[:256]))
        return msgs[:3]

    # ── scan cadence ─────────────────────────────────────────────────
    def _should_scan(self, context: BotContext) -> bool:
        """Decide whether to spend this turn scanning."""
        # Stagger initial scans so not everyone scans on the same turn.
        if context.turn_number <= 2:
            return (context.turn_number % 2) == (self._index % 2)
        # Scan every 3 turns normally.
        if self._turns_since_scan >= 3:
            return True
        # Scan if we have very little knowledge around us.
        px, py = context.position
        unknown_neighbors = sum(
            1 for _, (dx, dy) in _MOVES
            if (px + dx, py + dy) not in self._known
        )
        if unknown_neighbors >= 3:
            return True
        return False

    # ── navigation ───────────────────────────────────────────────────
    def _navigate(self, context: BotContext) -> Action:
        pos = context.position

        # If we're stuck (oscillating), clear target and pick a new one.
        if self._is_stuck():
            self._target = None
            self._path = []

        # If current target reached or invalid, pick a new one.
        if self._target is not None:
            if pos == self._target or self._target in self._known and \
               self._known[self._target] in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                self._target = None
                self._path = []

        if self._target is None:
            self._target = self._pick_frontier_target(pos)
            self._path = []

        if self._target is None:
            # No frontier — explore randomly.
            return self._random_passable_move(pos)

        # Follow cached path or recompute.
        if self._path and self._path[0] == pos:
            self._path.pop(0)
        if not self._path or self._path[0] != self._next_step_toward(pos):
            self._path = self._bfs_path(pos, self._target)

        if self._path:
            nxt = self._path[0]
            action = _action_for_step(pos, nxt)
            if action != Action.STAY:
                return action

        # Fallback
        return self._random_passable_move(pos)

    def _next_step_toward(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        if self._path:
            return self._path[0]
        return None

    # ── frontier selection ───────────────────────────────────────────
    def _pick_frontier_target(self, pos: tuple[int, int]) -> Optional[tuple[int, int]]:
        """Pick the best frontier tile: unknown tiles adjacent to known passable ones.

        Among frontier tiles, prefer those that are:
          1. Far from any teammate (to spread out).
          2. Close to us (reachable quickly).
        We combine these with a scoring function.
        """
        frontier: list[tuple[int, int]] = []
        for (kx, ky), tile in self._known.items():
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            for _, (dx, dy) in _MOVES:
                nb = (kx + dx, ky + dy)
                if nb not in self._known:
                    frontier.append(nb)

        if not frontier:
            # No frontier — all reachable tiles are known.  Pick an
            # unvisited-by-us known passable tile instead.
            unvisited = [
                p for p, t in self._known.items()
                if t not in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS, TileType.TRAP)
                and p not in self._visited
            ]
            if unvisited:
                self._rng.shuffle(unvisited)
                return unvisited[0]
            return None

        # De-duplicate.
        frontier = list(set(frontier))

        # Gather peer positions (exclude self).
        peers = [
            p for bid, p in self._peer_positions.items() if bid != self.id
        ]

        def score(tile: tuple[int, int]) -> float:
            dist_to_me = _manhattan(pos, tile)
            if peers:
                min_peer_dist = min(_manhattan(tile, p) for p in peers)
            else:
                min_peer_dist = 0
            # We want HIGH peer distance (spread out) and LOW self distance
            # (reachable quickly).  Balance with weights.
            return min_peer_dist * 2.0 - dist_to_me * 1.0

        # Score all frontiers, pick the best handful, then choose one
        # randomly among the top candidates to add some variety.
        frontier.sort(key=score, reverse=True)
        top_n = max(1, len(frontier) // 5)
        candidates = frontier[:top_n]
        return self._rng.choice(candidates)

    # ── BFS pathfinding ──────────────────────────────────────────────
    def _bfs_path(
        self, start: tuple[int, int], goal: tuple[int, int], max_depth: int = 60
    ) -> list[tuple[int, int]]:
        """BFS shortest path avoiding obstacles and traps where possible."""
        if start == goal:
            return []

        queue: deque[tuple[tuple[int, int], list[tuple[int, int]]]] = deque()
        queue.append((start, []))
        visited: set[tuple[int, int]] = {start}

        while queue:
            cur, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            for _, (dx, dy) in _MOVES:
                nb = (cur[0] + dx, cur[1] + dy)
                if nb in visited:
                    continue
                tile = self._known.get(nb)
                # Unknown tiles are assumed passable (optimistic).
                if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                    continue
                # Avoid traps unless they're on the direct path to the goal.
                if tile == TileType.TRAP and nb != goal:
                    continue
                visited.add(nb)
                new_path = path + [nb]
                if nb == goal:
                    return new_path
                queue.append((nb, new_path))

        return []

    # ── stuck detection ──────────────────────────────────────────────
    def _is_stuck(self) -> bool:
        if len(self._last_positions) < 6:
            return False
        recent = self._last_positions[-6:]
        return len(set(recent)) <= 2

    # ── fallback random move ─────────────────────────────────────────
    def _random_passable_move(self, pos: tuple[int, int]) -> Action:
        options: list[Action] = []
        for action, (dx, dy) in _MOVES:
            nb = (pos[0] + dx, pos[1] + dy)
            tile = self._known.get(nb)
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            if tile == TileType.TRAP:
                continue
            options.append(action)
        if options:
            return self._rng.choice(options)
        # Even traps are better than staying still.
        all_moves = [
            a for a, (dx, dy) in _MOVES
            if self._known.get((pos[0]+dx, pos[1]+dy))
            not in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS)
        ]
        return self._rng.choice(all_moves) if all_moves else Action.STAY


# ── Team registration ────────────────────────────────────────────────
@TeamRegistry.register(
    key="cartographers",
    name="Cartographers",
    description=(
        "Collaborative frontier exploration — bots share a map via radio "
        "and each targets the unexplored area farthest from its peers."
    ),
    author="Copilot",
)
class CartographerTeam(Team):
    """A team of 5 CartographerBots with a shared radio frequency."""

    def __init__(
        self, default_frequency: int = 42, seed: int | None = None
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed

    def initialize(self) -> list[Bot]:
        return [
            CartographerBot(
                bot_index=i,
                rng_seed=(self._seed + i if self._seed is not None else None),
            )
            for i in range(5)
        ]
