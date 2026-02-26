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
    """Exploration scoring rules R20-R23."""

    def test_initial_score_at_least_one(self):
        """R23: spawn tiles are counted at initialization."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, max_turns=1)
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] >= 1

    def test_staying_does_not_increase_score(self):
        """R22: revisiting (or staying on) a tile doesn't add score."""
        teams = [StayTeam(), StayTeam()]
        game = make_small_game(teams, max_turns=10)
        result = game.run()
        for tid in result.scores:
            # Spawn tiles only
            spawn_count = len(game.game_map.spawn_positions[tid])
            assert result.scores[tid] == spawn_count

    def test_moving_increases_score(self):
        """Moving to new tiles should increase score."""
        mover = _SingleActionTeam(Action.MOVE_RIGHT)
        stayer = StayTeam()
        game = make_small_game([mover, stayer], max_turns=5)
        result = game.run()
        assert result.scores[mover.team_id] > result.scores[stayer.team_id]

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
        # Should be spawn_count + up to 5 (one right per bot)
        assert result.scores[pp.team_id] <= spawn_count + 5

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
        mover = _SingleActionTeam(Action.MOVE_RIGHT)
        stayer = StayTeam()
        game = make_small_game([mover, stayer], max_turns=5)
        result = game.run()
        assert result.ranking[0] == mover.team_id

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
        assert result.turns_played == 7

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

    def test_scores_match_visited_count(self):
        game = make_small_game(
            [_SingleActionTeam(Action.MOVE_RIGHT), StayTeam()], max_turns=5
        )
        result = game.run()
        for tid in result.scores:
            assert result.scores[tid] == len(result.visited[tid])

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
        """When multiple bots from same team visit same tile, counted once."""
        # All bots move right — they all visit the same column of tiles
        team = _SingleActionTeam(Action.MOVE_RIGHT)
        game = make_small_game([team, StayTeam()], max_turns=3)
        result = game.run()
        # Score should not count duplicates
        assert result.scores[team.team_id] == len(result.visited[team.team_id])

    def test_trap_tile_counts_for_exploration(self):
        """Trap tiles are passable and should count as visited."""
        team = RecorderTeam()
        game = make_small_game([team, StayTeam()], max_turns=6, trap_ratio=0.0)

        spawns = game.game_map.spawn_positions[team.team_id]
        sx, sy = spawns[0]
        tx, ty = sx + 1, sy
        game.game_map.tiles[tx][ty] = TileType.TRAP

        team.bots[0].action_queue = [Action.MOVE_RIGHT] + [Action.STAY] * 5
        result = game.run()
        assert (tx, ty) in result.visited[team.team_id]


# ===================================================================
# 7. N-team game-master tests
# ===================================================================


class TestNTeamGameMaster:
    """Game-master tests specific to N-team support."""

    def test_three_team_independent_scoring(self):
        """3 teams each exploring independently."""
        t1 = _SingleActionTeam(Action.MOVE_RIGHT)
        t2 = _SingleActionTeam(Action.MOVE_DOWN)
        t3 = StayTeam()
        game = make_small_game([t1, t2, t3], max_turns=5, width=20, height=20)
        result = game.run()
        # Movers should beat stayer
        assert result.scores[t1.team_id] > result.scores[t3.team_id]
        assert result.scores[t2.team_id] > result.scores[t3.team_id]

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
