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

### Phase 2 — Directed border-seeking + axis sharing

After bootstrap, bots 0–3 are assigned cardinal directions (N/S/W/E)
and bias their frontier selection toward their assigned border.  When
a bot detects `OUT_OF_BOUNDS` it resolves the corresponding axis and
computes the shared→absolute translation delta for that axis.

**Axis delta sharing:** Because all bots share the same relative frame,
the translation delta is identical for every bot.  It is broadcast as
an `LX:<delta>` or `LY:<delta>` radio segment so all teammates resolve
that axis instantly — only two bots (one per axis) need to actually
reach a border.  This ensures all 5 bots achieve full absolute
localisation even on very large maps (80×80+) where a single bot
couldn't reach both edges within the turn budget.

**Perpendicular redirect:** As a fallback, once a bot has resolved one
axis it switches its border direction to the perpendicular axis.  This
way it will eventually find the second border itself even if radio
sharing is disrupted.

### Phase 3 — Collaborative exploration

Once both axes are resolved a bot promotes its map to absolute
coordinates.  It then uses zone-based coordination (5 vertical
strips), Dijkstra pathfinding (trap-aware), and peer-avoidance
frontier scoring — same as the Smart Coordinators but with the 10–25
turn head start from the shared frame.

## Core algorithms

| Algorithm | Purpose |
|---|---|
| **Shared-frame bootstrap** | BFS through a sighting-offset graph (bidirectional edges from scan `BotInfo`) to compute each bot's spawn offset relative to a common origin. |
| **Gossip protocol** | Each bot broadcasts its own sightings and relays peer sightings so transitive offsets propagate in 1–2 radio rounds. |
| **Axis delta sharing** | When a bot resolves an axis, the shared→absolute delta is broadcast so all teammates localise that axis without visiting a border. |
| **Perpendicular redirect** | After resolving one axis, the bot's border direction switches to the perpendicular axis as backup. |
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
localisation state.  Localisation deltas use the `L` prefix and are
broadcast by any bot that has resolved an axis:

| Message | Format | Description |
|---|---|---|
| Position | `[R\|A]P<id>:<x>,<y>` | Bot position |
| Zone | `[R\|A]Z<id>:<zone>` | Zone assignment (0–4) |
| Axis delta | `LX:<delta>` / `LY:<delta>` | Shared→absolute translation delta for X or Y axis |
| Traps | `[R\|A]T<x>,<y>\|…` | All known trap locations |
| Scan data | `[R\|A]S<x>,<y>:<tile>\|…` | Tile types from latest scan |

## Performance

Wins 9/10 benchmark seeds against both Cartographers and Smart
Coordinators in 3-team games on 20×20 maps (200 turns).  All 5 bots
achieve full absolute localisation on maps up to 80×80+ thanks to
axis delta sharing.
