"""Pathfinders Team — efficient cooperative exploration.

Strategy overview
-----------------
**Core idea:** explore the map efficiently by avoiding hazards and
partitioning the territory among teammates.

1. **Enhanced localisation** — diagonal ``OUT_OF_BOUNDS`` inference lets a
   bot resolve both axes in fewer scans.
2. **Trap sharing & avoidance** — every broadcast includes ALL known trap
   locations.  Dijkstra pathfinding assigns a high cost to traps so bots
   route around them when a reasonable detour exists.
3. **Zone-based frontier preference** — once localised the map is divided
   into 5 vertical strips.  Each bot prioritises frontiers in its own
   strip, reducing overlap without hard constraints.
"""

from __future__ import annotations

import random
from heapq import heappop, heappush
from typing import Optional

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotOutput, Message, TileType)
from radiogrid.registry import TeamRegistry

# ── constants ────────────────────────────────────────────────────────

_MOVES: list[tuple[Action, tuple[int, int]]] = [
    (Action.MOVE_UP, (0, -1)),
    (Action.MOVE_DOWN, (0, 1)),
    (Action.MOVE_LEFT, (-1, 0)),
    (Action.MOVE_RIGHT, (1, 0)),
]

_TILE_CHAR: dict[TileType, str] = {
    TileType.EMPTY: "E",
    TileType.OBSTACLE: "O",
    TileType.TRAP: "T",
    TileType.SPAWN: "S",
}
_CHAR_TILE: dict[str, TileType] = {v: k for k, v in _TILE_CHAR.items()}

_TOKEN = "#PTH#"  # shared secret for message authentication

_TRAP_STEP_COST = 5
_MAX_PATH_COST = 50


# ── helpers ──────────────────────────────────────────────────────────

def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _action_for_step(src: tuple[int, int], dst: tuple[int, int]) -> Action:
    dx, dy = dst[0] - src[0], dst[1] - src[1]
    for action, (adx, ady) in _MOVES:
        if adx == dx and ady == dy:
            return action
    return Action.STAY


# ── SmartBot ─────────────────────────────────────────────────────────

class SmartBot(Bot):
    """Frontier explorer with trap avoidance + zone coordination."""

    def __init__(self, bot_index: int, rng_seed: int | None = None) -> None:
        super().__init__()
        self._idx = bot_index
        self._rng = random.Random(rng_seed)

        # position tracking (spawn = relative origin)
        self._rel_x: int = 0
        self._rel_y: int = 0
        self._pending_move: tuple[int, int] | None = None

        # localisation
        self._spawn_abs_x: int | None = None
        self._spawn_abs_y: int | None = None
        self._map_promoted: bool = False

        # map knowledge
        self._known: dict[tuple[int, int], TileType] = {}
        self._known_traps: set[tuple[int, int]] = set()
        self._visited: set[tuple[int, int]] = set()

        # peers
        self._peer_positions: dict[int, tuple[int, int]] = {}

        # navigation
        self._target: Optional[tuple[int, int]] = None
        self._path: list[tuple[int, int]] = []
        self._turns_since_scan: int = 100
        self._last_positions: list[tuple[int, int]] = []

        # zone
        self._zone_id: int = -1
        self._zone_x_min: int = 0
        self._zone_x_max: int = 0
        self._zone_computed: bool = False
        self._peer_zones: dict[int, int] = {}  # bot_id -> zone_id
        self._map_w: int = 0
        self._map_h: int = 0

    # ── coordinate helpers ───────────────────────────────────────

    @property
    def _loc(self) -> bool:
        return self._spawn_abs_x is not None and self._spawn_abs_y is not None

    @property
    def _pos(self) -> tuple[int, int]:
        if self._map_promoted:
            return (self._spawn_abs_x + self._rel_x,  # type: ignore[operator]
                    self._spawn_abs_y + self._rel_y)   # type: ignore[operator]
        return (self._rel_x, self._rel_y)

    # ── main entry ───────────────────────────────────────────────

    def decide(self, ctx: BotContext) -> BotOutput:
        self._map_w = ctx.map_width
        self._map_h = ctx.map_height

        # resolve last move
        if self._pending_move is not None:
            if ctx.move_succeeded:
                self._rel_x += self._pending_move[0]
                self._rel_y += self._pending_move[1]
            self._pending_move = None

        pos = self._pos
        self._visited.add(pos)
        self._known.setdefault(pos, TileType.EMPTY)

        self._last_positions.append(pos)
        if len(self._last_positions) > 10:
            self._last_positions.pop(0)

        # absorb information
        self._process_scan(ctx)
        self._try_localize(ctx)
        self._process_inbox(ctx)

        # trap self-detection
        if ctx.frozen_turns_remaining > 0:
            self._known[pos] = TileType.TRAP
            if self._map_promoted:
                self._known_traps.add(pos)

        # zone
        if self._loc and not self._zone_computed:
            self._compute_zone()

        messages = self._build_messages(ctx)

        # frozen → scan
        if ctx.frozen_turns_remaining > 0:
            return BotOutput(action=Action.SCAN, messages=messages)

        # scan decision
        if self._should_scan(ctx):
            self._turns_since_scan = 0
            return BotOutput(action=Action.SCAN, messages=messages)
        self._turns_since_scan += 1

        # navigate
        action = self._navigate(ctx)
        if action in DIRECTION_VECTORS:
            self._pending_move = DIRECTION_VECTORS[action]
        return BotOutput(action=action, messages=messages)

    # ================================================================
    #  INFORMATION GATHERING
    # ================================================================

    def _process_scan(self, ctx: BotContext) -> None:
        if ctx.scan_result is None:
            return
        px, py = self._pos
        for (dx, dy), ti in ctx.scan_result.tiles.items():
            c = (px + dx, py + dy)
            self._known[c] = ti.tile_type
            if ti.tile_type == TileType.TRAP and self._map_promoted:
                self._known_traps.add(c)

    def _try_localize(self, ctx: BotContext) -> None:
        if ctx.scan_result is None:
            return
        tiles = ctx.scan_result.tiles

        # cardinal OOB
        if self._spawn_abs_x is None:
            t = tiles.get((-1, 0))
            if t and t.tile_type == TileType.OUT_OF_BOUNDS:
                self._spawn_abs_x = -self._rel_x
            else:
                t = tiles.get((1, 0))
                if t and t.tile_type == TileType.OUT_OF_BOUNDS:
                    self._spawn_abs_x = ctx.map_width - 1 - self._rel_x
        if self._spawn_abs_y is None:
            t = tiles.get((0, -1))
            if t and t.tile_type == TileType.OUT_OF_BOUNDS:
                self._spawn_abs_y = -self._rel_y
            else:
                t = tiles.get((0, 1))
                if t and t.tile_type == TileType.OUT_OF_BOUNDS:
                    self._spawn_abs_y = ctx.map_height - 1 - self._rel_y

        # diagonal OOB inference
        for ddx, ddy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            d = tiles.get((ddx, ddy))
            if d is None or d.tile_type != TileType.OUT_OF_BOUNDS:
                continue
            h = tiles.get((ddx, 0))
            v = tiles.get((0, ddy))
            if h and h.tile_type != TileType.OUT_OF_BOUNDS and self._spawn_abs_y is None:
                self._spawn_abs_y = (
                    -self._rel_y if ddy == -1
                    else ctx.map_height - 1 - self._rel_y
                )
            if v and v.tile_type != TileType.OUT_OF_BOUNDS and self._spawn_abs_x is None:
                self._spawn_abs_x = (
                    -self._rel_x if ddx == -1
                    else ctx.map_width - 1 - self._rel_x
                )

        if self._loc and not self._map_promoted:
            self._promote_map()

    def _promote_map(self) -> None:
        ox, oy = self._spawn_abs_x, self._spawn_abs_y
        assert ox is not None and oy is not None
        new: dict[tuple[int, int], TileType] = {}
        for (rx, ry), tile in self._known.items():
            a = (ox + rx, oy + ry)
            new[a] = tile
            if tile == TileType.TRAP:
                self._known_traps.add(a)
        self._known = new
        self._visited = {(ox + rx, oy + ry) for rx, ry in self._visited}
        self._last_positions = [(ox + rx, oy + ry) for rx, ry in self._last_positions]
        self._target = None
        self._path = []
        self._peer_positions.clear()
        self._map_promoted = True

    def _process_inbox(self, ctx: BotContext) -> None:
        for msg in ctx.inbox:
            if not msg.content.startswith(_TOKEN):
                continue
            payload = msg.content[len(_TOKEN):]
            for seg in payload.split(";"):
                seg = seg.strip()
                if len(seg) < 3 or seg[0] != "A":
                    continue
                tag, body = seg[1], seg[2:]
                try:
                    if tag == "P":
                        bid_s, coords = body.split(":")
                        xs, ys = coords.split(",")
                        self._peer_positions[int(bid_s)] = (int(xs), int(ys))
                    elif tag == "Z":
                        bid_s, z = body.split(":")
                        self._peer_zones[int(bid_s)] = int(z)
                    elif tag == "T":
                        for part in body.split("|"):
                            if "," not in part:
                                continue
                            xs, ys = part.split(",")
                            tp = (int(xs), int(ys))
                            self._known_traps.add(tp)
                            if self._map_promoted:
                                self._known[tp] = TileType.TRAP
                    elif tag == "S":
                        if not self._map_promoted:
                            continue
                        for part in body.split("|"):
                            if ":" not in part:
                                continue
                            coords, c = part.rsplit(":", 1)
                            xs, ys = coords.split(",")
                            p = (int(xs), int(ys))
                            if c in _CHAR_TILE:
                                tt = _CHAR_TILE[c]
                                if p not in self._known:
                                    self._known[p] = tt
                                if tt == TileType.TRAP:
                                    self._known_traps.add(p)
                except (ValueError, IndexError):
                    pass

    # ================================================================
    #  RADIO
    # ================================================================

    def _build_messages(self, ctx: BotContext) -> list[Message]:
        if not self._loc:
            return []
        freq = ctx.broadcast_frequency
        pos = self._pos
        msgs: list[Message] = []

        # msg 1: position + zone + traps
        parts = [f"AP{self.id}:{pos[0]},{pos[1]}"]
        if self._zone_id >= 0:
            parts.append(f"AZ{self.id}:{self._zone_id}")
        if self._known_traps:
            _traps_iter = self._known_traps if len(self._known_traps) <= 20 else list(self._known_traps)[:20]
            tb = "AT" + "|".join(f"{x},{y}" for x, y in _traps_iter)
            cand = ";".join(parts) + ";" + tb
            if len(_TOKEN + cand) <= 256:
                parts.append(tb)
            else:
                msgs.append(Message(frequency=freq, content=(_TOKEN + tb)[:256]))
        msgs.insert(0, Message(frequency=freq, content=(_TOKEN + ";".join(parts))[:256]))

        # msg 2: scan data
        if ctx.scan_result is not None:
            sp: list[str] = []
            for (dx, dy), ti in ctx.scan_result.tiles.items():
                c = _TILE_CHAR.get(ti.tile_type)
                if c is None:
                    continue
                sp.append(f"{pos[0]+dx},{pos[1]+dy}:{c}")
            if sp:
                msgs.append(Message(frequency=freq, content=(_TOKEN + "AS" + "|".join(sp))[:256]))
        return msgs[:3]

    # ================================================================
    #  ZONE
    # ================================================================

    def _compute_zone(self) -> None:
        """Assign the closest zone not already claimed by a peer."""
        w = self._map_w
        strip = max(1, w // 5)
        pos_x = self._pos[0]

        taken = set(self._peer_zones.values())

        # Rank zones by distance to current position, prefer unclaimed
        best_zone = 0
        best_score = -999999.0
        for z in range(5):
            zmin = z * strip
            zmax = (z + 1) * strip - 1 if z < 4 else w - 1
            centre = (zmin + zmax) / 2
            dist = abs(pos_x - centre)
            s = -dist
            if z in taken:
                s -= 100  # strongly avoid claimed zones
            if s > best_score:
                best_score = s
                best_zone = z

        self._zone_id = best_zone
        self._zone_x_min = best_zone * strip
        self._zone_x_max = (best_zone + 1) * strip - 1 if best_zone < 4 else w - 1
        self._zone_computed = True

    # ================================================================
    #  SCAN CADENCE
    # ================================================================

    def _should_scan(self, ctx: BotContext) -> bool:
        if ctx.turn_number <= 2:
            return (ctx.turn_number % 2) == (self._idx % 2)
        if self._turns_since_scan >= 3:
            return True
        pos = self._pos
        unknown = sum(
            1 for _, (dx, dy) in _MOVES
            if (pos[0] + dx, pos[1] + dy) not in self._known
        )
        return unknown >= 3

    # ================================================================
    #  NAVIGATION
    # ================================================================

    def _navigate(self, ctx: BotContext) -> Action:
        pos = self._pos

        if self._is_stuck():
            self._target = None
            self._path = []

        if self._target is not None:
            if pos == self._target:
                self._target = None
                self._path = []
            elif self._target in self._known and self._known[self._target] in (
                TileType.OBSTACLE, TileType.OUT_OF_BOUNDS,
            ):
                self._target = None
                self._path = []

        if self._target is None:
            self._target = self._pick_frontier(pos)
            self._path = []

        if self._target is None:
            return self._random_safe_move(pos)

        if self._path and self._path[0] == pos:
            self._path.pop(0)
        if not self._path:
            self._path = self._dijkstra_path(pos, self._target)

        if self._path:
            nxt = self._path[0]
            act = _action_for_step(pos, nxt)
            if act != Action.STAY:
                return act

        return self._random_safe_move(pos)

    # ── frontier selection ───────────────────────────────────────

    def _pick_frontier(self, pos: tuple[int, int]) -> Optional[tuple[int, int]]:
        frontier: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        known = self._known
        _OBS = TileType.OBSTACLE
        _OOB = TileType.OUT_OF_BOUNDS
        for (kx, ky), tile in known.items():
            if tile is _OBS or tile is _OOB:
                continue
            for _, (dx, dy) in _MOVES:
                nb = (kx + dx, ky + dy)
                if nb not in seen and nb not in known:
                    seen.add(nb)
                    frontier.append(nb)

        if not frontier:
            unvisited = [
                p for p, t in known.items()
                if t is not _OBS and t is not _OOB
                and p not in self._visited
            ]
            if unvisited:
                self._rng.shuffle(unvisited)
                return unvisited[0]
            return None

        peers = [p for bid, p in self._peer_positions.items() if bid != self.id]

        # Sample if frontier is very large
        if len(frontier) > 100:
            self._rng.shuffle(frontier)
            candidates = frontier[:100]
        else:
            candidates = frontier

        # Inline scoring for speed
        px, py = pos
        peer_coords = [(p[0], p[1]) for p in peers]
        zone_active = self._zone_computed
        zmin = self._zone_x_min
        zmax = self._zone_x_max
        scored: list[tuple[float, tuple[int, int]]] = []
        for tile in candidates:
            tx, ty = tile
            dist = abs(px - tx) + abs(py - ty)
            mp = 999_999
            for ppx, ppy in peer_coords:
                pd = abs(tx - ppx) + abs(ty - ppy)
                if pd < mp:
                    mp = pd
            if mp == 999_999:
                mp = 0
            s = mp * 2.0 - dist
            if zone_active and zmin <= tx <= zmax:
                s += 5.0
            scored.append((s, tile))
        scored.sort(reverse=True, key=lambda x: x[0])
        top_n = max(1, len(scored) // 5)
        return self._rng.choice(scored[:top_n])[1]

    # ── Dijkstra (trap-aware) ────────────────────────────────────

    def _dijkstra_path(
        self, start: tuple[int, int], goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        if start == goal:
            return []
        known = self._known
        known_traps = self._known_traps
        _OBS = TileType.OBSTACLE
        _OOB = TileType.OUT_OF_BOUNDS
        _TRAP = TileType.TRAP
        max_cost = _MAX_PATH_COST
        gx, gy = goal
        h0 = abs(start[0] - gx) + abs(start[1] - gy)
        # A* heap: (f=g+h, g, x, y)
        heap: list[tuple[int, int, int, int]] = [(h0, 0, start[0], start[1])]
        costs: dict[tuple[int, int], int] = {start: 0}
        parent: dict[tuple[int, int], tuple[int, int]] = {}
        expansions = 0

        while heap:
            _f, g, cx, cy = heappop(heap)
            if cx == gx and cy == gy:
                path: list[tuple[int, int]] = []
                cur = goal
                while cur != start:
                    path.append(cur)
                    cur = parent[cur]
                path.reverse()
                return path
            cur = (cx, cy)
            if g > costs.get(cur, 999_999):
                continue
            if g >= max_cost:
                continue
            expansions += 1
            if expansions > 1000:
                break
            for _, (dx, dy) in _MOVES:
                nx, ny = cx + dx, cy + dy
                nb = (nx, ny)
                tile = known.get(nb)
                if tile is _OBS or tile is _OOB:
                    continue
                step = _TRAP_STEP_COST if (tile is _TRAP or nb in known_traps) else 1
                nc = g + step
                if nc < costs.get(nb, 999_999):
                    costs[nb] = nc
                    parent[nb] = cur
                    heappush(heap, (nc + abs(nx - gx) + abs(ny - gy), nc, nx, ny))
        return []

    # ── stuck detection ──────────────────────────────────────────

    def _is_stuck(self) -> bool:
        if len(self._last_positions) < 6:
            return False
        return len(set(self._last_positions[-6:])) <= 3

    # ── safe random move ─────────────────────────────────────────

    def _random_safe_move(self, pos: tuple[int, int]) -> Action:
        safe: list[Action] = []
        risky: list[Action] = []
        for action, (dx, dy) in _MOVES:
            nb = (pos[0] + dx, pos[1] + dy)
            tile = self._known.get(nb)
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            if tile == TileType.TRAP or nb in self._known_traps:
                risky.append(action)
            else:
                safe.append(action)
        if safe:
            return self._rng.choice(safe)
        if risky:
            return self._rng.choice(risky)
        return self._rng.choice([a for a, _ in _MOVES])


# ── Team registration ────────────────────────────────────────────────

@TeamRegistry.register(
    key="pathfinders",
    name="Pathfinders",
    description=(
        "Frontier exploration enhanced with trap sharing, "
        "Dijkstra pathfinding around hazards, and zone-based territory coordination."
    ),
    author="Copilot",
)
class SmartTeam(Team):
    def __init__(
        self, default_frequency: int = 77, seed: int | None = None,
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed
        self._bots: list[SmartBot] = []

    def initialize(self) -> list[Bot]:
        self._bots = [
            SmartBot(
                bot_index=i,
                rng_seed=(self._seed + i if self._seed is not None else None),
            )
            for i in range(5)
        ]
        return self._bots

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        """Merge all bots' absolute-coordinate maps.

        Coordinates outside the map boundaries are filtered out to
        avoid penalising the team for radio-interference artefacts.
        """
        _EMPTY = TileType.EMPTY
        _SPAWN = TileType.SPAWN
        merged: dict[tuple[int, int], TileType] = {}
        for bot in self._bots:
            if not bot._map_promoted:
                continue
            for pos, tile in bot._known.items():
                if tile is _EMPTY or tile is _SPAWN:
                    existing = merged.get(pos)
                    if existing is None or (existing is _EMPTY and tile is _SPAWN):
                        merged[pos] = tile

        # Determine map bounds from any bot that has seen a context.
        map_w = map_h = 0
        for bot in self._bots:
            if bot._map_w > 0:
                map_w, map_h = bot._map_w, bot._map_h
                break
        if map_w > 0:
            merged = {
                pos: tile
                for pos, tile in merged.items()
                if 0 <= pos[0] < map_w and 0 <= pos[1] < map_h
            }
        return merged
