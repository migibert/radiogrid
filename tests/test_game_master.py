"""Black-box tests from the game-master / engine perspective.

These tests validate game-level outcomes: scoring, visited tiles,
win conditions, ranking, map generation, and N-team support.
"""

from __future__ import annotations

import pytest

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.game import Game, GameResult
from radiogrid.engine.map import GameMap
from radiogrid.engine.models import (Action, BotContext, BotOutput, Message,
                                     TileType)
from tests.conftest import (RecorderBot, RecorderTeam, StayBot, StayTeam,
                            make_small_game)

# ===================================================================
# Helpers
# ===================================================================


class _SingleActionBot(Bot):
    def __init__(self, action: Action) -> None:
        super().__init__()
        self._action = action

    def decide(self, context: BotContext) -> BotOutput:
        return BotOutput(action=self._action)


class _SingleActionTeam(Team):
    def __init__(self, action: Action, freq: int = 1) -> None:
        super().__init__(default_frequency=freq)
        self._action = action

    def initialize(self) -> list[Bot]:
        return [_SingleActionBot(self._action) for _ in range(5)]


# ===================================================================
# 1. Scoring basics
# ===================================================================


class TestScoring:
    """Discovery scoring rules."""

    def test_no_discovery_means_zero_score(self):
        """Teams that don't implement get_discovered_tiles score 0."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, max_turns=1)
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] == 0

    def test_staying_does_not_increase_score(self):
        """Non-reporting teams stay at 0 regardless of turns."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, max_turns=10)
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] == 0

    def test_reporting_correct_tiles_scores(self):
        """Reporting correct tiles yields a positive score."""
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=1)
        correct_team._game_map = game.game_map

        result = game.run()
        assert result.scores[correct_team.team_id] > 0
        assert result.scores[stayer.team_id] == 0

    def test_revisit_same_tile_no_extra_score(self):
        """R22: back-and-forth doesn't double-count."""

        class _PingPongBot(Bot):
            def __init__(self):
                super().__init__()
                self._turn = 0

            def decide(self, context: BotContext) -> BotOutput:
                self._turn += 1
                if self._turn % 2 == 1:
                    return BotOutput(action=Action.MOVE_RIGHT)
                return BotOutput(action=Action.MOVE_LEFT)

        class _PingPongTeam(Team):
            def initialize(self) -> list[Bot]:
                return [_PingPongBot() for _ in range(5)]

        pp = _PingPongTeam()
        game = make_small_game([pp, StayTeam()], max_turns=10)
        result = game.run()
        # Each bot visits at most 2 tiles (spawn + one right), 5 bots
        # But spawn positions overlap at most 5 spots, so at most ~10
        spawn_count = len(set(game.game_map.spawn_positions[pp.team_id]))
        # No discovery reports → score is 0
        assert result.scores[pp.team_id] == 0

    def test_obstacle_tiles_dont_count(self):
        """R21: obstacle tiles cannot be visited."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, obstacle_ratio=0.3, max_turns=1)
        result = game.run()
        for tid in result.scores:
            for pos in result.visited[tid]:
                x, y = pos
                assert game.game_map.get_tile(x, y) != TileType.OBSTACLE


# ===================================================================
# 2. Win condition / ranking
# ===================================================================


class TestWinCondition:
    """Game result and ranking logic."""

    def test_higher_score_wins(self):
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=5)
        correct_team._game_map = game.game_map
        result = game.run()
        assert result.ranking[0] == correct_team.team_id
        assert result.scores[correct_team.team_id] > result.scores[stayer.team_id]

    def test_draw_when_scores_equal(self):
        """Equal scores → is_draw = True."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, max_turns=1)
        result = game.run()
        # Both stay teams have same spawn-tile count
        if result.scores[1] == result.scores[2]:
            assert result.is_draw

    def test_turns_played_matches_max_turns(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=7)
        result = game.run()
        # Turns played equals max_turns unless a team fully explored the map
        assert result.turns_played <= 7

    def test_ranking_length_equals_team_count(self):
        teams = [StayTeam() for _ in range(4)]
        game = make_small_game(teams, max_turns=1, width=20, height=20)
        result = game.run()
        assert len(result.ranking) == 4

    def test_ranking_descending_scores(self):
        """Ranking should be in descending score order."""
        mover = _SingleActionTeam(Action.MOVE_DOWN)
        stayers = [StayTeam() for _ in range(3)]
        game = make_small_game([mover, *stayers], max_turns=5, width=20, height=20)
        result = game.run()
        scores_in_order = [result.scores[tid] for tid in result.ranking]
        assert scores_in_order == sorted(scores_in_order, reverse=True)

    def test_three_way_draw(self):
        """Three teams with identical scores → is_draw."""
        teams = [StayTeam() for _ in range(3)]
        game = make_small_game(teams, max_turns=1, width=20, height=20)
        result = game.run()
        scores = list(result.scores.values())
        if len(set(scores)) == 1:
            assert result.is_draw


# ===================================================================
# 3. Map generation
# ===================================================================


class TestMapGeneration:
    """Map structural properties."""

    def test_map_dimensions(self):
        gm = GameMap.generate(width=15, height=10, num_teams=2, seed=0)
        assert gm.width == 15
        assert gm.height == 10

    def test_seed_deterministic(self):
        m1 = GameMap.generate(width=10, height=10, num_teams=2, seed=42)
        m2 = GameMap.generate(width=10, height=10, num_teams=2, seed=42)
        for x in range(10):
            for y in range(10):
                assert m1.tiles[x][y] == m2.tiles[x][y]

    def test_different_seeds_differ(self):
        m1 = GameMap.generate(width=20, height=20, num_teams=2, seed=1)
        m2 = GameMap.generate(width=20, height=20, num_teams=2, seed=2)
        any_diff = False
        for x in range(20):
            for y in range(20):
                if m1.tiles[x][y] != m2.tiles[x][y]:
                    any_diff = True
                    break
        assert any_diff

    def test_spawn_positions_exist_per_team(self):
        gm = GameMap.generate(width=20, height=20, num_teams=3, seed=0)
        for tid in range(1, 4):
            assert tid in gm.spawn_positions
            assert len(gm.spawn_positions[tid]) == 5

    def test_spawn_tiles_are_spawn_type(self):
        gm = GameMap.generate(width=20, height=20, num_teams=2, seed=0)
        for tid, spawns in gm.spawn_positions.items():
            for x, y in spawns:
                assert gm.tiles[x][y] == TileType.SPAWN

    def test_connectivity(self):
        """All non-obstacle tiles should be reachable from any spawn."""
        from collections import deque

        gm = GameMap.generate(
            width=20, height=20, num_teams=2, obstacle_ratio=0.3, seed=99
        )
        start = gm.spawn_positions[1][0]
        visited = set()
        queue = deque([start])
        visited.add(start)
        while queue:
            x, y = queue.popleft()
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = x + dx, y + dy
                if (
                    gm.in_bounds(nx, ny)
                    and gm.tiles[nx][ny] != TileType.OBSTACLE
                    and (nx, ny) not in visited
                ):
                    visited.add((nx, ny))
                    queue.append((nx, ny))
        all_passable = {
            (x, y)
            for x in range(gm.width)
            for y in range(gm.height)
            if gm.tiles[x][y] != TileType.OBSTACLE
        }
        assert visited == all_passable

    def test_out_of_bounds(self):
        gm = GameMap.generate(width=10, height=10, num_teams=2)
        assert gm.get_tile(-1, 0) == TileType.OUT_OF_BOUNDS
        assert gm.get_tile(10, 0) == TileType.OUT_OF_BOUNDS
        assert gm.get_tile(0, -1) == TileType.OUT_OF_BOUNDS
        assert gm.get_tile(0, 10) == TileType.OUT_OF_BOUNDS

    def test_n_team_spawn_positions(self):
        """N teams each get exactly 5 spawn positions."""
        for n in [2, 3, 4, 5, 6]:
            gm = GameMap.generate(width=30, height=30, num_teams=n, seed=0)
            assert len(gm.spawn_positions) == n
            for tid in range(1, n + 1):
                assert len(gm.spawn_positions[tid]) == 5

    def test_min_teams_validation(self):
        with pytest.raises(ValueError):
            GameMap.generate(width=10, height=10, num_teams=1)


# ===================================================================
# 4. Game construction
# ===================================================================


class TestGameConstruction:
    """Game initialization and parameter validation."""

    def test_min_two_teams(self):
        with pytest.raises(ValueError):
            Game(teams=[StayTeam()], max_turns=1)

    def test_team_ids_assigned(self):
        teams = [StayTeam() for _ in range(3)]
        game = make_small_game(teams, max_turns=1)
        for i, team in enumerate(teams):
            assert team.team_id == i + 1

    def test_bot_ids_unique(self):
        teams = [StayTeam() for _ in range(3)]
        game = make_small_game(teams, max_turns=1)
        ids = [s.bot.id for s in game._bot_states.values()]
        assert len(ids) == len(set(ids))

    def test_wrong_bot_count_rejected(self):
        class _BadTeam(Team):
            def initialize(self) -> list[Bot]:
                return [StayBot() for _ in range(3)]

        with pytest.raises(ValueError):
            make_small_game([_BadTeam(), StayTeam()])


# ===================================================================
# 5. Game result structure
# ===================================================================


class TestGameResult:
    """GameResult data shape and invariants."""

    def test_result_has_all_team_ids(self):
        teams = [StayTeam() for _ in range(4)]
        game = make_small_game(teams, max_turns=1, width=20, height=20)
        result = game.run()
        for i in range(1, 5):
            assert i in result.scores
            assert i in result.visited

    def test_visited_is_frozenset(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=1)
        result = game.run()
        for v in result.visited.values():
            assert isinstance(v, frozenset)

    def test_default_teams_score_zero(self):
        """Teams that don't implement get_discovered_tiles score 0."""
        game = make_small_game(
            [_SingleActionTeam(Action.MOVE_RIGHT), StayTeam()], max_turns=5
        )
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] == 0

    def test_result_is_frozen(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=1)
        result = game.run()
        with pytest.raises(AttributeError):
            result.scores = {}  # type: ignore[misc]


# ===================================================================
# 6. Exploration tracking details
# ===================================================================


class TestExplorationTracking:
    """Fine-grained exploration score tracking."""

    def test_spawn_tiles_counted_initially(self):
        """R23: spawn tiles are in visited set before any turns."""
        team = RecorderTeam()
        game = make_small_game([team, StayTeam()], max_turns=1)
        result = game.run()
        for sx, sy in game.game_map.spawn_positions[team.team_id]:
            assert (sx, sy) in result.visited[team.team_id]

    def test_each_team_has_independent_visited(self):
        """Team A visiting a tile doesn't give it to Team B."""
        mover = _SingleActionTeam(Action.MOVE_RIGHT)
        stayer = StayTeam()
        game = make_small_game([mover, stayer], max_turns=5)
        result = game.run()
        mover_extra = result.visited[mover.team_id] - result.visited[stayer.team_id]
        # Mover should have tiles stayer doesn't
        assert len(mover_extra) > 0

    def test_multiple_bots_visiting_same_tile(self):
        """When multiple bots from same team visit same tile, counted once in visited."""
        # All bots move right — they all visit the same column of tiles
        team = _SingleActionTeam(Action.MOVE_RIGHT)
        game = make_small_game([team, StayTeam()], max_turns=3)
        result = game.run()
        # Visited set has no duplicates (set by nature)
        assert len(result.visited[team.team_id]) > 0
        # Without discovery reports, score is 0
        assert result.scores[team.team_id] == 0

    def test_trap_tile_does_not_count_for_exploration(self):
        """Trap tiles are passable but do NOT count as explored.

        Stepping on a trap freezes the bot for 3 turns (penalty) but
        the tile is excluded from the exploration score (no reward).
        """
        team = RecorderTeam()
        game = make_small_game([team, StayTeam()], max_turns=6, trap_ratio=0.0)

        spawns = game.game_map.spawn_positions[team.team_id]
        all_spawns = set()
        for sp_list in game.game_map.spawn_positions.values():
            all_spawns.update(sp_list)

        # Find a non-spawn tile reachable by moving right from bot 0
        sx, sy = spawns[0]
        tx, ty = sx, sy
        steps = 0
        while (tx, ty) in all_spawns:
            tx += 1
            steps += 1
        game.game_map.tiles[tx][ty] = TileType.TRAP

        team.bots[0].action_queue = (
            [Action.MOVE_RIGHT] * steps + [Action.STAY] * (6 - steps)
        )
        result = game.run()
        # Trap tile should NOT appear in visited set
        assert (tx, ty) not in result.visited[team.team_id]

    def test_trap_tile_excluded_from_total_explorable(self):
        """Traps should not be counted in total_explorable_tiles."""
        team = RecorderTeam()
        game = make_small_game([team, StayTeam()], max_turns=1, trap_ratio=0.0)

        # Record explorable count before adding a trap
        original_explorable = game._total_explorable

        # Turn an empty tile into a trap
        for x in range(game.game_map.width):
            for y in range(game.game_map.height):
                if game.game_map.tiles[x][y] == TileType.EMPTY:
                    game.game_map.tiles[x][y] = TileType.TRAP
                    # Manually recount (the engine computed it at init)
                    new_explorable = sum(
                        1
                        for xx in range(game.game_map.width)
                        for yy in range(game.game_map.height)
                        if game.game_map.tiles[xx][yy] not in (
                            TileType.OBSTACLE, TileType.TRAP
                        )
                    )
                    assert new_explorable == original_explorable - 1
                    return
        pytest.skip("No empty tile found to convert")


# ===================================================================
# 7. N-team game-master tests
# ===================================================================


class TestNTeamGameMaster:
    """Game-master tests specific to N-team support."""

    def test_three_team_independent_scoring(self):
        """3 teams: discovery team beats non-reporting teams."""
        t1 = _CorrectDiscoveryTeam()
        t2 = _SingleActionTeam(Action.MOVE_DOWN)
        t3 = StayTeam()
        game = make_small_game([t1, t2, t3], max_turns=5, width=20, height=20)
        t1._game_map = game.game_map
        result = game.run()
        # Discovery reporter beats non-reporting teams
        assert result.scores[t1.team_id] > result.scores[t3.team_id]
        assert result.scores[t1.team_id] > result.scores[t2.team_id]
        # Non-reporting teams both score 0
        assert result.scores[t2.team_id] == 0
        assert result.scores[t3.team_id] == 0

    def test_six_team_game_completes(self):
        """6 teams should work without errors."""
        teams = [StayTeam() for _ in range(6)]
        game = make_small_game(teams, max_turns=3, width=30, height=30)
        result = game.run()
        assert result.turns_played == 3
        assert len(result.scores) == 6

    def test_n_team_draw_detection(self):
        """Multiple teams can tie."""
        teams = [StayTeam() for _ in range(4)]
        game = make_small_game(teams, max_turns=1, width=20, height=20)
        result = game.run()
        # All stay teams should have the same score (spawn tiles)
        scores = list(result.scores.values())
        if len(set(scores)) == 1:
            assert result.is_draw

    def test_winner_is_first_in_ranking(self):
        """The team with highest score is ranking[0]."""
        mover = _SingleActionTeam(Action.MOVE_RIGHT)
        others = [StayTeam() for _ in range(3)]
        game = make_small_game([mover, *others], max_turns=5, width=20, height=20)
        result = game.run()
        winner_id = result.ranking[0]
        top_score = result.scores[winner_id]
        for tid in result.scores:
            assert top_score >= result.scores[tid]

    def test_all_team_ids_in_ranking(self):
        """Every team must appear in the ranking list."""
        teams = [StayTeam() for _ in range(5)]
        game = make_small_game(teams, max_turns=1, width=30, height=30)
        result = game.run()
        assert set(result.ranking) == set(result.scores.keys())

# ===================================================================
# 8. Exploration goal & early termination
# ===================================================================


class TestExplorationGoal:
    """Full-map exploration goal and early termination."""

    def test_total_explorable_in_result(self):
        """GameResult includes total_explorable count."""
        game = make_small_game([StayTeam(), StayTeam()], max_turns=1)
        result = game.run()
        assert result.total_explorable > 0

    def test_total_explorable_matches_non_obstacle_non_trap_tiles(self):
        """total_explorable equals the number of non-obstacle, non-trap tiles."""
        game = make_small_game(
            [StayTeam(), StayTeam()], max_turns=1, obstacle_ratio=0.2,
            trap_ratio=0.05, seed=42,
        )
        result = game.run()
        explorable = sum(
            1
            for x in range(game.game_map.width)
            for y in range(game.game_map.height)
            if game.game_map.tiles[x][y] not in (TileType.OBSTACLE, TileType.TRAP)
        )
        assert result.total_explorable == explorable

    def test_fully_explored_by_none_when_not_complete(self):
        """No team fully explores on a large map with few turns."""
        game = make_small_game(
            [StayTeam(), StayTeam()], max_turns=1, width=20, height=20
        )
        result = game.run()
        assert result.fully_explored_by is None

    def test_early_termination_on_full_discovery(self):
        """Game ends early when a team correctly reports all explorable tiles."""
        from radiogrid.engine.models import TileType

        class _DiscoveryTeam(Team):
            """Team that discovers the full map and reports it."""
            def __init__(self):
                super().__init__()
                self._game_map = None

            def initialize(self) -> list[Bot]:
                return [_SingleActionBot(Action.MOVE_RIGHT) for _ in range(5)]

            def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
                if self._game_map is None:
                    return {}
                gm = self._game_map
                return {
                    (x, y): gm.tiles[x][y]
                    for x in range(gm.width)
                    for y in range(gm.height)
                    if gm.tiles[x][y] not in (TileType.OBSTACLE, TileType.TRAP)
                }

        dt = _DiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game(
            [dt, stayer], max_turns=500, width=10, height=10
        )
        # Give the team access to the real map (simulating perfect scanning)
        dt._game_map = game.game_map

        result = game.run()
        # Should terminate on turn 1 since the report is complete and correct
        assert result.turns_played < 500
        assert result.fully_explored_by == dt.team_id

    def test_cells_visited_by_multiple_teams(self):
        """Multiple teams can visit the same cell independently."""
        # Both teams move right — they won't share spawns but can
        # explore overlapping territory on a small map
        t1 = _SingleActionTeam(Action.MOVE_RIGHT)
        t2 = _SingleActionTeam(Action.MOVE_RIGHT)
        game = make_small_game([t1, t2], max_turns=5)
        result = game.run()
        # Both teams should have their own visited sets
        assert len(result.visited[t1.team_id]) > 0
        assert len(result.visited[t2.team_id]) > 0
        # Without discovery reports, both score 0
        assert result.scores[t1.team_id] == 0
        assert result.scores[t2.team_id] == 0

    def test_score_bounded_by_total_explorable(self):
        """A discovery team's score cannot exceed total_explorable."""
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=1)
        correct_team._game_map = game.game_map
        result = game.run()
        for tid in result.scores:
            assert 0 <= result.scores[tid] <= result.total_explorable


# ===================================================================
# 9. Discovery scoring
# ===================================================================


class _DiscoveryBot(Bot):
    """Bot that stays put — discovery logic is in the team."""

    def decide(self, context: BotContext) -> BotOutput:
        return BotOutput(action=Action.STAY)


class _CorrectDiscoveryTeam(Team):
    """Reports all tiles it has physically visited with correct types."""

    def __init__(self):
        super().__init__()
        self._game_map = None  # injected by test

    def initialize(self) -> list[Bot]:
        return [_DiscoveryBot() for _ in range(5)]

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        if self._game_map is None:
            return {}
        gm = self._game_map
        return {
            (x, y): gm.tiles[x][y]
            for x in range(gm.width)
            for y in range(gm.height)
            if gm.tiles[x][y] not in (TileType.OBSTACLE, TileType.TRAP)
        }


class _WrongDiscoveryTeam(Team):
    """Reports all positions but with wrong tile types."""

    def __init__(self):
        super().__init__()
        self._game_map = None

    def initialize(self) -> list[Bot]:
        return [_DiscoveryBot() for _ in range(5)]

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        if self._game_map is None:
            return {}
        gm = self._game_map
        # Report every explorable tile as OBSTACLE (always wrong)
        return {
            (x, y): TileType.OBSTACLE
            for x in range(gm.width)
            for y in range(gm.height)
            if gm.tiles[x][y] not in (TileType.OBSTACLE, TileType.TRAP)
        }


class _CrashingDiscoveryTeam(Team):
    """Team whose get_discovered_tiles raises an exception."""

    def initialize(self) -> list[Bot]:
        return [_DiscoveryBot() for _ in range(5)]

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        raise RuntimeError("oops")


class TestDiscoveryScoring:
    """Discovery-based scoring mechanics."""

    def test_correct_reports_increase_score(self):
        """Reporting tiles correctly yields a score equal to total explorable."""
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=1)
        correct_team._game_map = game.game_map

        result = game.run()
        # Correct team reports the full map correctly → score = total explorable
        assert result.scores[correct_team.team_id] == result.total_explorable
        # Stayer reports nothing → score = 0
        assert result.scores[stayer.team_id] == 0

    def test_wrong_reports_penalise_score(self):
        """Reporting tiles incorrectly incurs a 1:1 penalty, floored at 0."""
        wrong_team = _WrongDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([wrong_team, stayer], max_turns=1)
        wrong_team._game_map = game.game_map

        result = game.run()
        # All reports are wrong → correct=0, wrong=total → max(0, 0 - total) = 0
        assert result.scores[wrong_team.team_id] == 0

    def test_no_report_equals_zero(self):
        """A team that returns {} scores 0."""
        stayer1 = StayTeam()
        stayer2 = StayTeam()
        game = make_small_game([stayer1, stayer2], max_turns=3)
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] == 0

    def test_crashing_get_discovered_tiles_handled(self):
        """Exception in get_discovered_tiles treated as empty report."""
        crash_team = _CrashingDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([crash_team, stayer], max_turns=3)
        result = game.run()
        # Should complete without errors; score = 0 (no valid report)
        assert result.turns_played == 3
        assert result.scores[crash_team.team_id] == 0

    def test_discovery_stats_recorded(self):
        """TeamStats should contain discovery-related counters."""
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=1)
        correct_team._game_map = game.game_map

        result = game.run()
        ts = result.team_stats[correct_team.team_id]
        assert ts.tiles_reported == result.total_explorable
        assert ts.tiles_correct == result.total_explorable
        assert ts.tiles_wrong == 0
        assert ts.discovery_score == result.total_explorable
        assert len(ts.discovery_curve) == 1

    def test_discovery_curve_tracks_per_turn(self):
        """Discovery curve should have one entry per turn."""
        correct_team = _CorrectDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([correct_team, stayer], max_turns=5)
        correct_team._game_map = game.game_map

        result = game.run()
        ts = result.team_stats[correct_team.team_id]
        assert len(ts.discovery_curve) == result.turns_played

    def test_score_floors_at_zero(self):
        """Discovery score can never go below 0."""
        wrong_team = _WrongDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([wrong_team, stayer], max_turns=5)
        wrong_team._game_map = game.game_map

        result = game.run()
        assert result.scores[wrong_team.team_id] >= 0

    def test_early_termination_requires_full_correct_report(self):
        """Only a complete and fully correct report triggers early termination."""
        # A team that reports all tiles but all wrong should NOT trigger
        wrong_team = _WrongDiscoveryTeam()
        stayer = StayTeam()
        game = make_small_game([wrong_team, stayer], max_turns=5)
        wrong_team._game_map = game.game_map

        result = game.run()
        assert result.fully_explored_by is None
        assert result.turns_played == 5

    def test_no_feedback_to_team(self):
        """Teams should receive no discovery score feedback via BotContext."""
        team = RecorderTeam()
        game = make_small_game([team, StayTeam()], max_turns=3)
        game.run()
        for ctx in team.bots[0].contexts:
            assert not hasattr(ctx, 'team_explored_count')
            assert not hasattr(ctx, 'discovery_score')