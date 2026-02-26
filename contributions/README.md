# Contributing Bots to RadioGrid

## How to Contribute

1. Create a new Python file in this directory (e.g., `my_team.py`).
2. Implement a `Bot` subclass and a `Team` subclass.
3. **Register your team** using the `@TeamRegistry.register` decorator so it
   appears in both the CLI and the web UI automatically.
4. Your team's `initialize()` method must return exactly **5** bot instances.

## Template

```python
from radiogrid.engine.bot_interface import Bot, Team
from radiogrid.engine.models import Action, BotContext, BotOutput, Message
from radiogrid.registry import TeamRegistry


class MyBot(Bot):
    def decide(self, context: BotContext) -> BotOutput:
        # Your strategy here
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

    def initialize(self) -> list[Bot]:
        return [MyBot() for _ in range(5)]
```

## Rules

- Your bot receives a `BotContext` each turn — this is **read-only** partial information.
- You must return a `BotOutput` with one action, 0–3 messages, and optional frequency changes.
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
