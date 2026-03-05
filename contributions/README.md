# Contributing Bots to RadioGrid

## Contributed Teams

Each team lives in its own subdirectory with a dedicated `README.md`
describing its strategy and core algorithms.

| Team | Directory | Key Idea |
|---|---|---|
| **Cartographers** | [`cartographers/`](cartographers/) | Frontier exploration with shared absolute map via radio |
| **Pathfinders** | [`smart/`](smart/) | Dijkstra pathfinding around traps + zone-based territory coordination |
| **Rendezvous** | [`rendezvous/`](rendezvous/) | Bootstrap shared frame via teammate detection, then border-seeking + zones |

See each team's `README.md` for detailed strategy, radio protocol, and
algorithm descriptions.

---

## How to Contribute

1. Create a new subdirectory under `contributions/` (e.g., `contributions/my_team/`).
2. Add an `__init__.py` (can be empty) and your team module (e.g., `my_team.py`).
3. Implement a `Bot` subclass and a `Team` subclass.
4. **Register your team** using the `@TeamRegistry.register` decorator so it
   appears in both the CLI and the web UI automatically.
5. Your team's `initialize()` method must return exactly **5** bot instances.
6. Optionally add a `README.md` describing your strategy.

## Template

```
contributions/my_team/
├── __init__.py
├── my_team.py
└── README.md        # optional
```

```python
# contributions/my_team/my_team.py
from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import Action, BotContext, BotOutput, Message, TileType
from radiogrid.registry import TeamRegistry


class MyBot(Bot):
    def __init__(self):
        super().__init__()
        self._known: dict[tuple[int, int], TileType] = {}

    def decide(self, context: BotContext) -> BotOutput:
        # Your strategy here
        # Record scan results into self._known to share with the team
        return BotOutput(action=Action.STAY)


@TeamRegistry.register(
    key="my_team",
    name="My Awesome Team",
    description="A clever exploration strategy.",
    author="Your Name",
)
class MyTeam(Team):
    def __init__(self) -> None:
        super().__init__(default_frequency=42)
        self._bots: list[MyBot] = []

    def initialize(self) -> list[Bot]:
        self._bots = [MyBot() for _ in range(5)]
        return self._bots

    def get_discovered_tiles(self) -> dict[tuple[int, int], TileType]:
        """Merge all bots' known maps into one report.

        This is called by the engine every turn.  Each correct tile = +1,
        each wrong tile = -1.  Only report tiles you're confident about.
        """
        merged: dict[tuple[int, int], TileType] = {}
        for bot in self._bots:
            merged.update(bot._known)
        return merged
```

## Rules

- Your bot receives a `BotContext` each turn — this is **read-only** partial information.
- You must return a `BotOutput` with one action, 0–3 messages, and optional frequency changes.
- **Scoring is based on discovery**: implement `get_discovered_tiles()` on your `Team` to report your map knowledge. Each correct tile = +1, each wrong tile = −1, score = `max(0, correct − wrong)`. Without this method your team scores **0**.
- Do **not** access game engine internals, other bots' memory, or the full map.
- Spoofing, eavesdropping, and deception via radio are fair game.
- See the main `README.MD` for the full game specification.

## Running

```bash
# CLI
python run_game.py --team my_team --team explorer

# Web UI  (your team will appear in the dropdown automatically)
python run_ui.py
```
