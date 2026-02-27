"""RadioGrid game engine — supports any number of teams (≥ 2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.map import GameMap
from radiogrid.engine.models import (DIRECTION_VECTORS, Action, BotContext,
                                     BotInfo, BotOutput, Message, ScanResult,
                                     TeamStats, TileInfo, TileType)

# ---------------------------------------------------------------------------
# Internal per-bot state (not exposed to bots)
# ---------------------------------------------------------------------------

@dataclass
class _BotState:
    bot: Bot
    team_id: int
    x: int
    y: int
    frozen_turns_remaining: int = 0
    broadcast_frequency: int = 0
    listen_frequency: int = 0
    inbox: list[Message] = field(default_factory=list)
    scan_result: ScanResult | None = None
    last_move_succeeded: bool = True


# ---------------------------------------------------------------------------
# Public result object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameResult:
    """Outcome of a completed game.

    Attributes:
        scores: Mapping from team_id to exploration score.
        visited: Mapping from team_id to the set of distinct tiles visited.
        ranking: Team ids ordered from highest to lowest score.
                 Teams with equal scores share the same rank position but
                 appear in the list in their original registration order.
        turns_played: Total number of turns that were executed.
        is_draw: True when the top two (or more) teams are tied.
        total_explorable: Number of non-obstacle tiles on the map.
        fully_explored_by: Team id of the first team to explore all tiles,
                           or None if no team achieved full exploration.
    """

    scores: dict[int, int]
    visited: dict[int, frozenset[tuple[int, int]]]
    ranking: list[int]
    turns_played: int
    is_draw: bool
    total_explorable: int = 0
    fully_explored_by: int | None = None
    team_stats: dict[int, TeamStats] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Game engine
# ---------------------------------------------------------------------------

class Game:
    """RadioGrid game engine.

    Usage::

        game = Game(
            teams=[team_a, team_b, team_c],
            width=20, height=20, max_turns=200,
        )
        result = game.run()
    """

    def __init__(
        self,
        teams: list[Team],
        width: int = 20,
        height: int = 20,
        max_turns: int = 200,
        obstacle_ratio: float = 0.2,
        trap_ratio: float = 0.05,
        seed: int | None = None,
    ) -> None:
        if len(teams) < 2:
            raise ValueError("At least 2 teams are required")

        self.max_turns = max_turns
        self.turn = 0

        num_teams = len(teams)
        self.game_map = GameMap.generate(
            width=width,
            height=height,
            num_teams=num_teams,
            obstacle_ratio=obstacle_ratio,
            trap_ratio=trap_ratio,
            seed=seed,
        )

        # Assign team ids (1-indexed)
        self._teams: list[Team] = teams
        for idx, team in enumerate(teams):
            team.team_id = idx + 1

        # Create bots and internal state
        self._bot_states: dict[int, _BotState] = {}
        self._team_ids: list[int] = []
        bot_id_counter = 1

        for team in teams:
            tid = team.team_id
            self._team_ids.append(tid)
            bots = team.initialize()
            if len(bots) != 5:
                raise ValueError(
                    f"Team {tid} returned {len(bots)} bots (expected 5)"
                )
            spawns = self.game_map.spawn_positions[tid]
            for i, bot in enumerate(bots):
                bot.id = bot_id_counter
                bot.team_id = tid
                sx, sy = spawns[i]
                state = _BotState(
                    bot=bot,
                    team_id=tid,
                    x=sx,
                    y=sy,
                    broadcast_frequency=team.default_frequency,
                    listen_frequency=team.default_frequency,
                )
                self._bot_states[bot_id_counter] = state
                bot_id_counter += 1

        # Visited tiles per team
        self._visited: dict[int, set[tuple[int, int]]] = {}
        for tid in self._team_ids:
            self._visited[tid] = set()

        # Mark spawns as visited (R23)
        for bot_state in self._bot_states.values():
            self._visited[bot_state.team_id].add((bot_state.x, bot_state.y))

        # Count total explorable tiles (exclude obstacles AND traps).
        # Traps are passable but carry only a freeze penalty — no
        # exploration reward, so they don't count toward the goal.
        self._total_explorable = sum(
            1
            for x in range(self.game_map.width)
            for y in range(self.game_map.height)
            if self.game_map.tiles[x][y] not in (TileType.OBSTACLE, TileType.TRAP)
        )

        # Track which team (if any) first fully explores the map
        self._fully_explored_by: int | None = None

        # Per-team telemetry
        self._team_stats: dict[int, TeamStats] = {
            tid: TeamStats() for tid in self._team_ids
        }

        # History recording
        self._snapshots: list[dict] = []
        self._new_visits_this_turn: dict[int, list[tuple[int, int]]] = {
            tid: [] for tid in self._team_ids
        }
        self._initial_snapshot = self._capture_initial_state()
        self._result: GameResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> GameResult:
        """Execute the full game and return the result.

        The game ends when ``max_turns`` are exhausted **or** when a team
        has visited every explorable (non-obstacle) tile on the map.
        The team with the most explored tiles wins.
        """
        for _ in range(self.max_turns):
            self.turn += 1
            self._execute_turn()

            # Early termination: a team fully explored the map
            if self._fully_explored_by is not None:
                break

        self._result = self._build_result()
        return self._result

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    def _execute_turn(self) -> None:
        # Step 1 — Build contexts and collect decisions
        outputs: dict[int, BotOutput] = {}
        for bid, state in self._bot_states.items():
            ctx = self._build_context(state)
            try:
                out = state.bot.decide(ctx)
            except Exception:
                out = BotOutput()
            outputs[bid] = self._validate_output(out)

        # Step 2 — Movement (R1-R6)
        moved_bots: set[int] = set()
        for bid, out in outputs.items():
            state = self._bot_states[bid]
            if state.frozen_turns_remaining > 0:
                continue
            if out.action in DIRECTION_VECTORS:
                dx, dy = DIRECTION_VECTORS[out.action]
                nx, ny = state.x + dx, state.y + dy
                if self.game_map.is_passable(nx, ny):
                    state.x = nx
                    state.y = ny
                    moved_bots.add(bid)

        # Step 2b — Track move success for bot feedback
        for bid, out in outputs.items():
            state = self._bot_states[bid]
            ts = self._team_stats[state.team_id]
            if out.action in DIRECTION_VECTORS:
                state.last_move_succeeded = bid in moved_bots
                ts.moves_attempted += 1
                if bid not in moved_bots:
                    ts.moves_failed += 1
            else:
                state.last_move_succeeded = True

        # Step 2c — Track idle and scan stats
        for bid, out in outputs.items():
            state = self._bot_states[bid]
            ts = self._team_stats[state.team_id]
            if out.action == Action.SCAN:
                ts.scans_performed += 1
            elif out.action == Action.STAY and state.frozen_turns_remaining == 0:
                ts.idle_turns += 1

        # Step 3 — Trap effects (R5): only for bots that actually moved
        just_trapped: set[int] = set()
        for bid in moved_bots:
            state = self._bot_states[bid]
            if self.game_map.get_tile(state.x, state.y) == TileType.TRAP:
                state.frozen_turns_remaining = 3
                just_trapped.add(bid)
                self._team_stats[state.team_id].traps_triggered += 1

        # Step 4 — Update exploration scores (R20-R22)
        # Traps are passable but do NOT count as explored (penalty only).
        for tid in self._team_ids:
            self._new_visits_this_turn[tid].clear()
        for state in self._bot_states.values():
            tile = self.game_map.get_tile(state.x, state.y)
            if tile not in (TileType.OBSTACLE, TileType.TRAP):
                pos = (state.x, state.y)
                if pos not in self._visited[state.team_id]:
                    self._visited[state.team_id].add(pos)
                    self._new_visits_this_turn[state.team_id].append(pos)

        # Check for full map exploration
        if self._fully_explored_by is None:
            for tid in self._team_ids:
                if len(self._visited[tid]) >= self._total_explorable:
                    self._fully_explored_by = tid
                    break  # first team checked wins ties within a turn

        # Build position index for efficient scan lookups
        pos_index: dict[tuple[int, int], list[_BotState]] = {}
        for s in self._bot_states.values():
            pos_index.setdefault((s.x, s.y), []).append(s)

        # Step 5 — Scan results (built now, delivered next turn via context)
        for bid, out in outputs.items():
            state = self._bot_states[bid]
            if out.action == Action.SCAN:
                # R10: frozen bots CAN scan
                if state.frozen_turns_remaining > 0 and bid not in just_trapped:
                    # Frozen from a previous turn — still allowed to scan per R10
                    pass
                state.scan_result = self._build_scan_result(state.x, state.y, pos_index)

        # Step 6 — Communication (R13-R19)
        self._dispatch_messages(outputs)

        # Step 7 — Frequency changes (take effect for next turn)
        for bid, out in outputs.items():
            state = self._bot_states[bid]
            ts = self._team_stats[state.team_id]
            if out.new_broadcast_frequency is not None:
                state.broadcast_frequency = out.new_broadcast_frequency
                ts.frequency_changes += 1
            if out.new_listen_frequency is not None:
                state.listen_frequency = out.new_listen_frequency
                ts.frequency_changes += 1

        # Step 8 — Decrement freeze timers (skip just-trapped bots)
        for bid, state in self._bot_states.items():
            if bid in just_trapped:
                continue
            if state.frozen_turns_remaining > 0:
                state.frozen_turns_remaining -= 1
                self._team_stats[state.team_id].turns_frozen += 1

        # Step 9 — Record exploration curve
        for tid in self._team_ids:
            self._team_stats[tid].exploration_curve.append(
                len(self._visited[tid])
            )

        # Step 10 — Record snapshot for history replay
        self._record_snapshot(outputs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_context(self, state: _BotState) -> BotContext:
        """Build the read-only context snapshot for this bot.

        Absolute position is *not* exposed.  Instead the bot receives
        ``move_succeeded`` (whether its last movement action worked)
        and the map dimensions so it can self-localise via border
        detection.
        """
        # Read-and-clear scan result (one-shot delivery, R9)
        scan = state.scan_result
        state.scan_result = None

        return BotContext(
            frozen_turns_remaining=state.frozen_turns_remaining,
            move_succeeded=state.last_move_succeeded,
            map_width=self.game_map.width,
            map_height=self.game_map.height,
            inbox=list(state.inbox),
            scan_result=scan,
            broadcast_frequency=state.broadcast_frequency,
            listen_frequency=state.listen_frequency,
            turn_number=self.turn,
            total_explorable_tiles=self._total_explorable,
            team_explored_count=len(self._visited[state.team_id]),
        )

    @staticmethod
    def _validate_output(output: BotOutput) -> BotOutput:
        """Sanitize a bot's output, replacing invalid data."""
        if not isinstance(output, BotOutput):
            return BotOutput()

        action = output.action if isinstance(output.action, Action) else Action.STAY

        messages: list[Message] = []
        if isinstance(output.messages, list):
            for msg in output.messages[:3]:
                if isinstance(msg, Message) and len(msg.content) <= 256:
                    messages.append(msg)

        return BotOutput(
            action=action,
            messages=messages,
            new_broadcast_frequency=output.new_broadcast_frequency,
            new_listen_frequency=output.new_listen_frequency,
        )

    def _build_scan_result(
        self,
        cx: int,
        cy: int,
        pos_index: dict[tuple[int, int], list[_BotState]],
    ) -> ScanResult:
        """Build scan result for the 8 tiles surrounding (cx, cy)."""
        tiles: dict[tuple[int, int], TileInfo] = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                tile_type = self.game_map.get_tile(nx, ny)
                bots_here: list[BotInfo] = []
                if tile_type != TileType.OUT_OF_BOUNDS:
                    for s in pos_index.get((nx, ny), ()):
                        bots_here.append(
                            BotInfo(
                                id=s.bot.id,
                                team_id=s.team_id,
                                broadcast_frequency=s.broadcast_frequency,
                                listen_frequency=s.listen_frequency,
                                frozen_turns_remaining=s.frozen_turns_remaining,
                            )
                        )
                tiles[(dx, dy)] = TileInfo(tile_type=tile_type, bots=bots_here)
        return ScanResult(tiles=tiles)

    def _dispatch_messages(self, outputs: dict[int, BotOutput]) -> None:
        """Collect outgoing messages and deliver to matching inboxes.

        Messages are passed through exactly as the bot constructed them.
        The engine does **not** inject or verify ``sender_id`` or
        ``sender_team_id`` — teams are responsible for their own
        authentication protocols.

        Also records per-team message telemetry in ``_team_stats``.
        """
        # Clear all inboxes
        for state in self._bot_states.values():
            state.inbox = []

        # Collect messages together with the *real* team id of the sender
        outgoing: list[tuple[int, Message]] = []  # (real_team_id, msg)
        all_team_ids = set(self._team_ids)
        for bid, out in outputs.items():
            real_tid = self._bot_states[bid].team_id
            for msg in out.messages:
                outgoing.append((real_tid, msg))
                self._team_stats[real_tid].messages_sent += 1
                # Spoof detection: the declared sender_team_id is a
                # *different* valid team id — i.e. the bot is actively
                # impersonating another team.  Leaving sender_team_id at
                # None (or the bot's own team) does NOT count.
                if (msg.sender_team_id is not None
                        and msg.sender_team_id != real_tid
                        and msg.sender_team_id in all_team_ids):
                    self._team_stats[real_tid].spoofed_messages_sent += 1

        # Build frequency → listeners index for efficient delivery
        freq_index: dict[int, list[_BotState]] = {}
        for state in self._bot_states.values():
            freq_index.setdefault(state.listen_frequency, []).append(state)

        # Deliver to matching inboxes and track receive stats.
        # Receive counts are per *unique message* reaching a team, not
        # per bot‑inbox insertion (a single message heard by 5 bots on
        # the same team still counts as 1 received message for that team).
        for real_tid, msg in outgoing:
            is_spoofed = (msg.sender_team_id is not None
                          and msg.sender_team_id != real_tid
                          and msg.sender_team_id in all_team_ids)
            teams_reached: set[int] = set()
            for state in freq_index.get(msg.frequency, ()):
                state.inbox.append(msg)
                teams_reached.add(state.team_id)
            # Update per-team stats once per team per message
            for recv_tid in teams_reached:
                recv_ts = self._team_stats[recv_tid]
                if recv_tid == real_tid:
                    recv_ts.messages_received_own += 1
                else:
                    recv_ts.messages_received_cross += 1
                # A spoofed message that lands in an opponent team's
                # inbox counts as a delivered spoof for the *sender*.
                if is_spoofed and recv_tid != real_tid:
                    self._team_stats[real_tid].spoofed_messages_delivered += 1

    # ------------------------------------------------------------------
    # History recording
    # ------------------------------------------------------------------

    def _capture_initial_state(self) -> dict:
        """Capture the state after init, before any turns."""
        return {
            "bots": [
                {
                    "id": bid,
                    "team_id": st.team_id,
                    "x": st.x,
                    "y": st.y,
                    "frozen": st.frozen_turns_remaining,
                }
                for bid, st in self._bot_states.items()
            ],
            "scores": {
                str(tid): len(self._visited[tid]) for tid in self._team_ids
            },
            "visited": {
                str(tid): [[x, y] for x, y in self._visited[tid]]
                for tid in self._team_ids
            },
            "team_stats": {
                str(tid): self._team_stats[tid].snapshot_dict()
                for tid in self._team_ids
            },
        }

    def _record_snapshot(self, outputs: dict[int, BotOutput]) -> None:
        """Append a turn snapshot for history replay."""
        bots = []
        for bid, state in self._bot_states.items():
            action = outputs[bid].action.value if bid in outputs else "STAY"
            bots.append(
                {
                    "id": bid,
                    "team_id": state.team_id,
                    "x": state.x,
                    "y": state.y,
                    "frozen": state.frozen_turns_remaining,
                    "action": action,
                }
            )

        new_visits: dict[str, list[list[int]]] = {}
        for tid in self._team_ids:
            new_visits[str(tid)] = [
                [x, y] for x, y in self._new_visits_this_turn[tid]
            ]

        self._snapshots.append(
            {
                "turn": self.turn,
                "bots": bots,
                "scores": {
                    str(tid): len(self._visited[tid])
                    for tid in self._team_ids
                },
                "new_visits": new_visits,
                "team_stats": {
                    str(tid): self._team_stats[tid].snapshot_dict()
                    for tid in self._team_ids
                },
            }
        )

    def get_history(self) -> dict:
        """Return the full game history as a JSON-serialisable dict.

        Intended for the visualisation UI. Call **after** :meth:`run`.
        The ``"teams"`` key is left as an empty list — the caller
        (e.g. the web server) should enrich it with registry metadata.
        """
        result = self._result or self._build_result()
        return {
            "map": {
                "width": self.game_map.width,
                "height": self.game_map.height,
                "tiles": [
                    [
                        self.game_map.tiles[x][y].value
                        for y in range(self.game_map.height)
                    ]
                    for x in range(self.game_map.width)
                ],
            },
            "teams": [],  # enriched by the server / caller
            "initial": self._initial_snapshot,
            "turns": self._snapshots,
            "result": {
                "scores": {str(k): v for k, v in result.scores.items()},
                "ranking": result.ranking,
                "is_draw": result.is_draw,
                "turns_played": result.turns_played,
                "total_explorable": result.total_explorable,
                "fully_explored_by": result.fully_explored_by,
            },
            "team_stats": {
                str(tid): self._team_stats[tid].to_dict()
                for tid in self._team_ids
            },
        }

    def _build_result(self) -> GameResult:
        """Build the final game result after all turns are done."""
        scores: dict[int, int] = {}
        visited: dict[int, frozenset[tuple[int, int]]] = {}
        for tid in self._team_ids:
            visited[tid] = frozenset(self._visited[tid])
            scores[tid] = len(self._visited[tid])

        # Build ranking: descending by score, stable order for ties
        ranking = sorted(self._team_ids, key=lambda t: scores[t], reverse=True)

        top_score = scores[ranking[0]]
        is_draw = sum(1 for t in ranking if scores[t] == top_score) > 1

        return GameResult(
            scores=scores,
            visited=visited,
            ranking=ranking,
            turns_played=self.turn,
            is_draw=is_draw,
            total_explorable=self._total_explorable,
            fully_explored_by=self._fully_explored_by,
            team_stats={tid: self._team_stats[tid] for tid in self._team_ids},
        )
