"""Game map generation and management."""

from __future__ import annotations

import random
from collections import deque

from radiogrid.engine.models import TileType


class GameMap:
    """A rectangular grid map for the RadioGrid game.

    Handles generation, connectivity validation, and tile queries.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.tiles: list[list[TileType]] = [
            [TileType.EMPTY for _ in range(height)] for _ in range(width)
        ]
        # spawn_positions[team_id] -> list of (x, y) positions for that team
        self.spawn_positions: dict[int, list[tuple[int, int]]] = {}

    def in_bounds(self, x: int, y: int) -> bool:
        """Check if coordinates are within the map boundaries."""
        return 0 <= x < self.width and 0 <= y < self.height

    def get_tile(self, x: int, y: int) -> TileType:
        """Get the tile type at the given coordinates."""
        if not self.in_bounds(x, y):
            return TileType.OUT_OF_BOUNDS
        return self.tiles[x][y]

    def is_passable(self, x: int, y: int) -> bool:
        """Check if a tile can be entered by a bot."""
        if not self.in_bounds(x, y):
            return False
        return self.tiles[x][y] != TileType.OBSTACLE

    @staticmethod
    def generate(
        width: int = 20,
        height: int = 20,
        num_teams: int = 2,
        obstacle_ratio: float = 0.2,
        trap_ratio: float = 0.05,
        seed: int | None = None,
    ) -> GameMap:
        """Generate a random game map.

        Ensures all non-obstacle tiles are reachable from any spawn point.
        Spawns are distributed around the map perimeter for fairness.

        Args:
            width: Map width in tiles.
            height: Map height in tiles.
            num_teams: Number of teams (>= 2).
            obstacle_ratio: Target proportion of tiles that are obstacles (0-1).
            trap_ratio: Target proportion of non-obstacle tiles that are traps (0-1).
            seed: Optional seed for deterministic generation.

        Returns:
            A fully generated GameMap ready for play.
        """
        if num_teams < 2:
            raise ValueError("At least 2 teams are required")

        rng = random.Random(seed)
        game_map = GameMap(width, height)

        # Compute spawn positions for each team (team_ids are 1-indexed)
        all_spawns: set[tuple[int, int]] = set()
        for team_idx in range(num_teams):
            team_id = team_idx + 1
            spawns = _compute_team_spawns(width, height, team_idx, num_teams)
            game_map.spawn_positions[team_id] = spawns
            all_spawns.update(spawns)

        # Mark spawn tiles
        for x, y in all_spawns:
            game_map.tiles[x][y] = TileType.SPAWN

        # Place obstacles randomly, avoiding spawns
        total_tiles = width * height
        target_obstacles = int(total_tiles * obstacle_ratio)
        non_spawn_tiles = [
            (x, y)
            for x in range(width)
            for y in range(height)
            if (x, y) not in all_spawns
        ]
        rng.shuffle(non_spawn_tiles)

        obstacles_placed = 0
        for x, y in non_spawn_tiles:
            if obstacles_placed >= target_obstacles:
                break
            game_map.tiles[x][y] = TileType.OBSTACLE
            obstacles_placed += 1

        # Validate connectivity: all non-obstacle tiles reachable from first spawn
        first_spawn = game_map.spawn_positions[1][0]
        if not _is_connected(game_map, first_spawn):
            _ensure_connectivity(game_map, first_spawn, rng)

        # Place traps on remaining empty tiles
        empty_tiles = [
            (x, y)
            for x in range(width)
            for y in range(height)
            if game_map.tiles[x][y] == TileType.EMPTY
        ]
        non_obstacle_count = sum(
            1
            for x in range(width)
            for y in range(height)
            if game_map.tiles[x][y] != TileType.OBSTACLE
        )
        target_traps = int(non_obstacle_count * trap_ratio)
        rng.shuffle(empty_tiles)

        traps_placed = 0
        for x, y in empty_tiles:
            if traps_placed >= target_traps:
                break
            game_map.tiles[x][y] = TileType.TRAP
            traps_placed += 1

        return game_map


def _compute_team_spawns(
    width: int, height: int, team_idx: int, num_teams: int
) -> list[tuple[int, int]]:
    """Compute 5 spawn positions for a team.

    For 2 teams: opposite corners (top-left, bottom-right).
    For N teams: evenly spaced anchor points around the map perimeter.
    """
    offsets = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0)]

    if num_teams == 2:
        # Opposite corners for backward compatibility
        if team_idx == 0:
            base_x, base_y = 1, 1
            return [(base_x + dx, base_y + dy) for dx, dy in offsets]
        else:
            base_x, base_y = width - 2, height - 2
            return [(base_x - dx, base_y - dy) for dx, dy in offsets]

    # General case: distribute teams around the perimeter (clockwise)
    perimeter_points: list[tuple[int, int]] = []
    # Top edge (left to right)
    for x in range(1, width - 1):
        perimeter_points.append((x, 1))
    # Right edge (top to bottom)
    for y in range(1, height - 1):
        perimeter_points.append((width - 2, y))
    # Bottom edge (right to left)
    for x in range(width - 2, 0, -1):
        perimeter_points.append((x, height - 2))
    # Left edge (bottom to top)
    for y in range(height - 2, 0, -1):
        perimeter_points.append((1, y))

    # De-duplicate while preserving order
    seen: set[tuple[int, int]] = set()
    unique_perimeter: list[tuple[int, int]] = []
    for p in perimeter_points:
        if p not in seen:
            seen.add(p)
            unique_perimeter.append(p)

    perimeter_len = len(unique_perimeter)
    spacing = perimeter_len / num_teams
    anchor_idx = int(team_idx * spacing) % perimeter_len
    anchor = unique_perimeter[anchor_idx]

    ax, ay = anchor
    spawns: list[tuple[int, int]] = []
    for dx, dy in offsets:
        sx = max(0, min(width - 1, ax + dx))
        sy = max(0, min(height - 1, ay + dy))
        if (sx, sy) not in spawns:
            spawns.append((sx, sy))

    # Expand nearby if fewer than 5 due to clamping/overlap
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if len(spawns) >= 5:
                break
            sx = max(0, min(width - 1, ax + dx))
            sy = max(0, min(height - 1, ay + dy))
            if (sx, sy) not in spawns:
                spawns.append((sx, sy))
        if len(spawns) >= 5:
            break

    return spawns[:5]


def _get_passable_neighbors(
    game_map: GameMap, x: int, y: int
) -> list[tuple[int, int]]:
    """Get passable cardinal neighbors of a tile."""
    neighbors = []
    for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
        nx, ny = x + dx, y + dy
        if game_map.in_bounds(nx, ny) and game_map.tiles[nx][ny] != TileType.OBSTACLE:
            neighbors.append((nx, ny))
    return neighbors


def _flood_fill(game_map: GameMap, start: tuple[int, int]) -> set[tuple[int, int]]:
    """BFS flood fill from a start position, returning all reachable passable tiles."""
    visited: set[tuple[int, int]] = set()
    queue = deque([start])
    visited.add(start)

    while queue:
        x, y = queue.popleft()
        for nx, ny in _get_passable_neighbors(game_map, x, y):
            if (nx, ny) not in visited:
                visited.add((nx, ny))
                queue.append((nx, ny))

    return visited


def _is_connected(game_map: GameMap, start: tuple[int, int]) -> bool:
    """Check if all non-obstacle tiles are reachable from the given start position."""
    all_passable = {
        (x, y)
        for x in range(game_map.width)
        for y in range(game_map.height)
        if game_map.tiles[x][y] != TileType.OBSTACLE
    }

    if not all_passable:
        return True

    reachable = _flood_fill(game_map, start)
    return reachable == all_passable


def _ensure_connectivity(
    game_map: GameMap, start: tuple[int, int], rng: random.Random
) -> None:
    """Remove obstacles until all non-obstacle tiles are connected."""
    while not _is_connected(game_map, start):
        reachable = _flood_fill(game_map, start)

        unreachable_tile = None
        for x in range(game_map.width):
            for y in range(game_map.height):
                if (
                    game_map.tiles[x][y] != TileType.OBSTACLE
                    and (x, y) not in reachable
                ):
                    unreachable_tile = (x, y)
                    break
            if unreachable_tile:
                break

        if unreachable_tile is None:
            break

        _connect_via_obstacle_removal(game_map, unreachable_tile, reachable)


def _connect_via_obstacle_removal(
    game_map: GameMap,
    start: tuple[int, int],
    target_set: set[tuple[int, int]],
) -> None:
    """Remove obstacles along shortest path from start to target_set."""
    visited: set[tuple[int, int]] = set()
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    visited.add(start)

    found: tuple[int, int] | None = None

    while queue:
        x, y = queue.popleft()
        if (x, y) in target_set:
            found = (x, y)
            break

        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nx, ny = x + dx, y + dy
            if game_map.in_bounds(nx, ny) and (nx, ny) not in visited:
                visited.add((nx, ny))
                parent[(nx, ny)] = (x, y)
                queue.append((nx, ny))

    if found is None:
        return

    current: tuple[int, int] | None = found
    while current is not None:
        cx, cy = current
        if game_map.tiles[cx][cy] == TileType.OBSTACLE:
            game_map.tiles[cx][cy] = TileType.EMPTY
        current = parent.get(current)
