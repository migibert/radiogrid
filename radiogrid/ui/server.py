"""Flask web application for RadioGrid game setup and replay visualisation."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from radiogrid.engine.game import Game
from radiogrid.registry import TeamRegistry

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = str(Path(__file__).parent / "templates")
_STATIC_DIR = str(Path(__file__).parent / "static")


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=_TEMPLATE_DIR,
        static_folder=_STATIC_DIR,
    )

    # Discover teams once on startup
    TeamRegistry.discover()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        """Serve the main game setup/visualisation page."""
        return render_template("index.html")

    @app.route("/api/teams")
    def api_teams():
        """Return all registered teams as JSON."""
        entries = TeamRegistry.list_entries()
        return jsonify(
            [
                {
                    "key": e.key,
                    "name": e.name,
                    "description": e.description,
                    "author": e.author,
                }
                for e in entries
            ]
        )

    @app.route("/api/run", methods=["POST"])
    def api_run():
        """Run a game with the specified configuration and return history."""
        data = request.get_json(force=True)

        team_keys: list[str] = data.get("teams", [])
        width: int = int(data.get("width", 20))
        height: int = int(data.get("height", 20))
        max_turns: int = int(data.get("max_turns", 200))
        obstacle_ratio: float = float(data.get("obstacle_ratio", 0.2))
        trap_ratio: float = float(data.get("trap_ratio", 0.05))
        seed = data.get("seed")
        seed = int(seed) if seed not in (None, "", "null") else None

        if len(team_keys) < 2:
            return jsonify({"error": "At least 2 teams are required."}), 400

        try:
            teams = [TeamRegistry.create_team(key) for key in team_keys]
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 400

        game = Game(
            teams=teams,
            width=width,
            height=height,
            max_turns=max_turns,
            obstacle_ratio=obstacle_ratio,
            trap_ratio=trap_ratio,
            seed=seed,
        )
        game.run()
        history = game.get_history()

        # Enrich teams metadata
        history["teams"] = [
            {
                "id": t.team_id,
                "key": team_keys[i],
                "name": TeamRegistry.get(team_keys[i]).name,
            }
            for i, t in enumerate(teams)
        ]

        return jsonify(history)

    return app
