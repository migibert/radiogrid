#!/usr/bin/env python3
"""CLI runner for RadioGrid games.

Usage examples:

  # 2-team game (default)
  python run_game.py

  # 3-team game with custom settings
  python run_game.py --team random --team explorer --team random \
      --width 30 --height 30 --turns 300 --seed 42

Available team types: random, explorer
"""

from __future__ import annotations

import argparse
import sys

from radiogrid.engine.game import Game
from radiogrid.registry import TeamRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a RadioGrid game between N teams."
    )
    parser.add_argument(
        "--team",
        action="append",
        dest="teams",
        metavar="TYPE",
        help=(
            "Add a team of this type. Can be specified multiple times. "
            "At least 2 teams required. Default: random vs explorer."
        ),
    )
    parser.add_argument("--width", type=int, default=20, help="Map width (default: 20)")
    parser.add_argument("--height", type=int, default=20, help="Map height (default: 20)")
    parser.add_argument("--turns", type=int, default=200, help="Max turns (default: 200)")
    parser.add_argument(
        "--obstacles", type=float, default=0.2, help="Obstacle ratio 0-1 (default: 0.2)"
    )
    parser.add_argument(
        "--traps", type=float, default=0.05, help="Trap ratio 0-1 (default: 0.05)"
    )
    parser.add_argument("--seed", type=int, default=None, help="Map seed for reproducibility")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Team discovery
    TeamRegistry.discover()
    available = TeamRegistry.keys()

    team_names = args.teams or ["random", "explorer"]

    if len(team_names) < 2:
        parser.error("At least 2 teams are required (use --team multiple times)")

    teams = []
    for name in team_names:
        name_lower = name.lower()
        try:
            teams.append(TeamRegistry.create_team(name_lower))
        except KeyError:
            parser.error(
                f"Unknown team type '{name}'. Available: {', '.join(available)}"
            )

    game = Game(
        teams=teams,
        width=args.width,
        height=args.height,
        max_turns=args.turns,
        obstacle_ratio=args.obstacles,
        trap_ratio=args.traps,
        seed=args.seed,
    )

    print(f"RadioGrid — {len(teams)} teams on {args.width}x{args.height} map, {args.turns} turns")
    print(f"Teams: {', '.join(f'Team {t.team_id} ({n})' for t, n in zip(teams, team_names))}")
    print()

    result = game.run()

    print(f"Game finished in {result.turns_played} turns.\n")

    print("=== RESULTS ===")
    for rank, tid in enumerate(result.ranking, 1):
        label = team_names[tid - 1]
        score = result.scores[tid]
        print(f"  #{rank}: Team {tid} ({label}) — {score} tiles explored")

    print()
    if result.is_draw:
        tied = [
            tid
            for tid in result.ranking
            if result.scores[tid] == result.scores[result.ranking[0]]
        ]
        tied_labels = [f"Team {t} ({team_names[t-1]})" for t in tied]
        print(f"Result: DRAW between {', '.join(tied_labels)}")
    else:
        winner_id = result.ranking[0]
        print(f"Winner: Team {winner_id} ({team_names[winner_id - 1]})")


if __name__ == "__main__":
    main()
