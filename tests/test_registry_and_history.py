"""Tests for the TeamRegistry and game history recording."""

from __future__ import annotations

import pytest

from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import Action, BotContext, BotOutput
from radiogrid.registry import TeamEntry, TeamRegistry
from tests.conftest import StayTeam, make_small_game

# ===================================================================
# Registry
# ===================================================================


class TestTeamRegistry:
    """TeamRegistry registration, lookup, and discovery."""

    def setup_method(self):
        self._backup = dict(TeamRegistry._entries)

    def teardown_method(self):
        TeamRegistry._entries = self._backup

    def test_register_and_get(self):
        @TeamRegistry.register(key="test_team", name="Test", description="d")
        class _T(Team):
            def initialize(self):
                return [_DummyBot() for _ in range(5)]

        entry = TeamRegistry.get("test_team")
        assert entry.key == "test_team"
        assert entry.name == "Test"
        assert entry.team_class is _T

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            TeamRegistry.get("nonexistent_xyz")

    def test_list_entries(self):
        entries = TeamRegistry.list_entries()
        assert isinstance(entries, list)
        for e in entries:
            assert isinstance(e, TeamEntry)

    def test_create_team(self):
        @TeamRegistry.register(key="factory_test", name="F", description="d")
        class _T(Team):
            def initialize(self):
                return [_DummyBot() for _ in range(5)]

        team = TeamRegistry.create_team("factory_test")
        assert isinstance(team, _T)

    def test_discover_finds_builtin_teams(self):
        TeamRegistry.clear()
        TeamRegistry.discover()
        keys = TeamRegistry.keys()
        assert "random" in keys
        assert "explorer" in keys

    def test_clear(self):
        TeamRegistry.clear()
        assert len(TeamRegistry.list_entries()) == 0
        # teardown will restore the backup

    def test_keys(self):
        # Ensure builtins are present (idempotent if already registered)
        TeamRegistry.discover()
        # If modules were already imported, decorators don't re-fire.
        # Force-import the modules to trigger registration.
        import importlib

        import radiogrid.bots.explorer_bot as _eb
        import radiogrid.bots.random_bot as _rb
        importlib.reload(_rb)
        importlib.reload(_eb)

        keys = TeamRegistry.keys()
        assert isinstance(keys, list)
        assert len(keys) >= 2


# ===================================================================
# Game history
# ===================================================================


class TestGameHistory:
    """Game.get_history() structure and content."""

    def test_history_has_required_keys(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=3)
        game.run()
        h = game.get_history()
        assert "map" in h
        assert "initial" in h
        assert "turns" in h
        assert "result" in h

    def test_map_dimensions(self):
        game = make_small_game([StayTeam(), StayTeam()], width=8, height=6, max_turns=1)
        game.run()
        h = game.get_history()
        assert h["map"]["width"] == 8
        assert h["map"]["height"] == 6
        assert len(h["map"]["tiles"]) == 8
        assert len(h["map"]["tiles"][0]) == 6

    def test_turns_count_matches(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=5)
        game.run()
        h = game.get_history()
        assert len(h["turns"]) == 5

    def test_initial_has_bots(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=1)
        game.run()
        h = game.get_history()
        assert len(h["initial"]["bots"]) == 10  # 2 teams × 5

    def test_initial_has_scores(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=1)
        game.run()
        h = game.get_history()
        assert "1" in h["initial"]["scores"]
        assert "2" in h["initial"]["scores"]

    def test_turn_snapshots_have_bots(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=2)
        game.run()
        h = game.get_history()
        for snap in h["turns"]:
            assert len(snap["bots"]) == 10
            assert "turn" in snap
            assert "scores" in snap
            assert "new_visits" in snap

    def test_result_structure(self):
        game = make_small_game([StayTeam(), StayTeam()], max_turns=3)
        game.run()
        h = game.get_history()
        r = h["result"]
        assert "scores" in r
        assert "ranking" in r
        assert "is_draw" in r
        assert "total_explorable" in r
        assert "fully_explored_by" in r
        assert r["turns_played"] == 3

    def test_history_is_json_serialisable(self):
        import json

        game = make_small_game([StayTeam(), StayTeam()], max_turns=3)
        game.run()
        h = game.get_history()
        dumped = json.dumps(h)
        assert isinstance(dumped, str)

    def test_bot_action_recorded(self):
        """Turn snapshots should record each bot's action."""

        class _MoverTeam(Team):
            def initialize(self):
                class _M(Bot):
                    def decide(self, ctx):
                        return BotOutput(action=Action.MOVE_RIGHT)

                return [_M() for _ in range(5)]

        game = make_small_game([_MoverTeam(), StayTeam()], max_turns=2)
        game.run()
        h = game.get_history()
        snap = h["turns"][0]
        actions = {b["id"]: b["action"] for b in snap["bots"]}
        # First 5 bots (team 1) should have MOVE_RIGHT
        for bid in range(1, 6):
            assert actions[bid] == "MOVE_RIGHT"

    def test_new_visits_accumulate(self):
        """Exploration tiles should show up in new_visits."""

        class _MoverTeam(Team):
            def initialize(self):
                class _M(Bot):
                    def decide(self, ctx):
                        return BotOutput(action=Action.MOVE_DOWN)

                return [_M() for _ in range(5)]

        game = make_small_game([_MoverTeam(), StayTeam()], max_turns=3)
        game.run()
        h = game.get_history()
        total_new = 0
        for snap in h["turns"]:
            total_new += len(snap["new_visits"].get("1", []))
        assert total_new > 0


# ===================================================================
# Helpers
# ===================================================================


class _DummyBot(Bot):
    def decide(self, context: BotContext) -> BotOutput:
        return BotOutput(action=Action.STAY)
