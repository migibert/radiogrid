# RADIO GRID
### A Bot Exploration & Communication Game
**Complete Game Specification — v1.0**

> **Game Summary:** Two or more teams of 5 bots compete to explore and map a randomly generated grid. Teams score by **reporting discovered tiles** — each turn, the engine asks every team what it believes the map looks like, and awards +1 per correct tile and −1 per incorrect tile. Bots must explore, scan, and communicate via a radio frequency system that is open to eavesdropping, spoofing, and deception — making both cartography and communication strategy central to victory.
>
> **Target audience:** developers. Each team is a code submission.

---

## Table of Contents

1. [Core Concepts](#1-core-concepts)
2. [Map](#2-map)
3. [Bots](#3-bots)
4. [Scanning](#4-scanning)
5. [Communication](#5-communication)
6. [Game Loop](#6-game-loop)
7. [Official Ruleset](#7-official-ruleset)
8. [Developer Interfaces](#8-developer-interfaces)
9. [Example Strategies](#9-example-strategies)
10. [Constraints & Limits](#10-constraints--limits)
11. [Glossary](#11-glossary)

---

## 1. Core Concepts

| Term | Definition |
|---|---|
| Map | A 2D grid of tiles, generated randomly at game start. |
| Tile | A single cell of the grid. Can be Empty, Obstacle, or Trap. |
| Bot | An autonomous agent controlled by developer code. |
| Team | A group of exactly 5 bots sharing a strategy. |
| Turn | One cycle of the game loop. All bots act simultaneously. |
| Exploration Score | The number of correctly reported tiles minus incorrectly reported tiles (min 0). |
| Frequency | An integer channel identifier used for radio communication. |
| Frozen | A bot state where the bot cannot move or scan for N turns. |
| Scan | An action that reveals the contents of the 8 surrounding tiles. |

---

## 2. Map

### 2.1 Structure

The map is a rectangular grid of `W × H` tiles. Dimensions are fixed at game start. Each tile is one of:

| Tile Type | Description |
|---|---|
| **Empty** | Passable. Can be reported for discovery score. |
| **Obstacle** | Impassable. Bots cannot enter this tile. Not counted for scoring. |
| **Trap** | Passable but dangerous. Entering this tile freezes the bot for 3 turns. |
| **Spawn** | A subtype of Empty. Where bots begin the game. Can be reported for discovery score. |

### 2.2 Generation

- The map is generated randomly at game start using a configurable seed.
- Obstacles and traps are placed randomly, subject to the constraint that all non-obstacle tiles are reachable from any spawn point.
- Both teams receive a map guaranteed to be solvable (full exploration is theoretically possible).
- Map size and density parameters (obstacle %, trap %) are configurable before the game.
- Bots do **not** receive the map at startup. They must discover it through movement and scanning.

---

## 3. Bots

### 3.1 Bot State

Each bot maintains the following state, managed by the game engine:

| Field | Type / Values |
|---|---|
| `id` | Unique integer identifier across all bots in the game. |
| `team_id` | Identifier of the team this bot belongs to. |
| `position` | `(x, y)` integer coordinates on the grid. |
| `frozen_turns_remaining` | Integer ≥ 0. If > 0, bot is frozen. |
| `broadcast_frequency` | Integer. The frequency this bot transmits on. |
| `listen_frequency` | Integer. The frequency this bot receives on. |

### 3.2 Bot Input (per turn)

At the start of each turn, before deciding an action, each bot receives a read-only `BotContext` object:

| Field | Description |
|---|---|
| `frozen_turns_remaining` | How many turns the bot remains frozen (`0` = not frozen). |
| `move_succeeded` | Whether the bot's last movement action succeeded (`true` on first turn and after non-move actions). |
| `map_width` | Width of the map in tiles. |
| `map_height` | Height of the map in tiles. |
| `inbox` | List of `Message` objects received this turn (see Section 5). |
| `scan_result` | `ScanResult` object if the bot scanned last turn, else `null`. |
| `broadcast_frequency` | Current broadcast frequency. |
| `listen_frequency` | Current listen frequency. |
| `turn_number` | Current turn (1-indexed). |
| `total_explorable_tiles` | Number of non-obstacle, non-trap tiles on the map. |

### 3.3 Bot Output (per turn)

**Each bot returns exactly one `BotOutput` object per turn** containing:

| Field | Type |
|---|---|
| `action` | One value from the `Action` enum (see 3.4). |
| `messages` | List of 0–3 `Message` objects to send (see Section 5). |
| `new_broadcast_frequency` | Optional integer. If set, changes broadcast frequency for next turn. |
| `new_listen_frequency` | Optional integer. If set, changes listen frequency for next turn. |

### 3.4 Action Enum

| Action | Effect |
|---|---|
| `MOVE_UP` | Move one tile toward decreasing Y. Blocked by obstacles and map edges. |
| `MOVE_DOWN` | Move one tile toward increasing Y. Blocked by obstacles and map edges. |
| `MOVE_LEFT` | Move one tile toward decreasing X. Blocked by obstacles and map edges. |
| `MOVE_RIGHT` | Move one tile toward increasing X. Blocked by obstacles and map edges. |
| `SCAN` | Reveal the 8 surrounding tiles. Results available next turn via `scan_result`. |
| `STAY` | Do nothing. Useful when frozen (action is ignored but output is still processed). |

### 3.5 Frozen State

- A bot that steps onto a Trap tile becomes frozen for **3 turns**.
- **A frozen bot cannot move** — its movement action is treated as `STAY`. However, a frozen bot **CAN** still scan (R10), and scan results are delivered normally the following turn.
- A frozen bot **CAN** still send and receive messages normally.
- A frozen bot **CAN** still change its broadcast and listen frequencies.
- `frozen_turns_remaining` decrements by 1 at the end of each turn. At 0, the bot is free.

---

## 4. Scanning

### 4.1 Action Cost

**Scan is a full-turn action.** A bot that chooses `SCAN` cannot move that turn. This is the core tradeoff: information vs. movement progress.

### 4.2 Scan Area

Scanning reveals the 8 tiles surrounding the bot (the 3×3 square centered on the bot, excluding the bot's own tile).

```
[ NW ][ N  ][ NE ]
[  W ][ BOT][  E ]
[ SW ][ S  ][ SE ]

Directions: N = (0,-1), S = (0,+1), W = (-1,0), E = (+1,0)
Diagonals:  NW = (-1,-1), NE = (+1,-1), SW = (-1,+1), SE = (+1,+1)

Tiles outside the map boundary report as OUT_OF_BOUNDS.
```

### 4.3 ScanResult Object

A `ScanResult` contains 8 `TileInfo` entries, keyed by direction offset `(dx, dy)`:

| Field | Description |
|---|---|
| `tiles` | Dict mapping `(dx, dy) → TileInfo` for all 8 surrounding tiles. |

Each `TileInfo` contains:

| Field | Type / Values |
|---|---|
| `tile_type` | `EMPTY`, `OBSTACLE`, `TRAP`, or `OUT_OF_BOUNDS`. |
| `bots` | List of `BotInfo` objects for all bots occupying this tile (empty list if none). |

Each `BotInfo` contains:

| Field | Description |
|---|---|
| `id` | The bot's unique id. |
| `team_id` | The team this bot belongs to. |
| `broadcast_frequency` | The frequency this bot is currently broadcasting on. |
| `listen_frequency` | The frequency this bot is currently listening on. |
| `frozen_turns_remaining` | How many turns this bot is still frozen (`0` = not frozen). |

> **Note:** `BotInfo` exposes both frequencies. This is intentional and central to the game's radio warfare mechanics. Obtaining an enemy's `listen_frequency` via scan enables targeted message injection.

---

## 5. Communication

### 5.1 Radio Model

Communication is modeled as open radio broadcasting with no built-in addressing or authentication. Any bot can transmit on any frequency. Any bot listening on that frequency receives the message.

> **Key Principle:**
> There is no such thing as a private channel by default.
> Privacy is an emergent property of using an obscure or rotating frequency.
> Security is an emergent property of application-level message authentication.
> Both are the developer's responsibility, not the game engine's.

### 5.2 Message Object

| Field | Type |
|---|---|
| `frequency` | Integer. The channel on which this message is broadcast. |
| `content` | String. Arbitrary content, max 256 characters. |
| `sender_id` | Integer or None. Convenience field — **not** verified or set by the engine. Bots may put any value here. Default `None` (undeclared). |
| `sender_team_id` | Integer or None. Convenience field — **not** verified or set by the engine. Bots may put any value here. Default `None` (undeclared). |

**Important:** The game engine does **not** inject or verify `sender_id` or `sender_team_id`. These fields exist for convenience but contain whatever value the sender chooses (default `None`). A value of `None` means the sender chose not to declare its identity. Teams that want authenticated communication must implement their own protocol (e.g. a shared secret token embedded in `content`).

### 5.3 Sending Rules

- **Maximum 3 messages per bot per turn.** Messages beyond this limit are discarded silently.
- A bot sets the `frequency` field of each outgoing message independently.
- A bot may send on any integer frequency, including frequencies it is not listening on.
- A bot may send on the enemy team's observed frequency (spoofing).

### 5.4 Receiving Rules

- **A bot listens on exactly one frequency per turn**: its current `listen_frequency`.
- A bot receives **all** messages broadcast on that frequency that turn, from any sender.
- A bot cannot selectively filter its inbox. All messages on its frequency arrive.
- Frequency changes (`new_listen_frequency`) take effect at the start of the **next** turn.

### 5.5 Message Dispatching (Game Engine)

The game engine processes messages in this order each turn:

1. Collect all outgoing messages from all bots' `BotOutput.messages`.
2. Validate each message (length, message count per bot).
3. For each bot, deliver all messages whose `frequency` matches that bot's current `listen_frequency` into its inbox.
4. A bot receives its own messages if they match its listen frequency (no self-filter).

### 5.6 Allowed Communication Strategies

The following are explicitly permitted and considered part of legitimate gameplay:

- Listening on a frequency you believe the enemy is using.
- Broadcasting false information on the enemy's frequency (spoofing).
- Coordinating frequency rotation schedules with teammates.
- Embedding authentication tokens in message content to detect forgeries.
- Acting as a stationary relay bot (`STAY` each turn, dedicated to message forwarding).
- Luring enemy bots toward traps via false broadcast data.

---

## 6. Game Loop

### 6.1 Game Parameters

| Parameter | Description |
|---|---|
| `MAP_WIDTH` | Integer. Width of the map in tiles. |
| `MAP_HEIGHT` | Integer. Height of the map in tiles. |
| `MAX_TURNS` | Integer. Game ends after this many turns. Suggested default: `200`. |
| `OBSTACLE_RATIO` | Float 0–1. Target proportion of tiles that are obstacles. |
| `TRAP_RATIO` | Float 0–1. Target proportion of non-obstacle tiles that are traps. |
| `MAP_SEED` | Optional integer. Deterministic map generation for testing. |

### 6.2 Initialization

1. Generate the map using `MAP_WIDTH`, `MAP_HEIGHT`, `OBSTACLE_RATIO`, `TRAP_RATIO`, and optional `MAP_SEED`.
2. Place each team's 5 bots at their designated spawn tiles (symmetric placement).
3. Set all bots' `frozen_turns_remaining` to `0`.
4. Set all bots' `broadcast_frequency` and `listen_frequency` to their team's default frequency.
5. Mark all spawn tiles as visited for the respective team (telemetry only).
6. Initialize discovery score to 0 for each team.

### 6.3 Turn Sequence

Each turn executes these steps in order:

1. Build each bot's `BotContext` (position, frozen state, inbox from previous dispatch, last scan result).
2. Call each bot's `decide()` method in an isolated try/except. A bot that throws is treated as returning `STAY` with no messages.
3. Validate each bot's `BotOutput`. Invalid actions are replaced with `STAY`.
4. Process movement: for each non-frozen bot choosing `MOVE_*`, attempt to move. Movement is blocked by obstacles and map edges. Traps do **not** block movement — they trigger freeze on entry.
5. Apply trap effects: any bot that just moved onto a trap tile sets `frozen_turns_remaining = 3`.
6. Update visited-tile tracking: mark newly visited tiles for each team (telemetry only).
7. Evaluate discoveries: call each team's `get_discovered_tiles()` and update scores.
8. Process scan actions: build `ScanResult` for each bot that chose `SCAN`.
9. Process communication: collect outgoing messages, dispatch to inboxes (see 5.5).
10. Apply frequency changes: update broadcast and listen frequencies for bots that requested changes.
11. Decrement `frozen_turns_remaining` for all frozen bots (minimum 0).
12. Check early termination: if any team correctly reported every explorable tile, end the game.
13. Increment turn counter. If turn counter ≥ `MAX_TURNS`, end the game.

> **Simultaneous resolution:** All bots act on the state at the start of the turn. Two bots may attempt to move into the same tile; both moves succeed (bots can share tiles).

### 6.4 Win Condition

The game ends when `MAX_TURNS` is reached **or** when a team correctly reports every explorable tile on the map (early termination). The team with the higher discovery score wins. In case of a tie, the game is declared a draw.

**Discovery score** = `max(0, correct_reports − wrong_reports)`, where the engine validates each team's `get_discovered_tiles()` report against the ground-truth map every turn. Tiles omitted from the report carry no penalty.

---

## 7. Official Ruleset

These numbered rules are the authoritative game rules. Implementation must comply with all of them.

### 7.1 Movement Rules

- **R1** — Bots move in four cardinal directions only (no diagonal movement).
- **R2** — A move into an obstacle tile or outside the map boundary is silently ignored; the bot stays in place.
- **R3** — Multiple bots may occupy the same tile simultaneously.
- **R4** — Bots do not block each other's movement.
- **R5** — A bot that moves onto a trap tile is immediately frozen for 3 turns.
- **R6** — A frozen bot's movement action is silently ignored.

### 7.2 Scan Rules

- **R7** — `SCAN` is a full-turn action. A bot that scans cannot move that turn.
- **R8** — Scan reveals all 8 tiles in the 3×3 area centered on the bot (excluding the bot's own tile).
- **R9** — Scan results are delivered at the start of the **next** turn via the `scan_result` field.
- **R10** — A frozen bot MAY scan (results are still delivered the following turn).
- **R11** — Tiles outside the map boundary appear as `OUT_OF_BOUNDS`.
- **R12** — A bot that scans detects all bots in range, including teammates and enemies.

### 7.3 Communication Rules

- **R13** — A bot may send 0 to 3 messages per turn. Additional messages are discarded.
- **R14** — A bot receives all messages broadcast on its current `listen_frequency` this turn.
- **R15** — A bot can only listen on one frequency per turn.
- **R16** — Any bot may broadcast on any frequency, including frequencies used by the enemy.
- **R17** — `sender_id` and `sender_team_id` fields are convenience fields that the engine does **not** verify or inject — they may contain any value the sender chooses.
- **R18** — A frozen bot may send and receive messages normally.
- **R19** — Frequency changes take effect at the start of the following turn.

### 7.4 Scoring Rules

- **R20** — Scoring is based entirely on the team's `get_discovered_tiles()` report — physical visits alone do **not** earn points.
- **R21** — Each correctly reported tile (position + type matches ground truth) earns **+1**.
- **R22** — Each incorrectly reported tile (wrong type or out-of-bounds) incurs **−1**.
- **R23** — The discovery score is `max(0, correct − wrong)` — it can never go negative.
- **R24** — Tiles omitted from the report carry no penalty and no reward.
- **R25** — The engine does **not** reveal which reports were correct; teams must rely on their own confidence.
- **R26** — If a team's report covers exactly every explorable tile with zero errors, the game terminates early.

### 7.5 Fairness & Anti-Cheat Rules

- **R27** — Bots must not access the game engine's internal state objects directly.
- **R28** — Bots must not access other bots' objects or internal memory.
- **R29** — Bots must not modify the `BotContext`, `ScanResult`, or `Message` objects they receive.
- **R30** — Bots may take any action within the bounds of the `BotOutput` interface.
- **R31** — Spoofing, eavesdropping, and deceptive message content are explicitly **legal**.
- **R32** — A bot that throws an unhandled exception is treated as returning `STAY` with no messages for that turn. It is not eliminated.

---

## 8. Developer Interfaces

Pseudocode is used below; actual language bindings are defined separately.

### 8.1 Team Interface

```
Team (abstract class):
    team_id: int                          # assigned by game engine
    default_frequency: int                # initial broadcast/listen frequency for all bots

    initialize() -> List[Bot]             # create and return exactly 5 bot instances

    get_discovered_tiles() -> Dict[(int,int), TileType]
        # Called by the engine at the end of every turn.
        # Return a mapping of (x, y) -> TileType for tiles you believe
        # you have discovered.  Each correct entry = +1, each wrong = -1.
        # Default returns {} (score 0).  Override to score.
```

### 8.2 Bot Interface

```
Bot (abstract class):
    id: int                               # assigned by game engine
    team_id: int                          # assigned from parent team

    decide(context: BotContext) -> BotOutput
        # Called once per turn.
        # Must return within the time limit.
        # Must not access external state.
        # Must not throw unhandled exceptions.
```

### 8.3 BotContext Object

```
BotContext (read-only):
    frozen_turns_remaining: int           # 0 = free, >0 = frozen
    move_succeeded: bool                  # true if last movement action worked
    map_width: int                        # map width in tiles
    map_height: int                       # map height in tiles
    inbox: List[Message]                  # messages received this turn
    scan_result: ScanResult | None        # result of last SCAN, or None
    broadcast_frequency: int             # current broadcast frequency
    listen_frequency: int                # current listen frequency
    turn_number: int                     # current turn (1-indexed)
    total_explorable_tiles: int          # non-obstacle, non-trap tiles on the map
```

### 8.4 BotOutput Object

```
BotOutput:
    action: Action                        # required
    messages: List[Message]               # 0-3 items; excess discarded
    new_broadcast_frequency: int | None  # optional; takes effect next turn
    new_listen_frequency: int | None     # optional; takes effect next turn
```

### 8.5 Supporting Types

```
Action (enum):
    MOVE_UP, MOVE_DOWN, MOVE_LEFT, MOVE_RIGHT, SCAN, STAY
```

```
Message:
    frequency: int                        # channel to broadcast on (outgoing)
                                          # channel received on (incoming)
    content: str                          # max 256 characters
    sender_id: Optional[int]                   # convenience; NOT verified by engine (default None)
    sender_team_id: Optional[int]                # convenience; NOT verified by engine (default None)
```

```
ScanResult:
    tiles: Dict[(int,int), TileInfo]      # keys are (dx, dy) offsets
                                          # dx, dy each in {-1, 0, +1}
                                          # (0, 0) is excluded (own tile)
```

```
TileInfo:
    tile_type: TileType                   # EMPTY, OBSTACLE, TRAP, OUT_OF_BOUNDS
    bots: List[BotInfo]                   # all bots occupying this tile (empty if none)
```

```
BotInfo:
    id: int
    team_id: int
    broadcast_frequency: int
    listen_frequency: int
    frozen_turns_remaining: int
```

---

## 9. Example Strategies

These examples illustrate the strategic depth enabled by the ruleset. They are not prescriptive.

### Level 1 — Basic Coverage
- Assign each bot a quadrant of the map.
- Bots move in a snake pattern within their zone.
- Broadcast discovered coordinates on the team frequency.

### Level 2 — Shared Map Building
- Each bot maintains a local map representation.
- Bots broadcast scan results to teammates, building a collective map.
- A coordinator bot collects the map and rebalances zone assignments dynamically.

### Level 3 — Radio Intelligence
- Dedicate one bot to scanning enemies and tracking their frequencies.
- Listen on enemy frequencies to infer their map knowledge and movement patterns.
- Rotate own team frequency on a schedule unknown to the enemy.

### Level 4 — Active Disruption
- Inject false scan results into enemy channels (spoofing).
- Broadcast fake trap locations to send enemy bots into danger.
- Authenticate own team's messages with a pre-shared token in the `content` field to detect forgeries.
- Exploit frozen enemies: scan them to learn their frequencies, then flood their channel.

---

## 10. Constraints & Limits

| Constraint | Value / Description |
|---|---|
| Bots per team | Exactly 5. |
| Messages per bot per turn | Maximum 3. Excess is silently discarded. |
| Message content length | Maximum 256 characters. Longer messages are discarded. |
| Frequency values | Any integer. No reserved range. |
| Bot `decide()` time limit | Configurable. Suggested: 100ms. Timeout treated as `STAY`. |
| Map size | Configurable. Suggested default: 20 × 20. |
| Max turns | Configurable. Suggested default: 200. |
| Bot exceptions | Caught by engine. Treated as `STAY` + no messages for that turn. |

---

## 11. Glossary

| Term | Definition |
|---|---|
| `BotContext` | The read-only input object provided to a bot each turn. |
| `BotOutput` | The output object a bot must return each turn. |
| `BotInfo` | Snapshot of a detected bot's public state, returned in scan results. |
| Exploration Score | `max(0, correct_reports − wrong_reports)` from `get_discovered_tiles()`. |
| Frozen | State preventing movement and scanning. Communication still works. |
| Frequency | Integer channel identifier for radio messages. |
| Inbox | Messages received by a bot this turn. |
| Scan | Action revealing 8 surrounding tiles. Costs the movement action for this turn. |
| `ScanResult` | Output of a `SCAN` action, delivered at the start of the next turn. |
| Spoofing | Broadcasting on an enemy's frequency with false content. Legal. |
| Trap | Tile type that freezes any bot that steps on it for 3 turns. |
| Turn | One cycle of the game loop. All bots act simultaneously. |

---

*End of Specification*

---

## Running Locally

### Prerequisites

- **Python 3.10+** (3.13 recommended)
- **pip**

### Installation

```bash
# Clone the repository
git clone https://github.com/<your-org>/RadioGrid.git
cd RadioGrid

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows

# Install the project and its dependencies
pip install -e .

# Install dev dependencies (for running tests)
pip install -e ".[dev]"
```

### Run the Web UI

```bash
python run_ui.py
```

This starts a local web server at **http://127.0.0.1:5000** and opens your browser automatically.

From the UI you can:

- Configure the map (size, obstacle/trap ratios, seed)
- Set the number of turns
- Register any number of teams (≥ 2) from a dropdown of discovered teams
- Run the game and watch a turn-by-turn replay with per-team exploration overlay, bot positions, freeze indicators, and a live scoreboard
- Control playback speed, step forward/back, or jump to any turn

Options:

```bash
python run_ui.py --port 8080          # custom port
python run_ui.py --no-browser         # don't auto-open the browser
```

### Run from the CLI

```bash
# Default: Pathfinders vs Phantoms, 20×20 map, 200 turns
python run_game.py

# Custom game
python run_game.py --team pathfinders --team phantoms --team rendezvous \
    --width 30 --height 30 --turns 300 --seed 42
```

`--team` can be repeated to add any number of teams. Available team keys are discovered automatically from `contributions/`.

### Run the Tests

```bash
pytest
```

88 black-box tests covering the bot perspective, game-master rules, team registry, and history recording.

### Contributing a Team

Create a file in `contributions/` with a `@TeamRegistry.register` decorator. See [contributions/README.md](contributions/README.md) for the full template. Your team will appear in both the CLI and the web UI automatically.
