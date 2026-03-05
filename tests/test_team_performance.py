"""Performance gate tests for contributed teams.

Auto-discovers all registered teams and verifies that their bots'
``decide()`` calls complete within acceptable time budgets.

Thresholds (per ``decide()`` call):
    median  ≤  10 ms
    p99     ≤ 100 ms
    max     ≤   1  s

These limits are deliberately generous — most well-behaved bots will
run in the sub-millisecond range.  The goal is to catch accidental
O(n²) blowups, infinite loops, or expensive I/O, *not* to
micro-benchmark.

Run with ``pytest -v -s tests/test_team_performance.py`` for full
timing output.
"""

from __future__ import annotations

import time

import pytest

from radiogrid.engine.bot_interface import Bot
from radiogrid.engine.game import Game
from radiogrid.registry import TeamRegistry
from tests.conftest import StayTeam

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_GAME_WIDTH = 20
_GAME_HEIGHT = 20
_MAX_TURNS = 200
_OBSTACLE_RATIO = 0.2
_TRAP_RATIO = 0.05
_SEED = 42

# Per decide() call thresholds (seconds)
_MEDIAN_LIMIT = 0.010  # 10 ms
_P99_LIMIT = 0.100  # 100 ms
_MAX_LIMIT = 1.000  # 1 s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_data: list[float], pct: float) -> float:
    """Return the *pct*-th percentile from already-sorted data."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


class _TimingWrapper:
    """Transparent wrapper that records wall-clock time of each ``decide()`` call."""

    def __init__(self, bot: Bot) -> None:
        self._original_decide = bot.decide
        self.timings: list[float] = []
        bot.decide = self._timed_decide  # type: ignore[method-assign]

    def _timed_decide(self, context):  # noqa: ANN001
        t0 = time.perf_counter()
        result = self._original_decide(context)
        self.timings.append(time.perf_counter() - t0)
        return result


# ---------------------------------------------------------------------------
# Discovery & parametrisation
# ---------------------------------------------------------------------------

TeamRegistry.discover()
_team_keys = TeamRegistry.keys()


@pytest.mark.parametrize("team_key", _team_keys)
class TestTeamPerformance:
    """Ensure every contributed team's bots respond within time budgets."""

    def test_decide_time_within_budget(self, team_key: str) -> None:
        """Run a full game and assert per-call timing thresholds."""
        team_under_test = TeamRegistry.create_team(team_key)
        opponent = StayTeam()

        game = Game(
            teams=[team_under_test, opponent],
            width=_GAME_WIDTH,
            height=_GAME_HEIGHT,
            max_turns=_MAX_TURNS,
            obstacle_ratio=_OBSTACLE_RATIO,
            trap_ratio=_TRAP_RATIO,
            seed=_SEED,
        )

        # Attach timing wrappers to the team-under-test's bots
        # (bot instances are already created inside Game.__init__)
        wrappers: list[_TimingWrapper] = []
        for state in game._bot_states.values():
            if state.team_id == team_under_test.team_id:
                wrappers.append(_TimingWrapper(state.bot))

        game.run()

        # Aggregate all timings across the team's 5 bots
        all_timings = sorted(t for w in wrappers for t in w.timings)
        assert all_timings, "No decide() calls were recorded"

        median = _percentile(all_timings, 50)
        p99 = _percentile(all_timings, 99)
        worst = all_timings[-1]
        count = len(all_timings)
        total = sum(all_timings)

        # Printed when using pytest -s / -v
        print(
            f"\n  [{team_key}] {count} calls | "
            f"total {total:.3f}s | "
            f"median {median * 1000:.2f}ms | "
            f"p99 {p99 * 1000:.2f}ms | "
            f"max {worst * 1000:.2f}ms"
        )

        assert median <= _MEDIAN_LIMIT, (
            f"[{team_key}] median decide() time {median * 1000:.2f}ms "
            f"exceeds {_MEDIAN_LIMIT * 1000:.0f}ms limit"
        )
        assert p99 <= _P99_LIMIT, (
            f"[{team_key}] p99 decide() time {p99 * 1000:.2f}ms "
            f"exceeds {_P99_LIMIT * 1000:.0f}ms limit"
        )
        assert worst <= _MAX_LIMIT, (
            f"[{team_key}] max decide() time {worst * 1000:.2f}ms "
            f"exceeds {_MAX_LIMIT * 1000:.0f}ms limit"
        )
