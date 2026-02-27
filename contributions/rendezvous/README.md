# Rendezvous

**Registry key:** `rendezvous`
**Author:** Copilot

Bots establish a shared coordinate frame on turn 1 via teammate
detection in scan results, enabling collaborative map sharing 10–25
turns before border-based localisation methods.

## Strategy

### Phase 1 — Bootstrap (turns 1–3)

All bots SCAN on turn 1.  Each bot identifies nearby teammates from
`BotInfo` objects in scan results and broadcasts sighting offsets via
radio gossip.  By turn 3 every bot has computed its position in a
*shared* relative frame (lowest-ID bot's spawn = origin `(0,0)`).

**Key insight:** the 5 bots spawn in a tight 3×2 cluster, so every bot
sees 1–3 teammates in its very first scan — no movement needed.

### Phase 2 — Directed border-seeking

After bootstrap, bots 0–3 are assigned cardinal directions (N/S/W/E)
and bias their frontier selection toward their assigned border.  This
produces fast absolute localisation (~8–12 turns) while still
exploring new tiles en route.

### Phase 3 — Collaborative exploration

Once a bot resolves both axes via `OUT_OF_BOUNDS` detection, it
promotes its map to absolute coordinates.  It then uses zone-based
coordination (5 vertical strips), Dijkstra pathfinding (trap-aware),
and peer-avoidance frontier scoring — same as the Smart Coordinators
but with the 10–25 turn head start from the shared frame.

## Core algorithms

| Algorithm | Purpose |
|---|---|
| **Shared-frame bootstrap** | BFS through a sighting-offset graph (bidirectional edges from scan `BotInfo`) to compute each bot's spawn offset relative to a common origin. |
| **Gossip protocol** | Each bot broadcasts its own sightings and relays peer sightings so transitive offsets propagate in 1–2 radio rounds. |
| **Directional bias** | Dot-product bonus (`×3`) toward assigned border direction in frontier scoring (disabled once localised). |
| **Dual-mode radio** | `R` prefix = shared-relative coords, `A` prefix = absolute coords. Receiver converts between frames as needed. |
| **Dijkstra pathfinding** | Weighted shortest path: trap tiles cost 5, normal tiles cost 1. Max path cost 120. |
| **Zone assignment** | 5 vertical strips, each bot claims the closest unclaimed one. +5 frontier bonus for in-zone tiles. |
| **Diagonal OOB inference** | Same as Smart Coordinators — resolve an axis from a diagonal OOB when the adjacent cardinal is in-bounds. |

## Radio protocol

### Bootstrap phase

| Message | Format | Description |
|---|---|---|
| Sighting | `B<observer_id>:<seen_id>,<dx>,<dy>\|…` | Teammate offsets from scan results |

### Explore phase

Uses `R` (shared-Relative) or `A` (Absolute) prefix depending on
localisation state:

| Message | Format | Description |
|---|---|---|
| Position | `[R\|A]P<id>:<x>,<y>` | Bot position |
| Zone | `[R\|A]Z<id>:<zone>` | Zone assignment (0–4) |
| Traps | `[R\|A]T<x>,<y>\|…` | All known trap locations |
| Scan data | `[R\|A]S<x>,<y>:<tile>\|…` | Tile types from latest scan |

## Performance

Wins 9/10 benchmark seeds against both Cartographers and Smart
Coordinators in 3-team games on 20×20 maps (200 turns).
