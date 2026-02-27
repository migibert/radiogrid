"""Collaborative Cartographer Team — coordinated frontier exploration.

Each bot maintains a shared map built from its own scans and teammate
radio broadcasts.  Bots do **not** receive their absolute position;
instead, each one self-localises by scanning for map borders
(``OUT_OF_BOUNDS`` tiles) and using the known map dimensions.

Strategy overview
-----------------
**Phase 1 — Localisation:**
  • The bot navigates in relative coordinates (spawn = origin).
  • It scans for ``OUT_OF_BOUNDS`` to deduce absolute position on each
    axis independently.
  • Until fully localised it explores autonomously.

**Phase 2 — Collaborative exploration (once localised):**
  • Scan results and position are broadcast in *absolute* coordinates.
  • Teammates share knowledge via radio so each bot builds a global
    map without ever being told its position by the engine.
  • Frontier targeting + BFS pathfinding steer bots toward the
    unexplored area farthest from its peers.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Optional

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotOutput, Message, TileType)
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

_TOKEN = "#CRT#"  # shared secret for message authentication


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

        # ── Relative position tracking (spawn = origin) ──────────
        self._rel_x: int = 0
        self._rel_y: int = 0
        self._pending_move: tuple[int, int] | None = None

        # ── Localisation state ───────────────────────────────────
        # Once a border is detected on an axis we can compute the
        # absolute coordinate of the spawn and therefore convert any
        # relative coordinate to absolute.  ``None`` means unknown.
        self._spawn_abs_x: int | None = None
        self._spawn_abs_y: int | None = None

        # Collaborative map: coordinate → TileType.
        # Before localisation, keys are *relative* coordinates.
        # Once an axis is localised, keys on that axis are converted to
        # absolute in bulk and new discoveries are stored absolutely.
        # To keep things simple the map is rebuilt in absolute coords
        # when BOTH axes are localised (see ``_promote_map``).
        self._known: dict[tuple[int, int], TileType] = {}
        self._map_promoted: bool = False  # True once converted to abs

        # Tiles visited by *this* bot (for progress checks)
        self._visited: set[tuple[int, int]] = set()
        # Teammate positions (bot_id → pos), only before promo these are
        # relative coords of THIS bot; after promotion they are absolute.
        self._peer_positions: dict[int, tuple[int, int]] = {}
        # Current navigation target
        self._target: Optional[tuple[int, int]] = None
        self._path: list[tuple[int, int]] = []
        # Turn counter for scan cadence
        self._turns_since_scan: int = 100  # force an early scan
        # Stuck detection
        self._last_positions: list[tuple[int, int]] = []

    # ── coordinate helpers ───────────────────────────────────────
    @property
    def _localized(self) -> bool:
        return self._spawn_abs_x is not None and self._spawn_abs_y is not None

    def _rel_to_abs(self, rx: int, ry: int) -> tuple[int, int] | None:
        """Convert relative → absolute.  Returns *None* if not yet localised."""
        if self._spawn_abs_x is None or self._spawn_abs_y is None:
            return None
        return (self._spawn_abs_x + rx, self._spawn_abs_y + ry)

    @property
    def _pos(self) -> tuple[int, int]:
        """Current position in the coordinate system used by ``_known``."""
        if self._map_promoted:
            ax, ay = self._rel_to_abs(self._rel_x, self._rel_y)  # type: ignore[misc]
            return (ax, ay)
        return (self._rel_x, self._rel_y)

    # ── main entry point ─────────────────────────────────────────
    def decide(self, context: BotContext) -> BotOutput:
        # ── update relative position ─────────────────────────────
        if self._pending_move is not None:
            if context.move_succeeded:
                dx, dy = self._pending_move
                self._rel_x += dx
                self._rel_y += dy
            self._pending_move = None

        pos = self._pos
        self._visited.add(pos)
        self._known.setdefault(pos, TileType.EMPTY)

        # Track recent positions for stuck detection
        self._last_positions.append(pos)
        if len(self._last_positions) > 8:
            self._last_positions.pop(0)

        # ── absorb information ───────────────────────────────────
        self._process_scan(context)
        self._try_localize(context)
        self._process_inbox(context)

        # ── prepare outgoing messages ────────────────────────────
        messages = self._build_messages(context)

        # ── decide action ────────────────────────────────────────
        # Frozen? Just scan.
        if context.frozen_turns_remaining > 0:
            return BotOutput(action=Action.SCAN, messages=messages)

        # Should we scan this turn?
        if self._should_scan(context):
            self._turns_since_scan = 0
            return BotOutput(action=Action.SCAN, messages=messages)

        self._turns_since_scan += 1

        # Pick a movement action.
        action = self._navigate(context)

        # Track pending move
        if action in DIRECTION_VECTORS:
            self._pending_move = DIRECTION_VECTORS[action]

        return BotOutput(action=action, messages=messages)

    # ── information gathering ────────────────────────────────────
    def _process_scan(self, context: BotContext) -> None:
        if context.scan_result is None:
            return
        pos = self._pos
        for (dx, dy), tile_info in context.scan_result.tiles.items():
            self._known[(pos[0] + dx, pos[1] + dy)] = tile_info.tile_type

    def _try_localize(self, context: BotContext) -> None:
        """Check recent scan results for OUT_OF_BOUNDS in cardinal
        directions and derive absolute position for each axis."""
        if context.scan_result is None:
            return

        for (dx, dy), tile_info in context.scan_result.tiles.items():
            if tile_info.tile_type != TileType.OUT_OF_BOUNDS:
                continue
            # Cardinal directions only give unambiguous single-axis info.
            if dx == -1 and dy == 0 and self._spawn_abs_x is None:
                # Left border: abs_x = 0, so spawn_abs_x = -rel_x
                self._spawn_abs_x = -self._rel_x
            elif dx == 1 and dy == 0 and self._spawn_abs_x is None:
                # Right border: abs_x = map_width - 1
                self._spawn_abs_x = context.map_width - 1 - self._rel_x
            elif dy == -1 and dx == 0 and self._spawn_abs_y is None:
                # Top border: abs_y = 0
                self._spawn_abs_y = -self._rel_y
            elif dy == 1 and dx == 0 and self._spawn_abs_y is None:
                # Bottom border: abs_y = map_height - 1
                self._spawn_abs_y = context.map_height - 1 - self._rel_y

        if self._localized and not self._map_promoted:
            self._promote_map()

    def _promote_map(self) -> None:
        """Convert the entire local map from relative to absolute coords."""
        assert self._spawn_abs_x is not None and self._spawn_abs_y is not None
        new_known: dict[tuple[int, int], TileType] = {}
        for (rx, ry), tile in self._known.items():
            new_known[(self._spawn_abs_x + rx, self._spawn_abs_y + ry)] = tile
        self._known = new_known

        new_visited: set[tuple[int, int]] = set()
        for (rx, ry) in self._visited:
            new_visited.add((self._spawn_abs_x + rx, self._spawn_abs_y + ry))
        self._visited = new_visited

        new_lp: list[tuple[int, int]] = []
        for (rx, ry) in self._last_positions:
            new_lp.append((self._spawn_abs_x + rx, self._spawn_abs_y + ry))
        self._last_positions = new_lp

        # Clear target/path — they were in relative coords.
        self._target = None
        self._path = []
        self._peer_positions.clear()
        self._map_promoted = True

    def _process_inbox(self, context: BotContext) -> None:
        for msg in context.inbox:
            if not msg.content.startswith(_TOKEN):
                continue
            self._decode_message(msg.content[len(_TOKEN):])

    def _decode_message(self, content: str) -> None:
        """Parse position (P) and scan (S) messages from teammates.

        Only *absolute* coordinate messages (prefixed ``A``) are
        consumed.  Messages from un-localised bots are ignored to
        avoid frame-mismatch corruption.
        """
        for segment in content.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            # Only accept absolute-coordinate messages.
            if not segment.startswith("A"):
                continue
            tag = segment[1]  # P or S
            body = segment[3:] if len(segment) > 3 else ""
            if tag == "P":
                # AP<id>:<x>,<y>
                try:
                    bid_str, coords = body.split(":")
                    xs, ys = coords.split(",")
                    self._peer_positions[int(bid_str)] = (int(xs), int(ys))
                except (ValueError, IndexError):
                    pass
            elif tag == "S":
                # AS<x>,<y>:<c>|...
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

    # ── message building ─────────────────────────────────────────
    def _build_messages(self, context: BotContext) -> list[Message]:
        freq = context.broadcast_frequency
        msgs: list[Message] = []

        if not self._localized:
            # Nothing useful to broadcast until localised.
            return msgs

        pos = self._pos
        # Prefix with "A" = absolute coordinates.
        pos_str = f"AP{self.id}:{pos[0]},{pos[1]}"

        scan_str = ""
        if context.scan_result is not None:
            parts: list[str] = []
            for (dx, dy), ti in context.scan_result.tiles.items():
                c = _TILE_CHAR.get(ti.tile_type, "E")
                parts.append(f"{pos[0]+dx},{pos[1]+dy}:{c}")
            scan_str = "AS" + "|".join(parts)

        combined = pos_str
        if scan_str:
            candidate = combined + ";" + scan_str
            if len(_TOKEN + candidate) <= 256:
                combined = candidate
            else:
                msgs.append(Message(frequency=freq, content=(_TOKEN + scan_str)[:256]))
        msgs.insert(0, Message(frequency=freq, content=(_TOKEN + combined)[:256]))
        return msgs[:3]

    # ── scan cadence ─────────────────────────────────────────────
    def _should_scan(self, context: BotContext) -> bool:
        if context.turn_number <= 2:
            return (context.turn_number % 2) == (self._index % 2)
        if self._turns_since_scan >= 3:
            return True
        pos = self._pos
        unknown_neighbors = sum(
            1 for _, (dx, dy) in _MOVES
            if (pos[0] + dx, pos[1] + dy) not in self._known
        )
        if unknown_neighbors >= 3:
            return True
        return False

    # ── navigation ───────────────────────────────────────────────
    def _navigate(self, context: BotContext) -> Action:
        pos = self._pos

        if self._is_stuck():
            self._target = None
            self._path = []

        if self._target is not None:
            if pos == self._target or self._target in self._known and \
               self._known[self._target] in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                self._target = None
                self._path = []

        if self._target is None:
            self._target = self._pick_frontier_target(pos)
            self._path = []

        if self._target is None:
            return self._random_passable_move(pos)

        if self._path and self._path[0] == pos:
            self._path.pop(0)
        if not self._path or self._path[0] != self._next_step_toward(pos):
            self._path = self._bfs_path(pos, self._target)

        if self._path:
            nxt = self._path[0]
            action = _action_for_step(pos, nxt)
            if action != Action.STAY:
                return action

        return self._random_passable_move(pos)

    def _next_step_toward(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        if self._path:
            return self._path[0]
        return None

    # ── frontier selection ───────────────────────────────────────
    def _pick_frontier_target(self, pos: tuple[int, int]) -> Optional[tuple[int, int]]:
        frontier: list[tuple[int, int]] = []
        for (kx, ky), tile in self._known.items():
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            for _, (dx, dy) in _MOVES:
                nb = (kx + dx, ky + dy)
                if nb not in self._known:
                    frontier.append(nb)

        if not frontier:
            # No unknown neighbours — target any known passable tile we
            # haven't visited yet (including traps — they count toward
            # exploration and must be stepped on).
            unvisited = [
                p for p, t in self._known.items()
                if t not in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS)
                and p not in self._visited
            ]
            if unvisited:
                self._rng.shuffle(unvisited)
                return unvisited[0]
            return None

        frontier = list(set(frontier))

        peers = [
            p for bid, p in self._peer_positions.items() if bid != self.id
        ]

        def score(tile: tuple[int, int]) -> float:
            dist_to_me = _manhattan(pos, tile)
            if peers:
                min_peer_dist = min(_manhattan(tile, p) for p in peers)
            else:
                min_peer_dist = 0
            return min_peer_dist * 2.0 - dist_to_me * 1.0

        frontier.sort(key=score, reverse=True)
        top_n = max(1, len(frontier) // 5)
        candidates = frontier[:top_n]
        return self._rng.choice(candidates)

    # ── BFS pathfinding ──────────────────────────────────────────
    def _bfs_path(
        self, start: tuple[int, int], goal: tuple[int, int], max_depth: int = 60
    ) -> list[tuple[int, int]]:
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
                if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                    continue
                # Allow pathing through traps (the freeze cost is worth
                # reaching unexplored areas on the other side).
                visited.add(nb)
                new_path = path + [nb]
                if nb == goal:
                    return new_path
                queue.append((nb, new_path))
        return []

    # ── stuck detection ──────────────────────────────────────────
    def _is_stuck(self) -> bool:
        if len(self._last_positions) < 6:
            return False
        recent = self._last_positions[-6:]
        return len(set(recent)) <= 3

    # ── fallback random move ─────────────────────────────────────
    def _random_passable_move(self, pos: tuple[int, int]) -> Action:
        # Prefer non-trap tiles but allow traps if no other option
        safe: list[Action] = []
        traps: list[Action] = []
        for action, (dx, dy) in _MOVES:
            nb = (pos[0] + dx, pos[1] + dy)
            tile = self._known.get(nb)
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            if tile == TileType.TRAP:
                traps.append(action)
            else:
                safe.append(action)
        if safe:
            return self._rng.choice(safe)
        if traps:
            return self._rng.choice(traps)
        # Nothing known — try any direction
        all_moves = [a for a, _ in _MOVES]
        return self._rng.choice(all_moves)


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
