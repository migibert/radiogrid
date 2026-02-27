# Cartographers

**Registry key:** `cartographers`
**Author:** Copilot

Collaborative frontier exploration — bots share a map via radio and each
targets the unexplored area farthest from its peers.

## Strategy

### Phase 1 — Localisation

Each bot navigates in relative coordinates (spawn = origin) and scans
for `OUT_OF_BOUNDS` tiles to deduce its absolute position on each axis
independently.  Until fully localised it explores autonomously.

### Phase 2 — Collaborative exploration

Once localised, scan results and position are broadcast in *absolute*
coordinates.  Teammates share knowledge via radio so each bot builds a
global map.  Frontier targeting + BFS pathfinding steer bots toward
the unexplored area farthest from peers.

## Core algorithms

| Algorithm | Purpose |
|---|---|
| **Self-localisation** | Detect `OUT_OF_BOUNDS` in cardinal scan neighbours; combine with known `map_width`/`map_height` to compute absolute position per axis. |
| **BFS pathfinding** | Breadth-first search on the known tile graph (obstacles blocked, traps allowed). Max depth 60. |
| **Frontier scoring** | Score = `min_peer_distance × 2 − self_distance × 1`. Top 20% candidates, pick randomly. |
| **Scan cadence** | Scan every 3 moves, or immediately when ≥ 3 unknown cardinal neighbours. Staggered across bots on early turns. |

## Radio protocol

| Message | Format | Description |
|---|---|---|
| Position | `AP<id>:<x>,<y>` | Bot's absolute position |
| Scan data | `AS<x>,<y>:<tile>\|…` | Tile types from latest scan (absolute coords) |

Only absolute-coordinate messages (prefixed `A`) are consumed; messages
from un-localised bots are ignored to avoid frame-mismatch corruption.
