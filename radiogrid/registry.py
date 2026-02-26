"""Team registry for discovering and managing team implementations.

Provides a decorator-based registration system and auto-discovery of team
modules from ``radiogrid/bots/`` and ``contributions/``.

Usage::

    from radiogrid.registry import TeamRegistry

    @TeamRegistry.register(
        key="my_team",
        name="My Awesome Team",
        description="A clever exploration strategy.",
    )
    class MyTeam(Team):
        ...
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radiogrid.engine.bot_interface import Team


@dataclass(frozen=True)
class TeamEntry:
    """Metadata about a registered team implementation."""

    key: str
    name: str
    description: str
    team_class: type[Team]
    author: str = ""


class TeamRegistry:
    """Central registry for team implementations.

    Teams register themselves via the :meth:`register` class-method
    decorator.  The UI and CLI call :meth:`discover` once at startup
    to auto-import all team modules, which triggers the decorators.
    """

    _entries: dict[str, TeamEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @classmethod
    def register(
        cls,
        key: str,
        name: str,
        description: str,
        author: str = "",
    ):
        """Class decorator that registers a :class:`Team` subclass.

        Args:
            key: Short unique identifier (e.g. ``"random"``).
            name: Human-readable team name.
            description: One-line description of the team's strategy.
            author: Optional author name.
        """

        def decorator(team_class: type[Team]) -> type[Team]:
            cls._entries[key] = TeamEntry(
                key=key,
                name=name,
                description=description,
                team_class=team_class,
                author=author,
            )
            return team_class

        return decorator

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, key: str) -> TeamEntry:
        """Look up a registered team by its unique key.

        Raises:
            KeyError: If no team is registered with the given key.
        """
        if key not in cls._entries:
            raise KeyError(f"No team registered with key '{key}'")
        return cls._entries[key]

    @classmethod
    def list_entries(cls) -> list[TeamEntry]:
        """Return all registered team entries."""
        return list(cls._entries.values())

    @classmethod
    def keys(cls) -> list[str]:
        """Return all registered team keys."""
        return list(cls._entries.keys())

    # ------------------------------------------------------------------
    # Instantiation
    # ------------------------------------------------------------------

    @classmethod
    def create_team(cls, key: str, **kwargs: Any) -> Team:
        """Instantiate a team from its registry key."""
        entry = cls.get(key)
        return entry.team_class(**kwargs)

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    @classmethod
    def discover(cls) -> None:
        """Auto-import team modules from ``radiogrid/bots/`` and ``contributions/``.

        Each imported module's top-level code runs, triggering any
        ``@TeamRegistry.register`` decorators it contains.
        """
        # Built-in bots
        import radiogrid.bots as bots_pkg

        for _importer, modname, _ispkg in pkgutil.iter_modules(bots_pkg.__path__):
            try:
                importlib.import_module(f"radiogrid.bots.{modname}")
            except Exception:
                pass

        # Community contributions
        contributions_dir = Path(__file__).resolve().parent.parent / "contributions"
        if contributions_dir.is_dir():
            project_root = contributions_dir.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            for py_file in sorted(contributions_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                try:
                    importlib.import_module(f"contributions.{py_file.stem}")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    @classmethod
    def clear(cls) -> None:
        """Remove all entries.  Useful in tests."""
        cls._entries.clear()
