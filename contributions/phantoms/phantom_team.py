"""Phantom Signals — Communication Intelligence Team.

Strategy overview
-----------------
**Dual-role architecture:**

*  **Explorers** (bots 0–2) perform standard frontier exploration with
   Dijkstra pathfinding and zone coordination on a private team
   frequency.

*  **Spies** (bots 3–4) alternate between the team frequency and enemy
   frequencies each turn.  Intercepted enemy map data is absorbed into
   the spy's knowledge and relayed to teammates.  Spies also broadcast
   forged disinformation on enemy frequencies.

**SIGINT (Signals Intelligence):**
  Spy bots discover enemy radio frequencies at runtime by scanning
  adjacent tiles — ``BotInfo`` metadata reveals each enemy's broadcast
  and listen frequencies.  Discovered frequencies are shared with
  teammates and used for both eavesdropping and spoofing.  When no
  enemy frequencies are known yet, spies probe random frequencies to
  search for enemy traffic.  Frequencies are tracked with recency so
  the team adapts when enemies change their frequencies.

**Template-replay forgery (PSYOPS):**
  The disinformation system follows a realistic intelligence cycle:

  1. **Scan** — discover enemy bot frequencies from ``BotInfo`` metadata.
  2. **Listen** — tune to enemy frequencies and intercept their messages.
  3. **Collect** — store raw intercepted messages as templates, building
     a per-frequency corpus of the enemy's actual message format.
  4. **Forge** — pick a random template, regex-replace every coordinate
     pair (``\\d+,\\d+``) with fake coordinates (known-passable tiles or
     random in-bounds positions), producing a message that perfectly
     matches the enemy's format but contains poisoned map data.
  5. **Impersonate** — set ``sender_team_id`` to the target team's ID
     (learned from scans) so the forged message passes any team-id
     based authenticity check.

  This approach is format-agnostic: it works against any team protocol
  because it replays the enemy's own message structure verbatim —
  only the coordinate data is mutated.

**Dynamic frequency detection:**
  When scanning adjacent tiles, bot metadata (``BotInfo``) reveals
  enemy broadcast and listen frequencies.  These are tracked per-bot
  with turn timestamps so stale frequencies age out and the team
  adapts when enemies change channels.  Discovered frequencies are
  shared with teammates via ``AF`` radio messages.  Before any
  frequencies are discovered, spy bots probe random frequencies
  across a wide range to search for enemy radio traffic.
"""

from __future__ import annotations

import random
import re
from heapq import heappop, heappush
from typing import Optional

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotOutput, Message, TileType)
from radiogrid.registry import TeamRegistry

# ── Constants ────────────────────────────────────────────────────────

TEAM_FREQ = 91                    # private internal frequency
_TOKEN = "#PHT#"                  # authentication prefix for team messages
_FREQ_STALE_AFTER = 30            # turns before a discovered freq is stale
_PROBE_RANGE = range(1, 101)      # random freq range for blind probing
_TRAP_STEP_COST = 5
_MAX_PATH_COST = 50
_MAX_TEMPLATES = 20               # max stored intercepted messages per freq
_COORD_RE = re.compile(r'\d+,\d+')  # matches coordinate pairs in messages

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


# ── Helpers ──────────────────────────────────────────────────────────


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _action_for_step(src: tuple[int, int], dst: tuple[int, int]) -> Action:
    dx, dy = dst[0] - src[0], dst[1] - src[1]
    for action, (adx, ady) in _MOVES:
        if adx == dx and ady == dy:
            return action
    return Action.STAY


# ── PhantomBot ───────────────────────────────────────────────────────


class PhantomBot(Bot):
    """Communication intelligence bot with eavesdropping and spoofing.

    Bots 0–2 are *explorers*: they listen exclusively on the team
    frequency, explore frontiers, and share map data internally.

    Bots 3–4 are *spies*: they alternate between the team frequency
    and enemy frequencies each turn, intercepting enemy broadcasts and
    relaying stolen intelligence back to the team.  All bots send
    disinformation on enemy frequencies as part of their message budget.
    """

    def __init__(self, bot_index: int, rng_seed: int | None = None) -> None:
        super().__init__()
        self._idx = bot_index
        self._rng = random.Random(rng_seed)

        # ── Role ─────────────────────────────────────────────────
        self._is_spy = bot_index >= 3  # bots 3-4 are spies

        # ── Position tracking ────────────────────────────────────
        self._rel_x: int = 0
        self._rel_y: int = 0
        self._pending_move: tuple[int, int] | None = None

        # ── Localisation ─────────────────────────────────────────
        self._spawn_abs_x: int | None = None
        self._spawn_abs_y: int | None = None
        self._map_promoted: bool = False

        # ── Map knowledge ────────────────────────────────────────
        self._known: dict[tuple[int, int], TileType] = {}
        self._known_traps: set[tuple[int, int]] = set()
        self._visited: set[tuple[int, int]] = set()

        # ── Peer tracking ────────────────────────────────────────
        self._peer_positions: dict[int, tuple[int, int]] = {}
        self._peer_zones: dict[int, int] = {}

        # ── Navigation ───────────────────────────────────────────
        self._target: Optional[tuple[int, int]] = None
        self._path: list[tuple[int, int]] = []
        self._turns_since_scan: int = 100
        self._last_positions: list[tuple[int, int]] = []

        # ── Zone ─────────────────────────────────────────────────
        self._zone_id: int = -1
        self._zone_x_min: int = 0
        self._zone_x_max: int = 0
        self._zone_computed: bool = False
        self._map_w: int = 0
        self._map_h: int = 0

        # ── Intelligence ─────────────────────────────────────────
        # Discovered enemy frequencies with the turn they were last seen.
        # {frequency: last_seen_turn}  — starts empty, filled at runtime.
        self._enemy_freq_seen: dict[int, int] = {}
        self._enemy_positions: dict[int, tuple[int, int]] = {}
        self._eavesdrop_cycle: int = bot_index - 3 if self._is_spy else 0
        self._fresh_intel: list[tuple[tuple[int, int], str]] = []
        # Buffer intercepted abs-coord tiles before our map is promoted
        self._intel_buffer: dict[tuple[int, int], TileType] = {}

        # ── Template-replay forgery ──────────────────────────────
        # Raw intercepted messages keyed by the frequency they were
        # heard on — used as templates for forging fake messages.
        self._intercepted_templates: dict[int, list[str]] = {}
        # Maps enemy frequency → team_id, learned from scan BotInfo.
        self._freq_team_id: dict[int, int] = {}


    # ── Coordinate helpers ───────────────────────────────────────

    @property
    def _loc(self) -> bool:
        return self._spawn_abs_x is not None and self._spawn_abs_y is not None

    @property
    def _pos(self) -> tuple[int, int]:
        if self._map_promoted:
            return (self._spawn_abs_x + self._rel_x,   # type: ignore[operator]
                    self._spawn_abs_y + self._rel_y)    # type: ignore[operator]
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

        # ── Information gathering ────────────────────────────────
        self._process_scan(ctx)
        self._detect_enemy_freqs(ctx)
        self._try_localize(ctx)

        # Trap self-detection
        if ctx.frozen_turns_remaining > 0:
            self._known[pos] = TileType.TRAP
            if self._map_promoted:
                self._known_traps.add(pos)

        # ── Inbox processing ────────────────────────────────────
        # The engine applies frequency changes BEFORE dispatching
        # messages, so ctx.listen_frequency correctly reflects which
        # frequency the current inbox was filled on.
        inbox_freq = ctx.listen_frequency
        if self._is_spy and inbox_freq != TEAM_FREQ:
            # Inbox contains enemy messages from eavesdropping
            self._process_intercepted(ctx, inbox_freq)
        # Always process any own-team messages in inbox
        self._process_team_inbox(ctx)

        # ── Zone ─────────────────────────────────────────────────
        if self._loc and not self._zone_computed:
            self._compute_zone()

        # ── Build messages ───────────────────────────────────────
        messages = self._build_all_messages(ctx)

        # ── Spy frequency management ────────────────────────────
        new_listen: int | None = None
        if self._is_spy:
            new_listen = self._next_listen_freq(ctx)

        # ── Action selection ─────────────────────────────────────
        if ctx.frozen_turns_remaining > 0:
            return BotOutput(action=Action.SCAN, messages=messages,
                             new_listen_frequency=new_listen)

        if self._should_scan(ctx):
            self._turns_since_scan = 0
            return BotOutput(action=Action.SCAN, messages=messages,
                             new_listen_frequency=new_listen)
        self._turns_since_scan += 1

        action = self._navigate(ctx)
        if action in DIRECTION_VECTORS:
            self._pending_move = DIRECTION_VECTORS[action]

        return BotOutput(action=action, messages=messages,
                         new_listen_frequency=new_listen)

    # ================================================================
    #  SIGNALS INTELLIGENCE (SIGINT)
    # ================================================================

    def _active_enemy_freqs(self, current_turn: int) -> set[int]:
        """Return enemy frequencies seen recently (not stale)."""
        return {
            f for f, t in self._enemy_freq_seen.items()
            if current_turn - t <= _FREQ_STALE_AFTER
        }

    def _detect_enemy_freqs(self, ctx: BotContext) -> None:
        """Learn enemy frequencies from scan results (BotInfo metadata).

        Tracks each frequency with a timestamp so stale entries age
        out when enemies change their radio channels.  Also records
        which team_id maps to which frequency for impersonation.
        """
        if ctx.scan_result is None:
            return
        pos = self._pos
        for (dx, dy), tile_info in ctx.scan_result.tiles.items():
            for bot_info in tile_info.bots:
                if bot_info.team_id != self.team_id:
                    for f in (bot_info.broadcast_frequency,
                              bot_info.listen_frequency):
                        if f != TEAM_FREQ:
                            self._enemy_freq_seen[f] = ctx.turn_number
                            self._freq_team_id[f] = bot_info.team_id
                    # Track enemy position from scan
                    self._enemy_positions[bot_info.id] = (
                        pos[0] + dx, pos[1] + dy
                    )

    def _process_intercepted(self, ctx: BotContext, inbox_freq: int) -> None:
        """Parse enemy messages for map intelligence and collect templates.

        Decodes position reports (AP), scan data (AS), and trap
        warnings (AT) from intercepted transmissions.  Only
        absolute-coordinate (A-prefixed) messages are consumed since
        shared-relative frames from other teams cannot be translated.

        Every intercepted message is also stored as a raw template for
        later forgery via :meth:`_forge_from_template`.

        Args:
            ctx: The bot's current turn context.
            inbox_freq: The frequency the inbox was actually filled
                from (tracked via ``_dispatch_freq``).
        """
        for msg in ctx.inbox:
            if msg.content.startswith(_TOKEN):
                continue  # own team handled by _process_team_inbox

            # ── Register freq as enemy (discovered via interception) ─
            # Even without a scan, receiving a non-team message on a
            # probed frequency proves enemies use it.
            self._enemy_freq_seen[inbox_freq] = ctx.turn_number
            # Learn team_id from the message metadata when available
            if (msg.sender_team_id is not None
                    and msg.sender_team_id != self.team_id):
                self._freq_team_id[inbox_freq] = msg.sender_team_id
            # Infer team_id from AP bot-id if still unknown for this freq
            if inbox_freq not in self._freq_team_id:
                inferred = self._infer_team_from_content(msg.content)
                if inferred is not None:
                    self._freq_team_id[inbox_freq] = inferred

            # ── Store as forgery template ────────────────────────
            bucket = self._intercepted_templates.setdefault(inbox_freq, [])
            if len(bucket) < _MAX_TEMPLATES:
                bucket.append(msg.content)
            else:
                # Rotate: replace a random old sample to keep variety
                bucket[self._rng.randrange(len(bucket))] = msg.content

            for segment in msg.content.split(";"):
                segment = segment.strip()
                if len(segment) < 3 or segment[0] != "A":
                    continue

                tag = segment[1]
                body = segment[2:]

                try:
                    if tag == "P":
                        # AP<id>:<x>,<y>
                        bid_str, coords = body.split(":")
                        xs, ys = coords.split(",")
                        self._enemy_positions[int(bid_str)] = (
                            int(xs), int(ys)
                        )

                    elif tag == "S":
                        # AS<x>,<y>:<tile>|...
                        for part in body.split("|"):
                            if ":" not in part:
                                continue
                            coords, c = part.rsplit(":", 1)
                            xs, ys = coords.split(",")
                            p = (int(xs), int(ys))
                            if c in _CHAR_TILE:
                                tt = _CHAR_TILE[c]
                                if self._map_promoted:
                                    if p not in self._known:
                                        self._known[p] = tt
                                        self._fresh_intel.append((p, c))
                                    if tt == TileType.TRAP:
                                        self._known_traps.add(p)
                                else:
                                    self._intel_buffer[p] = tt

                    elif tag == "T":
                        # AT<x>,<y>|...
                        for part in body.split("|"):
                            if "," not in part:
                                continue
                            xs, ys = part.split(",")
                            tp = (int(xs), int(ys))
                            if self._map_promoted:
                                self._known_traps.add(tp)
                                if tp not in self._known:
                                    self._known[tp] = TileType.TRAP
                                    self._fresh_intel.append((tp, "T"))
                            else:
                                self._intel_buffer[tp] = TileType.TRAP

                except (ValueError, IndexError):
                    pass

    def _infer_team_from_content(self, content: str) -> int | None:
        """Try to infer enemy team_id from AP bot-id in message content.

        Intercepted AP messages have the form ``AP<bot_id>:<x>,<y>``.
        Bot IDs are assigned sequentially (5 per team, 1-indexed), so
        ``team_id = (bot_id - 1) // 5 + 1``.  Returns None if no AP
        segment is found or the inferred team matches our own.
        """
        for segment in content.split(";"):
            segment = segment.strip()
            # Strip any leading token (e.g. #CRT#)
            if "#" in segment:
                # Find the payload after the last token marker
                idx = segment.rfind("#")
                segment = segment[idx + 1:]
            if len(segment) >= 3 and segment[:2] == "AP":
                try:
                    bid_str = segment[2:].split(":")[0]
                    bid = int(bid_str)
                    tid = (bid - 1) // 5 + 1
                    if tid != self.team_id:
                        return tid
                except (ValueError, IndexError):
                    pass
        return None

    def _next_listen_freq(self, ctx: BotContext) -> int:
        """Determine the spy's next listen frequency.

        Alternates between the team frequency (to receive teammate
        data) and enemy frequencies (to eavesdrop).  The two spy bots
        are offset so they target different enemy frequencies.

        When no enemy frequencies have been discovered yet, the spy
        probes a random frequency from a wide range to search for
        enemy radio traffic.
        """
        if ctx.listen_frequency == TEAM_FREQ:
            # Currently on team freq → switch to an enemy freq
            active = sorted(self._active_enemy_freqs(ctx.turn_number))
            if active:
                idx = self._eavesdrop_cycle % len(active)
                self._eavesdrop_cycle += 1
                return active[idx]
            # No known enemy freqs — probe a random frequency
            probe = self._rng.choice(
                [f for f in _PROBE_RANGE if f != TEAM_FREQ]
            )
            return probe
        else:
            # Currently on enemy freq → switch back to team
            return TEAM_FREQ

    # ================================================================
    #  DISINFORMATION (PSYOPS) — template-replay forgery
    # ================================================================

    def _build_disinfo_messages(self, ctx: BotContext) -> list[Message]:
        """Generate spoofed messages targeting enemy frequencies.

        Uses intercepted message templates when available: picks a
        real enemy message, replaces all coordinate pairs with fake
        values, and impersonates the enemy's ``sender_team_id`` so
        the target team trusts the data.

        Falls back to blind coordinate noise if no templates have
        been collected yet for a given frequency.
        """
        if not self._loc:
            return []

        target_freqs = sorted(self._active_enemy_freqs(ctx.turn_number))
        if not target_freqs:
            return []

        msgs: list[Message] = []

        # Try to forge one message per target frequency (up to slot budget)
        freq = target_freqs[
            (ctx.turn_number + self._idx) % len(target_freqs)
        ]
        msg = self._forge_from_template(freq)
        if msg is not None:
            msgs.append(msg)

        return msgs

    def _forge_from_template(self, freq: int) -> Message | None:
        """Produce a forged message for *freq* using a captured template.

        1. Pick a random intercepted message from the template pool for
           this frequency.
        2. Find every coordinate pair (``\\d+,\\d+``) in the text.
        3. Replace each coordinate with a plausible but harmful fake
           value — an empty/spawn tile the enemy has yet to visit, or
           a random in-bounds coordinate.
        4. Set ``sender_team_id`` to the team that owns the frequency
           so the enemy's authenticity checks pass.

        Returns ``None`` if no templates are available yet.
        """
        templates = self._intercepted_templates.get(freq)
        if not templates:
            return None

        template = self._rng.choice(templates)

        # Build a pool of fake coordinates to substitute in
        fake_pool = self._fake_coord_pool()
        pool_idx = 0

        def _replace_coord(match: re.Match) -> str:
            nonlocal pool_idx
            if pool_idx < len(fake_pool):
                x, y = fake_pool[pool_idx]
                pool_idx += 1
                return f"{x},{y}"
            # Pool exhausted — generate a random in-bounds coord
            rx = self._rng.randint(0, max(self._map_w - 1, 0))
            ry = self._rng.randint(0, max(self._map_h - 1, 0))
            return f"{rx},{ry}"

        forged_content = _COORD_RE.sub(_replace_coord, template)

        # Impersonate the team that owns this frequency
        target_tid = self._freq_team_id.get(freq)

        return Message(
            frequency=freq,
            content=forged_content[:256],
            sender_team_id=target_tid,
        )

    def _fake_coord_pool(self) -> list[tuple[int, int]]:
        """Build a shuffled pool of plausible fake coordinates.

        Prefers known-empty/spawn tiles (these exist and are passable)
        so the forged data looks realistic.  Falls back to random
        in-bounds coordinates when map knowledge is sparse.
        """
        _EMPTY = TileType.EMPTY
        _SPAWN = TileType.SPAWN
        pos = self._pos
        candidates: list[tuple[int, int]] = []
        # Sample from known tiles instead of iterating all
        known_items = list(self._known.items())
        if len(known_items) > 200:
            sample = self._rng.sample(known_items, 200)
        else:
            sample = known_items
        for p, t in sample:
            if (t is _EMPTY or t is _SPAWN) and p != pos:
                candidates.append(p)
                if len(candidates) >= 60:
                    break
        # Pad with random coords if we don't have enough known tiles
        if self._map_w > 0 and self._map_h > 0:
            while len(candidates) < 40:
                candidates.append((
                    self._rng.randint(0, self._map_w - 1),
                    self._rng.randint(0, self._map_h - 1),
                ))
        self._rng.shuffle(candidates)
        return candidates[:60]

    # ================================================================
    #  INTERNAL COMMUNICATIONS
    # ================================================================

    def _process_team_inbox(self, ctx: BotContext) -> None:
        """Process messages from teammates on the private frequency."""
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
                        self._peer_positions[int(bid_s)] = (
                            int(xs), int(ys)
                        )
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
                    elif tag == "F":
                        # Discovered enemy frequencies shared by teammates
                        for fs in body.split(","):
                            fs = fs.strip()
                            if fs:
                                f = int(fs)
                                if f != TEAM_FREQ:
                                    # Adopt with current turn as timestamp
                                    if f not in self._enemy_freq_seen:
                                        self._enemy_freq_seen[f] = ctx.turn_number
                                    else:
                                        self._enemy_freq_seen[f] = max(
                                            self._enemy_freq_seen[f],
                                            ctx.turn_number,
                                        )
                except (ValueError, IndexError):
                    pass

    def _build_team_messages(self, ctx: BotContext) -> list[Message]:
        """Build internal team messages on the private frequency."""
        if not self._loc:
            return []

        pos = self._pos
        msgs: list[Message] = []

        # Message 1: Position + zone + discovered freqs + traps
        parts = [f"AP{self.id}:{pos[0]},{pos[1]}"]
        if self._zone_id >= 0:
            parts.append(f"AZ{self.id}:{self._zone_id}")

        # Share all discovered enemy frequencies with teammates
        active_freqs = self._active_enemy_freqs(ctx.turn_number)
        if active_freqs:
            parts.append(
                "AF" + ",".join(str(f) for f in sorted(active_freqs))
            )

        if self._known_traps:
            _traps_iter = self._known_traps if len(self._known_traps) <= 20 else list(self._known_traps)[:20]
            trap_body = "AT" + "|".join(
                f"{x},{y}" for x, y in _traps_iter
            )
            candidate = _TOKEN + ";".join(parts) + ";" + trap_body
            if len(candidate) <= 256:
                parts.append(trap_body)
            else:
                msgs.append(Message(frequency=TEAM_FREQ,
                                    content=(_TOKEN + trap_body)[:256]))

        msgs.insert(0, Message(frequency=TEAM_FREQ,
                                content=(_TOKEN + ";".join(parts))[:256]))

        # Message 2: Scan data (own scan + relayed intercepted intel)
        scan_parts: list[str] = []
        if ctx.scan_result is not None:
            for (dx, dy), ti in ctx.scan_result.tiles.items():
                c = _TILE_CHAR.get(ti.tile_type)
                if c is not None:
                    scan_parts.append(f"{pos[0]+dx},{pos[1]+dy}:{c}")

        # Append fresh intercepted intelligence tiles
        if self._fresh_intel:
            batch = self._fresh_intel[:15]
            self._fresh_intel = self._fresh_intel[15:]
            for (ix, iy), ic in batch:
                scan_parts.append(f"{ix},{iy}:{ic}")

        if scan_parts:
            msgs.append(Message(
                frequency=TEAM_FREQ,
                content=(_TOKEN + "AS" + "|".join(scan_parts))[:256],
            ))

        return msgs[:3]

    def _build_all_messages(self, ctx: BotContext) -> list[Message]:
        """Assemble the final message list: team data + disinformation.

        Allocates up to 2 slots for team coordination and 1 slot for
        disinformation.  Remaining slots are filled greedily.
        """
        team_msgs = self._build_team_messages(ctx)
        disinfo_msgs = self._build_disinfo_messages(ctx)

        messages: list[Message] = []
        messages.extend(team_msgs[:2])
        messages.extend(disinfo_msgs[:1])

        # Fill remaining slots
        if len(messages) < 3:
            messages.extend(team_msgs[2:3])
        if len(messages) < 3:
            messages.extend(disinfo_msgs[1:2])

        return messages[:3]

    # ================================================================
    #  SCAN & LOCALISATION
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
        """Resolve absolute position from OUT_OF_BOUNDS tiles in scans.

        Uses cardinal and diagonal OOB inference to determine the
        absolute coordinate of the spawn position on each axis.
        """
        if ctx.scan_result is None:
            return
        tiles = ctx.scan_result.tiles

        # Cardinal OOB detection
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

        # Diagonal OOB inference (resolve one axis from a diagonal
        # OOB when the adjacent cardinal tile is in-bounds)
        for ddx, ddy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            d = tiles.get((ddx, ddy))
            if d is None or d.tile_type != TileType.OUT_OF_BOUNDS:
                continue
            h = tiles.get((ddx, 0))
            v = tiles.get((0, ddy))
            if (h and h.tile_type != TileType.OUT_OF_BOUNDS
                    and self._spawn_abs_y is None):
                self._spawn_abs_y = (
                    -self._rel_y if ddy == -1
                    else ctx.map_height - 1 - self._rel_y
                )
            if (v and v.tile_type != TileType.OUT_OF_BOUNDS
                    and self._spawn_abs_x is None):
                self._spawn_abs_x = (
                    -self._rel_x if ddx == -1
                    else ctx.map_width - 1 - self._rel_x
                )

        if self._loc and not self._map_promoted:
            self._promote_map()

    def _promote_map(self) -> None:
        """Convert map from relative to absolute coordinates."""
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
        self._last_positions = [
            (ox + rx, oy + ry) for rx, ry in self._last_positions
        ]
        self._target = None
        self._path = []
        self._peer_positions.clear()
        self._map_promoted = True

        # Flush intelligence buffer (intercepted abs-coord tiles
        # received before this bot finished localisation)
        for p, tt in self._intel_buffer.items():
            if p not in self._known:
                self._known[p] = tt
            if tt == TileType.TRAP:
                self._known_traps.add(p)
        self._intel_buffer.clear()

    # ================================================================
    #  ZONE
    # ================================================================

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
            s = -abs(pos_x - centre)
            if z in taken:
                s -= 100
            if s > best_score:
                best_score = s
                best_zone = z

        self._zone_id = best_zone
        self._zone_x_min = best_zone * strip
        self._zone_x_max = (
            (best_zone + 1) * strip - 1 if best_zone < 4 else w - 1
        )
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
            act = _action_for_step(pos, self._path[0])
            if act != Action.STAY:
                return act

        return self._random_safe_move(pos)

    # ── Frontier selection ───────────────────────────────────────

    def _pick_frontier(
        self, pos: tuple[int, int],
    ) -> Optional[tuple[int, int]]:
        """Pick the best frontier tile — unknown neighbour of a known tile.

        Scoring considers:
        * Distance to self (prefer closer)
        * Distance to team peers (spread out)
        * Distance to known enemy positions (avoid overlap)
        * Zone bonus (prefer own vertical strip)
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
                p for p, t in known.items()
                if t is not _OBS and t is not _OOB
                and p not in self._visited
            ]
            if unvisited:
                self._rng.shuffle(unvisited)
                return unvisited[0]
            return None

        peers = [
            p for bid, p in self._peer_positions.items()
            if bid != self.id
        ]
        enemy_locs = list(self._enemy_positions.values())

        # Score only a random sample if frontier is large
        if len(frontier) > 100:
            self._rng.shuffle(frontier)
            candidates = frontier[:100]
        else:
            candidates = frontier

        # Inline scoring for speed
        px, py = pos
        peer_coords = [(p[0], p[1]) for p in peers]
        enemy_coords = [(e[0], e[1]) for e in enemy_locs]
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
            if enemy_coords:
                me = 999_999
                for ex, ey in enemy_coords:
                    ed = abs(tx - ex) + abs(ty - ey)
                    if ed < me:
                        me = ed
                s += me * 0.5
            if zone_active and zmin <= tx <= zmax:
                s += 5.0
            scored.append((s, tile))
        scored.sort(reverse=True, key=lambda x: x[0])
        top_n = max(1, len(scored) // 5)
        return self._rng.choice(scored[:top_n])[1]

    # ── Dijkstra (trap-aware) ────────────────────────────────────

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

    # ── Stuck detection ──────────────────────────────────────────

    def _is_stuck(self) -> bool:
        if len(self._last_positions) < 6:
            return False
        return len(set(self._last_positions[-6:])) <= 3

    # ── Safe random move ─────────────────────────────────────────

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
    key="phantoms",
    name="Phantom Signals",
    description=(
        "Communication intelligence team: eavesdrops on enemy radio to "
        "steal map data, broadcasts disinformation to misdirect rivals."
    ),
    author="Copilot",
)
class PhantomTeam(Team):
    """5 PhantomBots — 3 explorers + 2 spies on a private frequency."""

    def __init__(
        self,
        default_frequency: int = TEAM_FREQ,
        seed: int | None = None,
    ) -> None:
        super().__init__(default_frequency=default_frequency)
        self._seed = seed

    def initialize(self) -> list[Bot]:
        return [
            PhantomBot(
                bot_index=i,
                rng_seed=(self._seed + i if self._seed is not None else None),
            )
            for i in range(5)
        ]
