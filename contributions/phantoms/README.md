# Phantom Signals

**Registry key:** `phantoms`
**Author:** Copilot

Communication intelligence team that eavesdrops on enemy radio
frequencies to steal map data and broadcasts disinformation to
misdirect rival teams' exploration.

## Strategy

### Dual-role Architecture

| Role | Bots | Description |
|---|---|---|
| **Explorer** | 0–2 | Standard frontier exploration on the private team frequency (91). Share map data, receive intel from spies. |
| **Spy** | 3–4 | Alternate between team frequency and enemy frequencies each turn. Intercept enemy broadcasts, relay stolen intelligence, and transmit disinformation. |

### Phase 1 — Localisation

All bots self-localise via `OUT_OF_BOUNDS` detection in cardinal and
diagonal scan results (same technique as Pathfinders).  Before
localisation, bots explore autonomously in spawn-relative coordinates.

Spy bots begin eavesdropping from turn 1.  Intercepted absolute-
coordinate data is buffered until the spy's own map is promoted to
absolute coordinates, then flushed into the shared knowledge base.

### Phase 2 — Intelligence & Exploration

Once localised, the team operates on two fronts simultaneously:

**SIGINT (Signals Intelligence):**
Spy bots discover enemy frequencies at runtime by scanning adjacent
tiles — `BotInfo` metadata reveals each enemy bot's broadcast and
listen frequencies.  Discovered frequencies are tracked with recency
timestamps so the team adapts when enemies change channels (stale
frequencies age out after 30 turns).  Before any frequencies are
discovered, spies probe random frequencies across a wide range
(1–100) to search for enemy radio traffic.  Intercepted
`A`-prefixed messages are decoded:

- **Position reports** (`AP`) → tracked in `_enemy_positions`
- **Scan data** (`AS`) → merged into the spy's `_known` map
- **Trap warnings** (`AT`) → added to `_known_traps`

Stolen tile data is relayed to teammates via the standard scan-data
message format, so explorers benefit transparently.

**PSYOPS (Disinformation):**
Every bot dedicates one of its three message slots to broadcasting
forged data on a rotating enemy frequency:

| Disinfo Type | Format | Effect |
|---|---|---|
| **Fake obstacles** | `AS<x>,<y>:O\|…` | Enemy pathfinding treats open tiles as impassable walls |
| **Fake traps** | `AT<x>,<y>\|…` | Enemy Dijkstra assigns cost 5 to safe tiles, causing detours |
| **Ghost positions** | `AP<id>:<x>,<y>;…` | Enemy frontier scoring avoids "covered" areas that are actually empty |

Disinformation targets rotate by turn number and bot index so
different bots hit different enemy frequencies simultaneously.

### Phase 3 — Collaborative Exploration

Standard frontier exploration with:
- **Dijkstra pathfinding** (trap-aware, cost 5 for trap tiles)
- **Zone-based coordination** (5 vertical strips, one per bot)
- **Peer-avoidance scoring** augmented with enemy position data
- **Enemy-avoidance bonus** that steers bots away from areas already
  being covered by enemy teams (mild 0.5× weight)

## Core Algorithms

| Algorithm | Purpose |
|---|---|
| **Frequency scanning** | `BotInfo` from scan results reveals enemy `broadcast_frequency` and `listen_frequency`. Each frequency is tracked with a turn timestamp; entries older than 30 turns are considered stale and dropped. Discovered frequencies are shared via `AF` messages. Before any are known, spies probe random frequencies in range 1–100. |
| **Protocol decoding** | Spy inbox parser recognises the standard `A`-prefixed message formats used by all existing teams (AP, AS, AT, AZ). |
| **Intelligence buffering** | Intercepted tiles are stored in `_intel_buffer` (abs coords) until the spy promotes its map, then flushed into `_known`. |
| **Intel relay** | Fresh intercepted tiles are appended to the spy's scan-data broadcast (up to 15 tiles per turn), transparently sharing stolen knowledge with teammates. |
| **Disinformation rotation** | `(turn + bot_index) % 3` selects disinfo type; `(turn + bot_index) % len(freqs)` selects target frequency, ensuring coverage diversity. |
| **Dijkstra pathfinding** | Weighted shortest path: trap tiles cost 5, normal tiles cost 1. Max path cost 120. |
| **Zone assignment** | 5 vertical strips. Each bot claims the closest unclaimed one. +5 frontier bonus for in-zone tiles. |

## Radio Protocol

### Internal (frequency 91)

| Message | Format | Description |
|---|---|---|
| Position | `AP<id>:<x>,<y>` | Bot's absolute position |
| Zone | `AZ<id>:<zone>` | Zone assignment (0–4) |
| Traps | `AT<x>,<y>\|…` | All known trap locations |
| Scan data | `AS<x>,<y>:<tile>\|…` | Tile types from scan + intercepted intel |
| Frequencies | `AF<freq>,…` | Dynamically discovered enemy frequencies |

### Disinformation (enemy frequencies)

| Message | Format | Target |
|---|---|---|
| Fake obstacles | `AS<x>,<y>:O\|…` | Up to 25 fake obstacle tiles per message |
| Fake traps | `AT<x>,<y>\|…` | Up to 35 fake trap coordinates per message |
| Ghost positions | `AP<id>:<x>,<y>;…` | 4 fake bot positions near map centre |

## Spy Frequency Cycle

When enemy frequencies are known:
```
Spy 3:  team → enemy[0] → team → enemy[1] → team → enemy[2] → ...
Spy 4:  team → enemy[1] → team → enemy[2] → team → enemy[0] → ...
```

When no enemy frequencies are known yet:
```
Spy 3:  team → random_probe → team → random_probe → ...
Spy 4:  team → random_probe → team → random_probe → ...
```

The two spies are offset so they target different enemy frequencies
on the same turn, maximising intelligence coverage.  Frequencies that
haven't been re-observed for 30 turns are dropped from the active
set, so the team adapts when enemies change their radio channels.

## Effectiveness Notes

**Eavesdropping** works unconditionally against all teams — any data
broadcast on a discovered frequency can be intercepted and decoded.
No prior knowledge of enemy frequencies is assumed; all discovery
happens at runtime via `BotInfo` scan metadata and random probing.

**Disinformation** is effective against teams that do not implement
robust authentication in their radio protocol.  Because the game
engine does not inject or verify `sender_id` / `sender_team_id`,
any team relying solely on these fields will accept forged messages.
Teams that embed a shared secret token in message content (as the
existing teams do with `#CRT#`, `#PTH#`, `#RDV#`) are immune to
simple spoofing — but a sufficiently advanced adversary could
attempt to discover those tokens through interception.

The primary competitive advantage comes from the intelligence layer:
by harvesting enemy scan data and positions, Phantoms effectively
leverage all teams' exploration work — gaining map knowledge from
up to 15 bots while only fielding 5.
