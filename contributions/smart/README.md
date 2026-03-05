# Pathfinders

**Registry key:** `pathfinders`
**Author:** Copilot

Efficient cooperative exploration with trap avoidance, Dijkstra
pathfinding, and zone-based territory coordination.

## Strategy

### Enhanced localisation

Diagonal `OUT_OF_BOUNDS` inference lets a bot resolve both axes in
fewer scans.  A diagonal OOB is enough to infer the missing axis when
the adjacent cardinal tile is in-bounds.

### Trap sharing & avoidance

Every broadcast includes ALL known trap locations.  Dijkstra
pathfinding assigns a high cost to traps (cost 5 vs 1 for normal
tiles) so bots route around them when a reasonable detour exists.
A bot that hits a trap immediately shares it with the entire team.

### Zone-based coordination

The map is divided into 5 vertical strips.  Each bot claims the
closest unclaimed zone (avoiding peer conflicts via radio).  Frontier
scoring gives a +5 bonus to tiles inside the bot's own zone, reducing
overlap without hard constraints.

## Core algorithms

| Algorithm | Purpose |
|---|---|
| **Diagonal OOB inference** | If a diagonal tile is OOB but the adjacent cardinal is not, the OOB must be on the other axis — resolve it in one scan. |
| **Dijkstra pathfinding** | Weighted shortest path: trap tiles cost 5, normal tiles cost 1. Max path cost 120. |
| **Zone assignment** | 5 vertical strips. Each bot picks the closest strip not already claimed by a peer (penalty −100 for taken zones). |
| **Frontier scoring** | `min_peer_dist × 2 − self_dist + zone_bonus(5)`. Top 20% candidates, pick randomly. |
| **Scan cadence** | Staggered on early turns by bot index. Then every 3 moves, or immediately when ≥ 3 unknown cardinal neighbours. |

## Radio protocol

| Message | Format | Description |
|---|---|---|
| Position | `AP<id>:<x>,<y>` | Bot's absolute position |
| Zone | `AZ<id>:<zone>` | Zone assignment (0–4) |
| Traps | `AT<x>,<y>\|…` | All known trap locations |
| Scan data | `AS<x>,<y>:<tile>\|…` | Tile types from latest scan |
