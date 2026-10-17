// src/game/navigation.ts - A* pathfinding and frame-rate independent movement
// Owner: nav-team (f04-navigation)

import type {
  GameState,
  GameConfig,
  GameEvent,
  NavGrid,
  Vec3,
  Entity,
} from './types';
import { PRNG } from './prng';
import { isCombatTarget } from './combat';

// ─── A* Pathfinding ────────────────────────────────────────────────

interface AStarNode {
  x: number;
  z: number;
  g: number;
  h: number;
  f: number;
  parent: AStarNode | null;
}

function heuristic(ax: number, az: number, bx: number, bz: number): number {
  const dx = Math.abs(ax - bx);
  const dz = Math.abs(az - bz);
  // Octile distance for 8-directional movement
  return Math.max(dx, dz) + (Math.SQRT2 - 1) * Math.min(dx, dz);
}

function worldToGrid(
  grid: NavGrid,
  wx: number,
  wz: number
): { gx: number; gz: number } {
  const gx = Math.floor((wx - (grid.originX ?? 0)) / grid.cellSize);
  const gz = Math.floor((wz - (grid.originZ ?? 0)) / grid.cellSize);
  return { gx, gz };
}

function gridToWorld(
  grid: NavGrid,
  gx: number,
  gz: number
): Vec3 {
  return {
    x: (grid.originX ?? 0) + (gx + 0.5) * grid.cellSize,
    y: 0,
    z: (grid.originZ ?? 0) + (gz + 0.5) * grid.cellSize,
  };
}

/**
 * Find a path on the navigation grid using A* algorithm.
 * Returns an array of world-space waypoints or null if unreachable.
 */
export function findPath(
  grid: NavGrid,
  start: Vec3,
  end: Vec3
): Vec3[] | null {
  const s = worldToGrid(grid, start.x, start.z);
  const e = worldToGrid(grid, end.x, end.z);

  // Reject out-of-map coordinates rather than aliasing a boundary cell.
  if (s.gx < 0 || s.gx >= grid.width || s.gz < 0 || s.gz >= grid.height ||
      e.gx < 0 || e.gx >= grid.width || e.gz < 0 || e.gz >= grid.height) return null;
  // If start or end is blocked, no path
  if (grid.blocked[s.gz * grid.width + s.gx]) return null;
  if (grid.blocked[e.gz * grid.width + e.gx]) return null;

  // Same cell
  if (s.gx === e.gx && s.gz === e.gz) {
    return [{ x: end.x, y: 0, z: end.z }];
  }

  const openList: AStarNode[] = [];
  const closedSet = new Uint8Array(grid.width * grid.height);

  const startNode: AStarNode = {
    x: s.gx,
    z: s.gz,
    g: 0,
    h: heuristic(s.gx, s.gz, e.gx, e.gz),
    f: 0,
    parent: null,
  };
  startNode.f = startNode.g + startNode.h;
  openList.push(startNode);

  // Direction offsets for 8-connected grid
  const dirs = [
    [-1, -1], [0, -1], [1, -1],
    [-1,  0],          [1,  0],
    [-1,  1], [0,  1], [1,  1],
  ];

  while (openList.length > 0) {
    // Find node with lowest f
    let bestIdx = 0;
    let bestF = openList[0].f;
    for (let i = 1; i < openList.length; i++) {
      if (openList[i].f < bestF) {
        bestF = openList[i].f;
        bestIdx = i;
      }
    }

    const current = openList[bestIdx];
    // Remove from open list
    openList[bestIdx] = openList[openList.length - 1];
    openList.pop();

    const cIdx = current.z * grid.width + current.x;
    if (closedSet[cIdx]) continue;
    closedSet[cIdx] = 1;

    // Reached goal
    if (current.x === e.gx && current.z === e.gz) {
      const path: Vec3[] = [];
      let node: AStarNode | null = current;
      while (node) {
        path.unshift(gridToWorld(grid, node.x, node.z));
        node = node.parent;
      }
      // Remove the start point so we only have waypoints ahead
      if (path.length > 1) {
        path.shift();
      }
      // The final sub-segment stays inside the free goal cell. Preserve the
      // requested interaction point instead of stopping at a snapped center.
      path.push({ x: end.x, y: 0, z: end.z });
      return path;
    }

    for (const [dx, dz] of dirs) {
      const nx = current.x + dx;
      const nz = current.z + dz;

      if (nx < 0 || nx >= grid.width || nz < 0 || nz >= grid.height) continue;

      const nIdx = nz * grid.width + nx;
      if (closedSet[nIdx] || grid.blocked[nIdx]) continue;

      // For diagonal movement, check that both adjacent cardinal cells are free
      // to prevent cutting corners through obstacles
      if (dx !== 0 && dz !== 0) {
        const adj1 = current.z * grid.width + nx;
        const adj2 = nz * grid.width + current.x;
        if (grid.blocked[adj1] || grid.blocked[adj2]) continue;
      }

      const moveCost = dx !== 0 && dz !== 0 ? Math.SQRT2 : 1;
      const ng = current.g + moveCost;
      const nh = heuristic(nx, nz, e.gx, e.gz);

      // Check if already in open list with better g
      let existingBetter = false;
      for (const open of openList) {
        if (open.x === nx && open.z === nz && open.g <= ng) {
          existingBetter = true;
          break;
        }
      }
      if (existingBetter) continue;

      openList.push({
        x: nx,
        z: nz,
        g: ng,
        h: nh,
        f: ng + nh,
        parent: current,
      });
    }
  }

  // No path found
  return null;
}

// ─── Navigation Grid Builder ───────────────────────────────────────

/**
 * Build a NavGrid from level config data.
 * Blocks cells that overlap with obstacles or active entities (enemies).
 */
export function buildNavGrid(
  mapWidth: number,
  mapHeight: number,
  cellSize: number,
  obstacles: Array<{ x: number; z: number; width: number; depth: number }>,
  entities: Entity[],
  playerRadius: number
): NavGrid {
  const cols = Math.ceil(mapWidth / cellSize);
  const rows = Math.ceil(mapHeight / cellSize);
  const blocked = new Array<boolean>(cols * rows).fill(false);

  const originX = -mapWidth / 2;
  const originZ = -mapHeight / 2;
  // Conservatively block whole cells intersecting player-expanded geometry.
  // This prevents swept path segments from passing through thin walls.
  for (const obs of obstacles) {
    const minX = Math.floor((obs.x - obs.width / 2 - playerRadius - originX) / cellSize);
    const maxX = Math.floor((obs.x + obs.width / 2 + playerRadius - originX) / cellSize);
    const minZ = Math.floor((obs.z - obs.depth / 2 - playerRadius - originZ) / cellSize);
    const maxZ = Math.floor((obs.z + obs.depth / 2 + playerRadius - originZ) / cellSize);
    for (let gz = Math.max(0, minZ); gz <= Math.min(rows - 1, maxZ); gz++) {
      for (let gx = Math.max(0, minX); gx <= Math.min(cols - 1, maxX); gx++) {
        blocked[gz * cols + gx] = true;
      }
    }
  }

  // Block entity cells (active enemies act as obstacles for pathfinding)
  for (const ent of entities) {
    if (!ent.active) continue;
    if (ent.kind === 'weapon' || ent.kind === 'pickup') continue;
    if (ent.group === 'obstacle') continue; // rectangles are already rasterized above
    const clearance = playerRadius * 2;
    const minX = Math.floor((ent.x - clearance - originX) / cellSize);
    const maxX = Math.floor((ent.x + clearance - originX) / cellSize);
    const minZ = Math.floor((ent.z - clearance - originZ) / cellSize);
    const maxZ = Math.floor((ent.z + clearance - originZ) / cellSize);
    for (let gz = Math.max(0, minZ); gz <= Math.min(rows - 1, maxZ); gz++) {
      for (let gx = Math.max(0, minX); gx <= Math.min(cols - 1, maxX); gx++) {
        // A circular entity does not occupy the corners of its bounding square.
        // Block only cells actually intersecting its expanded collision disk.
        const x0=originX+gx*cellSize,z0=originZ+gz*cellSize;
        const nearestX=Math.max(x0,Math.min(x0+cellSize,ent.x));
        const nearestZ=Math.max(z0,Math.min(z0+cellSize,ent.z));
        if(Math.hypot(nearestX-ent.x,nearestZ-ent.z)<clearance)
          blocked[gz * cols + gx] = true;
      }
    }
  }

  return { width: cols, height: rows, cellSize, originX, originZ, blocked };
}

// Per-world geometry cache. Stationary entities need a rebuild only when a
// target is consumed or geometry changes; each real fixed step reuses that grid.
const navigationGrids = new WeakMap<GameState,{key:string;grid:NavGrid}>();
function gridForWorld(state:GameState,config:GameConfig,level:GameConfig['levels'][number]):NavGrid {
  const key=JSON.stringify([state.level,level.mapWidth,level.mapHeight,config.playerCollisionRadius,level.obstacles,
    state.entities.filter(e=>e.active&&e.kind!=='weapon'&&e.kind!=='pickup'&&e.group!=='obstacle').map(e=>[e.id,e.x,e.z])]);
  const cached=navigationGrids.get(state);if(cached?.key===key)return cached.grid;
  const grid=buildNavGrid(level.mapWidth,level.mapHeight,.25,level.obstacles,state.entities,config.playerCollisionRadius);
  navigationGrids.set(state,{key,grid});return grid;
}

// ─── Movement & Navigation Update ──────────────────────────────────

function distXZ(a: { x: number; z: number }, b: { x: number; z: number }): number {
  const dx = a.x - b.x;
  const dz = a.z - b.z;
  return Math.sqrt(dx * dx + dz * dz);
}

function clampToMap(
  px: number,
  pz: number,
  mapWidth: number,
  mapHeight: number,
  radius: number
): { x: number; z: number } {
  return {
    x: Math.max(-mapWidth / 2 + radius, Math.min(mapWidth / 2 - radius, px)),
    z: Math.max(-mapHeight / 2 + radius, Math.min(mapHeight / 2 - radius, pz)),
  };
}

/**
 * Main navigation update tick. Frame-rate independent.
 * Handles pathfinding, movement along path, obstacle avoidance,
 * target switching, interaction range stopping, and unreachable warnings.
 */
export function updateNavigation(
  state: GameState,
  dt: number,
  config: GameConfig,
  rng: PRNG,
  emit: (e: GameEvent) => void
): void {
  if (state.phase !== 'PLAYING') return;
  if (dt <= 0) return;

  const levelConfig = config.levels.find((l) => l.id === state.level);
  if (!levelConfig) return;

  const mapW = levelConfig.mapWidth;
  const mapH = levelConfig.mapHeight;
  const speed = (state.effectiveStats.moveSpeed || config.playerMoveSpeed);
  const radius = config.playerCollisionRadius;
  let interactionRange = config.enemyInteractionRange;

  // Build navigation grid dynamically (entities may move/die)
  const grid = gridForWorld(state, config, levelConfig);

  // If locked in interaction (attacking/absorbing), don't move
  if (state.interactionLock) {
    return;
  }

  // Determine destination
  let destX: number | null = null;
  let destZ: number | null = null;
  let isTargetEntity = false;
  let targetEntity: Entity | undefined;

  if (state.targetId) {
    targetEntity = state.entities.find(
      (e) => e.id === state.targetId && e.active
    );
    if (targetEntity) {
      interactionRange = targetEntity.kind === 'weapon' || targetEntity.kind === 'pickup'
        ? config.weaponPickupRange : config.enemyInteractionRange;
      if (isCombatTarget(targetEntity, config) && !(state.player.power > targetEntity.power)) {
        state.targetId = null;
        state.path = [];
        emit({ seq: state.metrics.events++, simTime: state.simTime,
          type: 'combat_invalid_warning', data: { reason: 'power_insufficient',
            message: '战力不足', targetId: targetEntity.id,
            playerPower: state.player.power, enemyPower: targetEntity.power } });
        return;
      }
      destX = targetEntity.x;
      destZ = targetEntity.z;
      isTargetEntity = true;
    } else {
      // Target died or deactivated, clear
      state.targetId = null;
      state.path = [];
    }
  }

  // If no target entity but we have a path (ground click), follow it
  // If we have a target entity, compute interaction point
  if (isTargetEntity && targetEntity && destX !== null && destZ !== null) {
    // Compute approach position: stop at interaction range from target
    const dx = state.player.x - destX;
    const dz = state.player.z - destZ;
    const dist = Math.sqrt(dx * dx + dz * dz);

    if (dist <= interactionRange) {
      // Already in range, stop moving, clear path
      state.path = [];
      return;
    }

    // Keep an existing route while its endpoint still reaches the current
    // target. Recomputing a player-relative point each tick causes oscillation.
    const endpoint = state.path[state.path.length - 1];
    const needsRepath = !endpoint || distXZ(endpoint, targetEntity) > interactionRange;
    if (needsRepath) {
      const startVec: Vec3 = { x: state.player.x, y: 0, z: state.player.z };
      const angle = Math.atan2(dz, dx);
      const radiusToTarget = interactionRange * 0.95;
      let newPath: Vec3[] | null = null;
      // Search both sides of the interaction ring: the nearest radial approach
      // may be blocked by a wall while a different side remains reachable.
      for (const offset of [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7, 8]) {
        const a = angle + offset * Math.PI / 8;
        const endVec: Vec3 = { x: destX + Math.cos(a) * radiusToTarget,
          y: 0, z: destZ + Math.sin(a) * radiusToTarget };
        newPath = findPath(grid, startVec, endVec);
        if (newPath?.length) break;
      }
      if (newPath && newPath.length > 0) {
        state.path = newPath;
      } else {
        // Unreachable target warning
        state.path = [];
        emit({
          seq: 0,
          simTime: state.simTime,
          type: 'NAV_UNREACHABLE',
          data: { targetId: state.targetId, x: destX, z: destZ, message: '无法到达目标' },
        });
        // Don't clear targetId so combat system can still evaluate if close enough
        return;
      }
    }
  } else if (!isTargetEntity && state.path.length > 0) {
    // Following ground-click path, nothing extra needed
  } else {
    // No target, no path, idle
    return;
  }

  // Move along path
  if (state.path.length === 0) return;

  const moveDistance = speed * dt;
  let remaining = moveDistance;

  while (remaining > 0 && state.path.length > 0) {
    const waypoint = state.path[0];
    const dx = waypoint.x - state.player.x;
    const dz = waypoint.z - state.player.z;
    const segDist = Math.sqrt(dx * dx + dz * dz);

    if (segDist <= remaining) {
      // Arrive at waypoint
      state.player.x = waypoint.x;
      state.player.z = waypoint.z;
      remaining -= segDist;
      state.path.shift();
    } else {
      // Move toward waypoint
      const nx = dx / segDist;
      const nz = dz / segDist;
      state.player.x += nx * remaining;
      state.player.z += nz * remaining;
      remaining = 0;
    }
  }

  // Clamp to map bounds
  const clamped = clampToMap(state.player.x, state.player.z, mapW, mapH, radius);
  state.player.x = clamped.x;
  state.player.z = clamped.z;

  // Obstacle collision resolution: push out of blocked cells
  resolveCollisions(state, grid, levelConfig.obstacles, state.entities, radius, mapW, mapH);

  // If targeting entity, check if now in range after movement
  if (isTargetEntity && targetEntity && state.path.length === 0) {
    const dist = distXZ(state.player, targetEntity);
    if (dist <= interactionRange) {
      // In range, navigation done for this target
      // Combat system will handle the rest
    }
  }
}

/**
 * Resolve collisions against obstacles and other entities.
 * Pushes the player out of overlapping geometry.
 */
function resolveCollisions(
  state: GameState,
  grid: NavGrid,
  obstacles: Array<{ x: number; z: number; width: number; depth: number }>,
  entities: Entity[],
  radius: number,
  mapW: number,
  mapH: number
): void {
  // Resolve against rectangular obstacles
  for (const obs of obstacles) {
    const halfW = obs.width / 2;
    const halfD = obs.depth / 2;
    const minX = obs.x - halfW;
    const maxX = obs.x + halfW;
    const minZ = obs.z - halfD;
    const maxZ = obs.z + halfD;

    // Find closest point on AABB to player center
    const closestX = Math.max(minX, Math.min(maxX, state.player.x));
    const closestZ = Math.max(minZ, Math.min(maxZ, state.player.z));

    const dx = state.player.x - closestX;
    const dz = state.player.z - closestZ;
    const dist = Math.sqrt(dx * dx + dz * dz);

    if (dist < radius) {
      if (dist === 0) {
        // Player center is inside obstacle, push out to nearest edge
        const distToMinX = state.player.x - minX;
        const distToMaxX = maxX - state.player.x;
        const distToMinZ = state.player.z - minZ;
        const distToMaxZ = maxZ - state.player.z;
        const minDist = Math.min(distToMinX, distToMaxX, distToMinZ, distToMaxZ);

        if (minDist === distToMinX) state.player.x = minX - radius;
        else if (minDist === distToMaxX) state.player.x = maxX + radius;
        else if (minDist === distToMinZ) state.player.z = minZ - radius;
        else state.player.z = maxZ + radius;
      } else {
        const overlap = radius - dist;
        const nx = dx / dist;
        const nz = dz / dist;
        state.player.x += nx * overlap;
        state.player.z += nz * overlap;
      }
    }
  }

  // Resolve against circular entities (enemies)
  for (const ent of entities) {
    if (!ent.active) continue;
    if (ent.kind === 'weapon' || ent.kind === 'pickup') continue;

    const dx = state.player.x - ent.x;
    const dz = state.player.z - ent.z;
    const dist = Math.sqrt(dx * dx + dz * dz);
    const minDist = radius * 2; // Both have radius

    if (dist < minDist && dist > 0) {
      const overlap = minDist - dist;
      const nx = dx / dist;
      const nz = dz / dist;
      state.player.x += nx * overlap;
      state.player.z += nz * overlap;
    }
  }

  // Final clamp
  const clamped = clampToMap(state.player.x, state.player.z, mapW, mapH, radius);
  state.player.x = clamped.x;
  state.player.z = clamped.z;
}

/**
 * Handle a selectTarget dispatch command for navigation purposes.
 * Sets up the target and initiates pathfinding.
 */
export function handleSelectTarget(
  state: GameState,
  config: GameConfig,
  targetId?: string,
  ground?: { x: number; z: number },
  emit?: (e: GameEvent) => void
): void {
  if (state.phase !== 'PLAYING' || state.interactionLock) return;

  if (targetId) {
    const entity = state.entities.find((e) => e.id === targetId && e.active);
    if (entity) {
      if (isCombatTarget(entity, config) && !(state.player.power > entity.power)) {
        state.targetId = null;
        state.path = [];
        emit?.({ seq: state.metrics.events++, simTime: state.simTime,
          type: 'combat_invalid_warning', data: { reason: 'power_insufficient',
            message: '战力不足', targetId: entity.id,
            playerPower: state.player.power, enemyPower: entity.power } });
        return;
      }
      state.targetId = targetId;
      state.path = []; // Will be computed in next updateNavigation tick
    }
  } else if (ground) {
    // Ground click: navigate to position, clear entity target
    state.targetId = null;

    const levelConfig = config.levels.find((l) => l.id === state.level);
    if (!levelConfig) return;

    const grid = gridForWorld(state, config, levelConfig);

    const startVec: Vec3 = { x: state.player.x, y: 0, z: state.player.z };
    const endVec: Vec3 = { x: ground.x, y: 0, z: ground.z };
    const path = findPath(grid, startVec, endVec);

    if (path) {
      state.path = path;
    } else {
      state.path = [];
      emit?.({seq:0,simTime:state.simTime,type:'NAV_UNREACHABLE',data:{x:ground.x,z:ground.z,message:'无法到达目标'}});
    }
  }
}

/**
 * Handle a move dispatch command (delta or absolute).
 */
export function handleMoveCommand(
  state: GameState,
  config: GameConfig,
  cmd: { x?: number; y?: number; z?: number; dx?: number; dy?: number }
): void {
  if (state.phase !== 'PLAYING' || state.interactionLock) return;

  const levelConfig = config.levels.find((l) => l.id === state.level);
  if (!levelConfig) return;

  // Clear any auto-navigation target
  state.targetId = null;
  state.path = [];

  if (cmd.dx !== undefined || cmd.dy !== undefined) {
    // Delta movement (from input)
    const dx = cmd.dx || 0;
    const dz = cmd.dy || 0; // dy maps to z in top-down
    const len = Math.sqrt(dx * dx + dz * dz);
    if (len > 0) {
      const speed = (state.effectiveStats.moveSpeed || config.playerMoveSpeed);
      // Apply as a single-frame impulse scaled to a reference dt
      const step = speed * 0.016; // ~60fps equivalent per dispatch
      state.player.x += (dx / len) * step;
      state.player.z += (dz / len) * step;
    }
  } else if (cmd.x !== undefined && cmd.z !== undefined) {
    // Absolute move (set path to target)
    const grid = gridForWorld(state, config, levelConfig);
    const startVec: Vec3 = { x: state.player.x, y: 0, z: state.player.z };
    const endVec: Vec3 = { x: cmd.x, y: 0, z: cmd.z };
    const path = findPath(grid, startVec, endVec);
    if (path) {
      state.path = path;
    }
  }

  // Delta input uses a small bounded displacement, but still needs collision
  // resolution immediately: the next navigation tick may be idle with no path.
  const r = config.playerCollisionRadius;
  const grid = buildNavGrid(levelConfig.mapWidth, levelConfig.mapHeight, 0.25,
    levelConfig.obstacles, state.entities, r);
  resolveCollisions(state, grid, levelConfig.obstacles, state.entities, r,
    levelConfig.mapWidth, levelConfig.mapHeight);
}
