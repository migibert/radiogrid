"""Rendezvous — shared relative frame from turn 1.

Strategy overview
-----------------
**Phase 1 — Bootstrap (turns 1–3):**
  All bots SCAN on turn 1.  On turn 2 each bot identifies nearby
  teammates from scan results (``BotInfo`` in neighbouring tiles) and
  broadcasts the sightings via radio.  By turn 3 every bot has enough
  gossip to compute its position in a *shared* relative coordinate
  frame (anchored at the lowest-ID bot's spawn = origin).

**Phase 2 — Directed border-seeking:**
  After bootstrap, bots 0–3 are each assigned a cardinal direction
  (N/S/W/E) and bias their frontier selection toward that border.
  When a bot detects ``OUT_OF_BOUNDS`` it resolves the corresponding
  axis.  The shared→absolute translation delta is broadcast over
  radio so *all* teammates resolve that axis instantly — only two
  bots (one per axis) need to actually reach a border.

  As a fallback, once a bot has resolved one axis it redirects toward
  the perpendicular border to find the second axis itself.

**Phase 3 — Collaborative exploration:**
  Once both axes are resolved a bot promotes its map to absolute
  coordinates.  It then uses zone-based coordination (5 vertical
  strips), Dijkstra pathfinding (trap-aware), and peer-avoidance
  frontier scoring — same as the Smart Coordinators but with the
  10–25 turn head start from the shared frame.

The bootstrap costs only ~2 scan turns (which also gather useful local
tile data), after which the team collaborates as effectively as a fully
localised team — 10–25 turns earlier than border-based approaches.
"""

from __future__ import annotations

import random
from collections import deque
from heapq import heappop, heappush
from typing import Optional

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotOutput, Message, TileType)
from radiogrid.registry import TeamRegistry

# ── Constants ────────────────────────────────────────────────────────

TEAM_SIZE = 5
_BOOTSTRAP_TIMEOUT = 6  # abandon bootstrap after this turn number
_TRAP_STEP_COST = 5  # Dijkstra cost to traverse a known trap tile
_MAX_PATH_COST = 50

# Cardinal directions assigned to bot indices 0-3; bot 4 gets shortest axis.
_BORDER_DIRS: list[tuple[int, int]] = [
    (0, -1),   # bot 0 → North (up)
    (0, 1),    # bot 1 → South (down)
    (-1, 0),   # bot 2 → West  (left)
    (1, 0),    # bot 3 → East  (right)
]

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
_TOKEN_FMT = "#RDV{}#"  # token template — team_id inserted at init

# ── Helpers ──────────────────────────────────────────────────────────


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _action_for_step(src: tuple[int, int], dst: tuple[int, int]) -> Action:
    dx, dy = dst[0] - src[0], dst[1] - src[1]
    for action, (adx, ady) in _MOVES:
        if adx == dx and ady == dy:
            return action
    return Action.STAY


# ── RendezvousBot ────────────────────────────────────────────────────


class RendezvousBot(Bot):
    """Bot that establishes a shared coordinate frame before exploring.

    During the bootstrap phase (turns 1-3) bots scan to discover each
    other, exchange sighting offsets via radio, and build a common
    relative frame.  Once the frame is established the map is promoted
    to shared-frame coordinates and bots collaborate immediately.
    """

    def __init__(self, bot_index: int, rng_seed: int | None = None,
                 team_id: int = 0) -> None:
        super().__init__()
        self._idx = bot_index
        self._rng = random.Random(rng_seed)
        self._token = _TOKEN_FMT.format(team_id)

        # ── Position tracking (own spawn = origin) ───────────────
        self._rel_x: int = 0
        self._rel_y: int = 0
        self._pending_move: tuple[int, int] | None = None

        # ── Bootstrap state ──────────────────────────────────────
        self._bootstrap_done: bool = False
        # {observer_engine_id: {seen_engine_id: (dx, dy)}}
        # offsets are relative to the observer's position AT SCAN TIME
        self._peer_sightings: dict[int, dict[int, tuple[int, int]]] = {}
        # own direct sightings from scans
        self._my_sightings: dict[int, tuple[int, int]] = {}
        # all teammate engine IDs discovered so far (including self)
        self._team_engine_ids: set[int] = set()
        # lowest engine ID on the team — defines shared-frame origin
        self._reference_id: int | None = None
        # own spawn position in the shared frame (ref spawn = (0,0))
        self._shared_offset: tuple[int, int] | None = None

        # ── Map state ────────────────────────────────────────────
        # Before bootstrap: keys are in spawn-relative coords.
        # After bootstrap: keys are in shared-frame coords.
        self._known: dict[tuple[int, int], TileType] = {}
        self._known_traps: set[tuple[int, int]] = set()
        self._in_shared_frame: bool = False
        self._visited: set[tuple[int, int]] = set()
        self._tiles_broadcast: set[tuple[int, int]] = set()
        self._peer_positions: dict[int, tuple[int, int]] = {}

        # ── Absolute localisation ────────────────────────────────
        # Once both axes are resolved via OOB detection the map is
        # promoted from shared-relative to absolute coordinates.
        self._spawn_abs_x: int | None = None  # abs X of own spawn
        self._spawn_abs_y: int | None = None  # abs Y of own spawn
        self._abs_localized: bool = False
        self._abs_promoted: bool = False
        self._border_dir: tuple[int, int] | None = None  # assigned seek direction
        # Shared→absolute translation delta per axis.  The delta is the
        # same for every bot in the shared frame, so a single broadcast
        # lets all teammates resolve that axis without visiting the border.
        self._shared_abs_delta_x: int | None = None
        self._shared_abs_delta_y: int | None = None

        # ── Zone coordination ────────────────────────────────────
        self._map_w: int = 0
        self._map_h: int = 0
        self._zone_id: int = -1
        self._zone_x_min: int = 0
        self._zone_x_max: int = 0
        self._zone_computed: bool = False
        self._peer_zones: dict[int, int] = {}  # engine_id → zone_id

        # ── Navigation ───────────────────────────────────────────
        self._target: Optional[tuple[int, int]] = None
        self._path: list[tuple[int, int]] = []
        self._turns_since_scan: int = 100  # force early scan
        self._last_positions: list[tuple[int, int]] = []

    # ── Coordinate helpers ───────────────────────────────────────

    @property
    def _pos(self) -> tuple[int, int]:
        """Current position in the active coordinate frame."""
        if self._abs_promoted:
            # Absolute coordinates
            assert self._spawn_abs_x is not None and self._spawn_abs_y is not None
            return (self._spawn_abs_x + self._rel_x,
                    self._spawn_abs_y + self._rel_y)
        if self._in_shared_frame and self._shared_offset is not None:
            return (
                self._shared_offset[0] + self._rel_x,
                self._shared_offset[1] + self._rel_y,
            )
        return (self._rel_x, self._rel_y)

    # ── Main entry ───────────────────────────────────────────────

    def decide(self, ctx: BotContext) -> BotOutput:
        self._map_w = ctx.map_width
        self._map_h = ctx.map_height

        # Resolve pending move
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

        # Always process scan tiles
        self._process_scan_tiles(ctx)

        # Trap self-detection
        if ctx.frozen_turns_remaining > 0:
            self._known[pos] = TileType.TRAP
            self._known_traps.add(pos)

        # ── Bootstrap phase ──────────────────────────────────────
        bootstrap_msgs: list[Message] = []
        if not self._bootstrap_done:
            self._scan_for_teammates(ctx)
            self._process_bootstrap_inbox(ctx)
            self._try_establish_shared_frame()
            bootstrap_msgs = self._build_bootstrap_messages(ctx)

            if self._bootstrap_done and self._shared_offset is not None:
                self._promote_to_shared_frame()
            elif not self._bootstrap_done:
                if ctx.turn_number >= _BOOTSTRAP_TIMEOUT:
                    # Give up bootstrap — explore in spawn-relative frame
                    self._bootstrap_done = True
                    # Assign border direction for direct localisation
                    if self._border_dir is None:
                        if self._idx < 4:
                            self._border_dir = _BORDER_DIRS[self._idx]
                        else:
                            self._border_dir = (0, -1)
                else:
                    return BotOutput(action=Action.SCAN, messages=bootstrap_msgs)

        # ── Explore inbox (before localisation — may carry axis deltas) ─
        self._process_explore_inbox(ctx)

        # ── Localisation ──────────────────────────────────────────
        self._try_localize_from_scan(ctx)
        if self._abs_localized and not self._abs_promoted:
            self._promote_to_absolute(ctx)
        # After resolving one axis, redirect toward the other border.
        self._update_border_dir()

        # ── Zone ──────────────────────────────────────────────────
        if self._abs_localized and not self._zone_computed:
            self._compute_zone()

        # ── Explore messages ─────────────────────────────────────
        explore_msgs = self._build_explore_messages(ctx)
        messages = (bootstrap_msgs + explore_msgs)[:3]

        if ctx.frozen_turns_remaining > 0:
            return BotOutput(action=Action.SCAN, messages=messages)

        if self._should_scan(ctx):
            self._turns_since_scan = 0
            return BotOutput(action=Action.SCAN, messages=messages)
        self._turns_since_scan += 1

        action = self._navigate(ctx)
        if action in DIRECTION_VECTORS:
            self._pending_move = DIRECTION_VECTORS[action]
        return BotOutput(action=action, messages=messages)

    # ================================================================
    #  BOOTSTRAP
    # ================================================================

    def _scan_for_teammates(self, ctx: BotContext) -> None:
        """Detect teammates in scan results and record their offsets."""
        if ctx.scan_result is None:
            return
        for (dx, dy), tile_info in ctx.scan_result.tiles.items():
            for bot_info in tile_info.bots:
                if bot_info.team_id == self.team_id and bot_info.id != self.id:
                    self._my_sightings.setdefault(bot_info.id, (dx, dy))
                    self._team_engine_ids.add(bot_info.id)
        self._team_engine_ids.add(self.id)
        if self._my_sightings:
            self._peer_sightings[self.id] = dict(self._my_sightings)

    def _process_bootstrap_inbox(self, ctx: BotContext) -> None:
        """Process gossip messages to learn transitive teammate offsets.

        Bootstrap message format::

            B<observer_id>:<seen_id>,<dx>,<dy>|<seen_id>,<dx>,<dy>|...
        """
        for msg in ctx.inbox:
            if not msg.content.startswith(self._token):
                continue
            payload = msg.content[len(self._token):]
            if not payload.startswith("B"):
                continue
            try:
                rest = payload[1:]  # strip "B"
                observer_str, sights_str = rest.split(":", 1)
                observer_id = int(observer_str)
                self._team_engine_ids.add(observer_id)

                sightings: dict[int, tuple[int, int]] = {}
                for part in sights_str.split("|"):
                    if not part:
                        continue
                    tokens = part.split(",")
                    if len(tokens) != 3:
                        continue
                    seen_id = int(tokens[0])
                    dx, dy = int(tokens[1]), int(tokens[2])
                    sightings[seen_id] = (dx, dy)
                    self._team_engine_ids.add(seen_id)

                if sightings:
                    # Merge — don't overwrite if we already have this
                    # observer's data (own sightings take priority)
                    if observer_id not in self._peer_sightings:
                        self._peer_sightings[observer_id] = sightings
                    else:
                        for sid, off in sightings.items():
                            self._peer_sightings[observer_id].setdefault(sid, off)
            except (ValueError, IndexError):
                pass

    def _try_establish_shared_frame(self) -> None:
        """BFS through the sighting graph to compute own offset from reference.

        The reference bot (lowest engine ID on the team) defines the
        shared-frame origin ``(0, 0)``.  If observer *O* saw target *T*
        at scan offset ``(dx, dy)``, then ``T = O + (dx, dy)`` in the
        shared frame.  The graph is traversed bidirectionally.
        """
        if self._shared_offset is not None:
            self._bootstrap_done = True
            return

        # Wait until all 5 teammates are discovered so the reference
        # (= min engine ID) is stable and won't change later.
        if len(self._team_engine_ids) < TEAM_SIZE:
            return

        self._reference_id = min(self._team_engine_ids)

        # Build bidirectional adjacency from all known sightings.
        adj: dict[int, list[tuple[int, tuple[int, int]]]] = {}
        for obs_id, sightings in self._peer_sightings.items():
            for seen_id, (dx, dy) in sightings.items():
                adj.setdefault(obs_id, []).append((seen_id, (dx, dy)))
                adj.setdefault(seen_id, []).append((obs_id, (-dx, -dy)))

        # BFS from the reference bot.
        visited: dict[int, tuple[int, int]] = {self._reference_id: (0, 0)}
        queue: deque[int] = deque([self._reference_id])

        while queue:
            node = queue.popleft()
            offset = visited[node]
            if node == self.id:
                self._shared_offset = offset
                self._bootstrap_done = True
                return
            for neighbor, (dx, dy) in adj.get(node, []):
                if neighbor not in visited:
                    visited[neighbor] = (offset[0] + dx, offset[1] + dy)
                    queue.append(neighbor)

    def _build_bootstrap_messages(self, ctx: BotContext) -> list[Message]:
        """Broadcast own sightings and relay peer sightings (gossip)."""
        freq = ctx.broadcast_frequency
        msgs: list[Message] = []

        # Message 1: own sightings
        if self._my_sightings:
            parts = [
                f"{sid},{dx},{dy}"
                for sid, (dx, dy) in self._my_sightings.items()
            ]
            content = self._token + f"B{self.id}:" + "|".join(parts)
            msgs.append(Message(frequency=freq, content=content[:256]))

        # Messages 2-3: relay peer sightings for transitive discovery
        for obs_id, sightings in self._peer_sightings.items():
            if obs_id == self.id or len(msgs) >= 3:
                break
            parts = [
                f"{sid},{dx},{dy}"
                for sid, (dx, dy) in sightings.items()
            ]
            content = self._token + f"B{obs_id}:" + "|".join(parts)
            msgs.append(Message(frequency=freq, content=content[:256]))

        return msgs[:3]

    def _promote_to_shared_frame(self) -> None:
        """Convert all map data from spawn-relative to shared-frame coords."""
        if self._shared_offset is None or self._in_shared_frame:
            return
        ox, oy = self._shared_offset
        self._known = {
            (ox + rx, oy + ry): t for (rx, ry), t in self._known.items()
        }
        self._visited = {(ox + rx, oy + ry) for rx, ry in self._visited}
        self._last_positions = [
            (ox + rx, oy + ry) for rx, ry in self._last_positions
        ]
        # Re-key traps into shared frame
        self._known_traps = {(ox + rx, oy + ry) for rx, ry in self._known_traps}
        self._target = None
        self._path = []
        self._peer_positions.clear()
        self._tiles_broadcast.clear()
        self._in_shared_frame = True

        # Assign border-seeking direction now that we're in shared frame
        if self._border_dir is None:
            if self._idx < 4:
                self._border_dir = _BORDER_DIRS[self._idx]
            else:
                # Bot 4: pick the axis that appears shorter from our position
                # (heuristic: use map dimensions to guess)
                # Default to North if unsure
                self._border_dir = (0, -1)

    # ================================================================
    #  ABSOLUTE LOCALISATION
    # ================================================================

    def _try_localize_from_scan(self, ctx: BotContext) -> None:
        """Check the latest scan for OUT_OF_BOUNDS to resolve absolute axes.

        Uses cardinal neighbours directly and diagonal OOB inference
        (same technique as SmartBot).  Only meaningful once we are in
        the shared frame so that ``_pos`` is consistent.
        """
        if self._abs_localized or ctx.scan_result is None:
            return

        tiles = ctx.scan_result.tiles

        # Cardinal OOB
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

        # Diagonal OOB inference
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

        # Compute / cache shared→absolute deltas for radio sharing.
        if self._shared_offset is not None:
            if self._spawn_abs_x is not None and self._shared_abs_delta_x is None:
                self._shared_abs_delta_x = self._spawn_abs_x - self._shared_offset[0]
            if self._spawn_abs_y is not None and self._shared_abs_delta_y is None:
                self._shared_abs_delta_y = self._spawn_abs_y - self._shared_offset[1]

        if self._spawn_abs_x is not None and self._spawn_abs_y is not None:
            self._abs_localized = True

    def _update_border_dir(self) -> None:
        """Redirect toward the perpendicular border once one axis is found.

        If a bot heading North found the Y axis, there's no reason to
        keep heading North — switch to West/East to find the X axis.
        This is a backup for cases where radio sharing of the delta
        doesn't reach a teammate quickly enough.
        """
        if self._abs_localized:
            return  # both axes resolved — no border-seeking needed
        if self._spawn_abs_x is not None and self._spawn_abs_y is None:
            # X resolved, need Y → head North
            if self._border_dir is None or self._border_dir[1] == 0:
                self._border_dir = (0, -1)
                self._target = None
                self._path = []
        elif self._spawn_abs_y is not None and self._spawn_abs_x is None:
            # Y resolved, need X → head West
            if self._border_dir is None or self._border_dir[0] == 0:
                self._border_dir = (-1, 0)
                self._target = None
                self._path = []

    def _promote_to_absolute(self, ctx: BotContext) -> None:
        """Convert map from shared-relative frame to absolute coordinates.

        This requires knowing both ``_spawn_abs_x`` and ``_spawn_abs_y``.
        The shared-frame offset is folded in: a shared-frame coord
        ``(sx, sy)`` becomes ``(spawn_abs_x + rel_x, ...)`` via the new
        ``_pos`` property.  For the *map* we need the combined transform:
        ``abs = shared + (spawn_abs - shared_offset)``.
        """
        assert self._spawn_abs_x is not None and self._spawn_abs_y is not None

        # delta from current frame origin to absolute origin.
        # When shared_offset is None (bootstrap failed), the map is
        # in spawn-relative coords where own spawn = (0, 0).
        so = self._shared_offset if self._shared_offset is not None else (0, 0)
        dx = self._spawn_abs_x - so[0]
        dy = self._spawn_abs_y - so[1]

        self._known = {
            (sx + dx, sy + dy): t for (sx, sy), t in self._known.items()
        }
        self._visited = {(sx + dx, sy + dy) for sx, sy in self._visited}
        self._last_positions = [
            (sx + dx, sy + dy) for sx, sy in self._last_positions
        ]
        self._known_traps = {(sx + dx, sy + dy) for sx, sy in self._known_traps}
        self._peer_positions = {
            bid: (px + dx, py + dy)
            for bid, (px, py) in self._peer_positions.items()
        }
        self._target = None
        self._path = []
        self._tiles_broadcast.clear()
        self._abs_promoted = True

    # ================================================================
    #  SCAN PROCESSING
    # ================================================================

    def _process_scan_tiles(self, ctx: BotContext) -> None:
        """Record tile types from the latest scan result."""
        if ctx.scan_result is None:
            return
        pos = self._pos
        for (dx, dy), tile_info in ctx.scan_result.tiles.items():
            c = (pos[0] + dx, pos[1] + dy)
            self._known[c] = tile_info.tile_type
            if tile_info.tile_type == TileType.TRAP:
                self._known_traps.add(c)

    # ================================================================
    #  RADIO — explore phase
    # ================================================================

    def _process_explore_inbox(self, ctx: BotContext) -> None:
        """Decode position, scan data, and traps from teammates.

        Handles both ``R`` (shared-Relative) and ``A`` (Absolute) prefixes.
        If this bot is already in absolute mode it converts incoming ``R``
        messages using the known delta; if still in shared mode it ignores
        ``A`` messages (they'll be re-sent next turn anyway).
        """
        for msg in ctx.inbox:
            if not msg.content.startswith(self._token):
                continue
            payload = msg.content[len(self._token):]
            for segment in payload.split(";"):
                segment = segment.strip()
                if len(segment) < 3:
                    continue
                prefix = segment[0]
                if prefix not in ("R", "A", "L"):
                    continue
                # Determine coordinate transform needed
                if prefix == "A":
                    if not self._abs_promoted:
                        continue
                    cdx, cdy = 0, 0
                elif prefix == "R":
                    if self._abs_promoted:
                        if self._shared_offset is None:
                            continue  # direct-localized, can't convert R
                        cdx = self._spawn_abs_x - self._shared_offset[0]  # type: ignore[operator]
                        cdy = self._spawn_abs_y - self._shared_offset[1]  # type: ignore[operator]
                    elif self._in_shared_frame:
                        cdx, cdy = 0, 0
                    else:
                        continue  # not in any compatible frame
                else:
                    cdx, cdy = 0, 0  # L-prefix handled below

                tag = segment[1]
                body = segment[2:]
                if prefix == "L":
                    # Localization delta — axis info from a peer.
                    # Format: LX:<delta> or LY:<delta>
                    try:
                        axis = segment[1]
                        delta = int(segment[3:])
                        if self._in_shared_frame and self._shared_offset is not None:
                            if axis == "X" and self._spawn_abs_x is None:
                                self._spawn_abs_x = delta + self._shared_offset[0]
                                self._shared_abs_delta_x = delta
                            elif axis == "Y" and self._spawn_abs_y is None:
                                self._spawn_abs_y = delta + self._shared_offset[1]
                                self._shared_abs_delta_y = delta
                            if self._spawn_abs_x is not None and self._spawn_abs_y is not None:
                                self._abs_localized = True
                    except (ValueError, IndexError):
                        pass
                    continue

                try:
                    if tag == "P":
                        bid_str, coords = body.split(":")
                        xs, ys = coords.split(",")
                        self._peer_positions[int(bid_str)] = (
                            int(xs) + cdx,
                            int(ys) + cdy,
                        )
                    elif tag == "Z":
                        bid_str, z = body.split(":")
                        self._peer_zones[int(bid_str)] = int(z)
                    elif tag == "T":
                        for part in body.split("|"):
                            if "," not in part:
                                continue
                            xs, ys = part.split(",")
                            tp = (int(xs) + cdx, int(ys) + cdy)
                            self._known_traps.add(tp)
                            self._known[tp] = TileType.TRAP
                    elif tag == "S":
                        for part in body.split("|"):
                            if ":" not in part:
                                continue
                            coords, c = part.rsplit(":", 1)
                            xs, ys = coords.split(",")
                            p = (int(xs) + cdx, int(ys) + cdy)
                            if c in _CHAR_TILE and p not in self._known:
                                self._known[p] = _CHAR_TILE[c]
                            if c == "T":
                                self._known_traps.add(p)
                except (ValueError, IndexError):
                    pass

    def _build_explore_messages(self, ctx: BotContext) -> list[Message]:
        """Broadcast position + scan data + traps.

        Uses ``A`` prefix when in absolute mode, ``R`` when in shared frame.
        """
        if not self._in_shared_frame and not self._abs_promoted:
            return []

        pfx = "A" if self._abs_promoted else "R"
        freq = ctx.broadcast_frequency
        pos = self._pos
        msgs: list[Message] = []

        # msg 1: position + zone + localization deltas + traps
        parts = [f"{pfx}P{self.id}:{pos[0]},{pos[1]}"]
        if self._zone_id >= 0:
            parts.append(f"{pfx}Z{self.id}:{self._zone_id}")
        # Broadcast axis deltas so all teammates can resolve axes
        if self._shared_abs_delta_x is not None:
            parts.append(f"LX:{self._shared_abs_delta_x}")
        if self._shared_abs_delta_y is not None:
            parts.append(f"LY:{self._shared_abs_delta_y}")
        if self._known_traps:
            _traps_iter = self._known_traps if len(self._known_traps) <= 20 else list(self._known_traps)[:20]
            trap_body = f"{pfx}T" + "|".join(
                f"{x},{y}" for x, y in _traps_iter
            )
            candidate = ";".join(parts) + ";" + trap_body
            if len(self._token + candidate) <= 256:
                parts.append(trap_body)
            else:
                msgs.append(Message(frequency=freq, content=(self._token + trap_body)[:256]))
        msgs.insert(0, Message(frequency=freq, content=(self._token + ";".join(parts))[:256]))

        # msg 2: scan tile data
        if ctx.scan_result is not None:
            sp: list[str] = []
            for (dx, dy), ti in ctx.scan_result.tiles.items():
                c = _TILE_CHAR.get(ti.tile_type)
                if c is None:
                    continue
                tile_pos = (pos[0] + dx, pos[1] + dy)
                sp.append(f"{tile_pos[0]},{tile_pos[1]}:{c}")
                self._tiles_broadcast.add(tile_pos)
            if sp:
                msgs.append(
                    Message(
                        frequency=freq,
                        content=(self._token + f"{pfx}S" + "|".join(sp))[:256],
                    )
                )

        # Fill remaining message slots with previously unsent tiles
        unsent = [
            (p, _TILE_CHAR[t])
            for p, t in self._known.items()
            if p not in self._tiles_broadcast and t in _TILE_CHAR
        ]
        i = 0
        while i < len(unsent) and len(msgs) < 3:
            batch = unsent[i:i + 25]
            i += 25
            parts = [f"{p[0]},{p[1]}:{c}" for p, c in batch]
            msgs.append(Message(
                frequency=freq,
                content=(self._token + f"{pfx}S" + "|".join(parts))[:256],
            ))
            for p, _ in batch:
                self._tiles_broadcast.add(p)

        return msgs[:3]

    # ================================================================
    #  SCAN CADENCE
    # ================================================================

    def _should_scan(self, ctx: BotContext) -> bool:
        # Stagger first scans across bots to avoid all scanning on same turn
        if ctx.turn_number <= 2:
            return (ctx.turn_number % 2) == (self._idx % 2)
        if self._turns_since_scan >= 3:
            return True
        pos = self._pos
        unknown = sum(
            1
            for _, (dx, dy) in _MOVES
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
            if pos == self._target or (
                self._target in self._known
                and self._known[self._target]
                in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS)
            ):
                self._target = None
                self._path = []

        if self._target is None:
            self._target = self._pick_frontier(pos)
            self._path = []

        if self._target is None:
            return self._random_passable_move(pos)

        if self._path and self._path[0] == pos:
            self._path.pop(0)
        if not self._path:
            self._path = self._dijkstra_path(pos, self._target)

        if self._path:
            act = _action_for_step(pos, self._path[0])
            if act != Action.STAY:
                return act

        return self._random_passable_move(pos)

    # ── Frontier selection ───────────────────────────────────────

    def _pick_frontier(
        self, pos: tuple[int, int]
    ) -> Optional[tuple[int, int]]:
        """Pick the best frontier tile — unknown neighbour of a known tile.

        Before absolute localisation, frontier selection is biased toward
        the bot's assigned border direction so it reaches a map edge
        quickly.  Once localised the bias is removed and pure
        peer-avoidance scoring takes over.
        """
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
                p
                for p, t in known.items()
                if t is not _OBS and t is not _OOB
                and p not in self._visited
            ]
            return self._rng.choice(unvisited) if unvisited else None

        peers = [
            p for bid, p in self._peer_positions.items() if bid != self.id
        ]

        bd = self._border_dir if not self._abs_localized else None

        # Sample if frontier is very large
        if len(frontier) > 100:
            self._rng.shuffle(frontier)
            candidates = frontier[:100]
        else:
            candidates = frontier

        # Inline scoring for speed — avoid closure + min(genexpr) overhead
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
            if bd is not None:
                s += (tx - px) * bd[0] * 3.0 + (ty - py) * bd[1] * 3.0
            if zone_active and zmin <= tx <= zmax:
                s += 5.0
            scored.append((s, tile))
        scored.sort(reverse=True, key=lambda x: x[0])
        top_n = max(1, len(scored) // 5)
        return self._rng.choice(scored[:top_n])[1]

    # ── Dijkstra pathfinding (trap-aware) ────────────────────────

    def _dijkstra_path(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """A* shortest path that penalises known trap tiles."""
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

    # ── Zone computation ─────────────────────────────────────────

    def _compute_zone(self) -> None:
        """Assign the closest zone strip not already claimed by a peer."""
        w = self._map_w
        if w == 0:
            return
        strip = max(1, w // 5)
        pos_x = self._pos[0]

        taken = set(self._peer_zones.values())

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

    # ── Stuck detection ──────────────────────────────────────────

    def _is_stuck(self) -> bool:
        if len(self._last_positions) < 6:
            return False
        return len(set(self._last_positions[-6:])) <= 3

    # ── Safe random move ─────────────────────────────────────────

    def _random_passable_move(self, pos: tuple[int, int]) -> Action:
        safe: list[Action] = []
        traps: list[Action] = []
        for action, (dx, dy) in _MOVES:
            nb = (pos[0] + dx, pos[1] + dy)
            tile = self._known.get(nb)
            if tile in (TileType.OBSTACLE, TileType.OUT_OF_BOUNDS):
                continue
            if tile == TileType.TRAP or nb in self._known_traps:
                traps.append(action)
            else:
                safe.append(action)
        if safe:
            return self._rng.choice(safe)
        if traps:
            return self._rng.choice(traps)
        return self._rng.choice([a for a, _ in _MOVES])


# ── Team registration ────────────────────────────────────────────────


@TeamRegistry.register(
    key="rendezvous",
    name="Rendezvous",
    description=(
        "Bots establish a shared coordinate frame on turn 1 via teammate "
        "detection, enabling immediate map sharing before absolute localisation."
    ),
    author="Copilot",
)
class RendezvousTeam(Team):
    """5 RendezvousBots with a shared radio frequency."""

    def __init__(
        self, default_frequency: int = 55, seed: int | None = None
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed
        self._bots: list[RendezvousBot] = []

    def initialize(self) -> list[Bot]:
        self._bots = [
            RendezvousBot(
                bot_index=i,
                rng_seed=(self._seed + i if self._seed is not None else None),
                team_id=self.team_id,
            )
            for i in range(5)
        ]
        return self._bots

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        """Merge all bots' maps into absolute coordinates.

        Bots that completed absolute promotion already have their
        ``_known`` in absolute coords.  For bots still in the shared
        frame, we compute the shared→absolute delta from any promoted
        bot and translate their tiles.  Bots that never achieved a
        shared frame are skipped (their coords are meaningless).

        Coordinates outside the map boundaries are filtered out to
        avoid penalising the team for radio-interference artefacts.
        """
        merged: dict[tuple[int, int], TileType] = {}

        # Determine map bounds (any bot will have the same values).
        map_w = map_h = 0
        for bot in self._bots:
            if bot._map_w > 0:
                map_w, map_h = bot._map_w, bot._map_h
                break

        # Try to derive the shared→absolute translation delta from any
        # promoted bot (they all share the same delta).
        abs_delta: tuple[int, int] | None = None
        for bot in self._bots:
            if bot._abs_promoted and bot._shared_offset is not None:
                assert bot._spawn_abs_x is not None and bot._spawn_abs_y is not None
                abs_delta = (
                    bot._spawn_abs_x - bot._shared_offset[0],
                    bot._spawn_abs_y - bot._shared_offset[1],
                )
                break

        _EMPTY = TileType.EMPTY
        _SPAWN = TileType.SPAWN
        for bot in self._bots:
            if bot._abs_promoted:
                # Already in absolute coordinates
                for pos, tile in bot._known.items():
                    if tile is _EMPTY or tile is _SPAWN:
                        existing = merged.get(pos)
                        if existing is None or (existing is _EMPTY and tile is _SPAWN):
                            merged[pos] = tile
            elif bot._in_shared_frame and abs_delta is not None:
                # Translate shared-frame coords to absolute
                dx, dy = abs_delta
                for (sx, sy), tile in bot._known.items():
                    apos = (sx + dx, sy + dy)
                    if tile is _EMPTY or tile is _SPAWN:
                        existing = merged.get(apos)
                        if existing is None or (existing is _EMPTY and tile is _SPAWN):
                            merged[apos] = tile

        # Filter out coordinates outside the known map boundaries.
        if map_w > 0:
            merged = {
                pos: tile
                for pos, tile in merged.items()
                if 0 <= pos[0] < map_w and 0 <= pos[1] < map_h
            }
        return merged
