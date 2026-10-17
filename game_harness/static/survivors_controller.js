function createSurvivorController(configuration, options = {}) {
  const mode = options.mode ?? "mpc";
  const bound = (value) => typeof value === "number" && Number.isFinite(value) && value > 40 ? value : 800;
  const halfWidth = bound(options.worldHalfWidth ?? configuration.world?.halfWidth);
  const halfHeight = bound(options.worldHalfHeight ?? configuration.world?.halfHeight);
  const safeHalfWidth = halfWidth - 20, safeHalfHeight = halfHeight - 20;
  const softHalfWidth = halfWidth * 0.8125, softHalfHeight = halfHeight * 0.8125;
  const defs = new Map(configuration.enemies.map((e) => [e.id, e]));
  let last = { dx: 1, dy: 0 };
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const len = (x, y) => Math.hypot(x, y);
  function choose(s) {
    const cards = s.upgradeChoices ?? s.skills.upgradeChoices, active = s.skills.active, passive = s.skills.passive;
    const slots = active.length + s.skills.ultimate.length;
    const score = (c) => {
      if (c.type === "evolution") return 1e4;
      let v = { W01: 85, W02: 65, W03: 78, W04: 65, W05: 56, W06: 57, W07: 49, W08: 67, W09: 47, W10: 105, W11: 42, W12: 15, P01: 45, P02: 40, P03: 38, P04: 54, P05: 70, P06: 43, P07: 44, P08: 20, reward_heal: 65, reward_damage: 40, reward_gold: 0 }[c.id] ?? 0;
      if (c.id[0] === "W") {
        if (!c.currentLevel) {
          if (slots < 3) v += 65;
          else v -= 25;
        } else if ([3, 5].includes(c.nextLevel)) v += 35;
        if (c.id === "W01" && c.nextLevel === 3) v += 80;
        if (c.id === "W10" && c.nextLevel === 3) v += 45;
      }
      const hp = s.player.hp / s.player.maxHp;
      if (c.id === "P01") v += (1 - hp) * 70;
      if (c.id === "P04" && c.currentLevel < 2) v += 20;
      if (c.id === "P06" && hp < 0.8) v += 30;
      if (c.id === "P05" && (active.find((a) => a.id === "W01")?.level ?? 0) >= 3 && c.nextLevel <= 3) v += 55;
      if (c.id === "W01" && (passive.find((a) => a.id === "P05")?.level ?? 0) >= 2) v += 70;
      if (mode === "growth" && c.id === "P07") v += 50;
      if (mode === "orbit" && ["W10", "P03"].includes(c.id)) v += 60;
      return v;
    };
    return [...cards].sort((a, b) => score(b) - score(a) || a.id.localeCompare(b.id))[0];
  }
  function control(s) {
    const p = s.player, speed = s.effectiveStats.moveSpeed, entities = s.entities.filter((e) => e.active && e.hp > 0);
    const threats = entities.filter((e) => e.group === "enemy" && Math.hypot(e.x - p.x, e.y - p.y) < 650);
    const picks = entities.filter((e) => ["gem", "xp", "healing", "magnet"].includes(e.kind) && Math.hypot(e.x - p.x, e.y - p.y) < 550).map((e) => ({ ...e, value: e.kind === "healing" ? (p.maxHp - p.hp) * 2 : e.kind === "magnet" ? 60 : Math.min(8, e.bonus ?? 1) }));
    const radius = Math.hypot(p.x, p.y), angle = radius > 10 ? Math.atan2(p.y, p.x) : 0;
    const radial = clamp((340 - radius) / 250, -0.8, 0.8), baseX = -Math.sin(angle) + radial * Math.cos(angle), baseY = Math.cos(angle) + radial * Math.sin(angle);
    const directions = [];
    for (let i = 0; i < 48; i++) directions.push({ dx: Math.cos(i * Math.PI / 24), dy: Math.sin(i * Math.PI / 24) });
    directions.push({ dx: 0, dy: 0 });
    let best = -Infinity, result = directions[0];
    for (const direction of directions) {
      const vx = direction.dx * speed, vy = direction.dy * speed;
      let cost = 0, minClear = 1e9;
      for (const t of [0.12, 0.3, 0.6, 0.95]) {
        const x2 = p.x + vx * t, y2 = p.y + vy * t;
        if (Math.abs(x2) > safeHalfWidth || Math.abs(y2) > safeHalfHeight) {
          cost += 1e5 + Math.max(Math.abs(x2) - safeHalfWidth, Math.abs(y2) - safeHalfHeight) * 100;
          continue;
        }
        cost += Math.pow(Math.abs(x2) / softHalfWidth, 8) * 8 + Math.pow(Math.abs(y2) / softHalfHeight, 8) * 8;
        for (const e of threats) {
          const d0 = len(e.x - p.x, e.y - p.y) || 1, def = defs.get(e.archetype), behavior = e.operator ?? def?.behavior;
          let ex = e.x, ey = e.y;
          if (e.kind === "enemy_projectile") {
            const v = e.operator.split(",").map(Number);
            ex += v[0] * (e.operand ?? 180) * t;
            ey += v[1] * (e.operand ?? 180) * t;
          } else if ((e.__chargeDur ?? 0) > 0) {
            const amount = Math.min(t, e.__chargeDur), sp = (e.operand ?? def?.speed ?? 75) * 3;
            ex += (e.__chargeDx ?? 0) * sp * amount;
            ey += (e.__chargeDy ?? 0) * sp * amount;
          } else if (behavior === "charge" && (e.__chargeCd ?? 0) <= 0 && d0 < 250) {
            const sp = (e.operand ?? def?.speed ?? 75) * 3;
            ex += (p.x - e.x) / d0 * sp * Math.min(t, 0.6);
            ey += (p.y - e.y) / d0 * sp * Math.min(t, 0.6);
          } else {
            const sp = (e.operand ?? def?.speed ?? 75) * (behavior === "ranged" ? d0 > 250 ? 0.5 : d0 < 170 ? -1 : 0 : 1);
            const tx = p.x + vx * t * 0.45 - e.x, ty = p.y + vy * t * 0.45 - e.y, d = len(tx, ty) || 1;
            ex += tx / d * sp * t;
            ey += ty / d * sp * t;
          }
          const distance = len(x2 - ex, y2 - ey);
          minClear = Math.min(minClear, distance);
          const damage = e.power ?? 10, weight = e.kind === "enemy_projectile" ? 1.2 : 1;
          const collision = Math.max(0, 35 - distance);
          cost += weight * (collision * collision * 80 + Math.exp(-Math.max(0, distance - 25) / 28) * 65) * (1 + damage / 35) * (t < 0.4 ? 1.3 : 0.7);
        }
      }
      let reward = (direction.dx * baseX + direction.dy * baseY) * 3;
      const x = p.x + vx * 0.6, y = p.y + vy * 0.6;
      for (const pickup of picks) {
        const before = len(pickup.x - p.x, pickup.y - p.y), after = len(pickup.x - x, pickup.y - y);
        if (before < 75) continue;
        const gain = clamp((before - Math.max(65, after)) / 100, -1, 1);
        reward += pickup.value * gain * 7 * Math.exp(-before / 260);
      }
      reward += (direction.dx * last.dx + direction.dy * last.dy) * 2;
      const value = reward - cost;
      if (value > best) {
        best = value;
        result = { ...direction, score: value, minClear };
      }
    }
    last = result;
    return result;
  }
  return { chooseUpgrade: choose, movement: control };
}

