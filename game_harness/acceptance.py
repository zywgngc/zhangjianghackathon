"""Trusted acceptance plans and artifact-backed, mechanical result evaluation.

The game and a language model may provide observations but cannot declare that
an acceptance test passed. This module evaluates assertions against hashed runner
artifacts. Missing evidence stays unexecuted, including visual/manual criteria.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .specifications import SpecDocument, load_case


# A changed upstream specification is ingested normally, but its trusted
# mechanical expectations need an explicit review rather than stale answers.
PLAN_SOURCE_HASHES = {
    "GAME-TEST-001": "b99717267f61fdac53bbc6e3508c4e9128017284387fa5ae6819f768d7b23f1b",
    "GAME-TEST-002": "6a4997f5514742664443d03237231c06137596e8fd90e14048d2974040be5c01",
    "GAME-TEST-003": "f220b6be3a0a7682fdd420ba8d64145208bd1e72d09f36a8a553df7a8fc9e8a0",
}
VISUAL_TESTS = {
    "GAME-TEST-001": {"T37": True, "T38": True, "T54": False},
    "GAME-TEST-002": {"T63": True, "T64": True, "T69": True, "T70": True, "T87": False},
    "GAME-TEST-003": {"T02": True, "T03": True, "T25": True, "T63": True, "T64": True,
                       "T70": False, "T74": False, "T80": True, "T81": True},
}


GATE_LEVELS = [
    {"level": 1, "name": "沙漠前线", "duration": 45, "bossHp": 150, "reward": 100,
     "gates": [["+5", "+2"], ["×2", "-3"], ["+10", "÷2"], ["×2", "-5"]]},
    {"level": 2, "name": "战地防线", "duration": 60, "bossHp": 300, "reward": 200,
     "gates": [["+8", "+3"], ["×2", "-5"], ["+12", "÷2"], ["×2", "-8"], ["+20", "÷3"]]},
    {"level": 3, "name": "军事基地", "duration": 75, "bossHp": 500, "reward": 300,
     "gates": [["+10", "+4"], ["×3", "-8"], ["÷2", "+15"], ["×2", "-10"], ["+20", "÷3"], ["×2", "-15"]]},
]
ACTIVE_SKILLS = [
    {"id": "W01", "name": "御剑术", "damage": 12, "cooldown": 1.5, "type": "homing", "counts": [1, 1, 3, 3, 5]},
    {"id": "W02", "name": "六脉剑气", "damage": 16, "cooldown": 1.2, "type": "piercing", "counts": [1, 1, 3, 3, 5], "maxHitsLevel5": 3},
    {"id": "W03", "name": "降龙掌", "damage": 24, "cooldown": 2, "type": "cone", "widthLevel3": 1.5, "counts": [1, 1, 1, 1, 3]},
    {"id": "W04", "name": "旋风刀法", "damage": 12, "cooldown": 2, "type": "rotating", "radiusLevel3": 1.4, "counts": [1, 1, 1, 1, 2]},
    {"id": "W05", "name": "暴雨梨花针", "damage": 5, "cooldown": 1.8, "type": "needles", "counts": [5, 5, 9, 9, 15], "statusLevel5": "poison"},
    {"id": "W06", "name": "寒冰神掌", "damage": 18, "cooldown": 2, "type": "ice", "slow": 0.3, "explosionLevel3": True, "freezeChanceLevel5": 0.2},
    {"id": "W07", "name": "烈火刀法", "damage": 20, "cooldown": 2, "type": "fire", "burnDurationLevel3": 3, "explosionLevel5": True},
    {"id": "W08", "name": "雷霆指", "damage": 18, "cooldown": 2.5, "type": "chain", "counts": [1, 1, 3, 3, 6]},
    {"id": "W09", "name": "万剑归宗", "damage": 15, "cooldown": 5, "type": "falling", "counts": [6, 6, 12, 12, 20]},
    {"id": "W10", "name": "太极剑阵", "damage": 10, "type": "orbit", "counts": [2, 2, 4, 4, 6], "radiusLevel5": 1.3},
    {"id": "W11", "name": "金刚震地", "damage": 25, "cooldown": 4, "type": "shockwave", "radiusLevel3": 1.5, "statusLevel5": ["stun", "knockback"]},
    {"id": "W12", "name": "影分身术", "type": "clones", "cooldown": 12, "duration": 5, "attackInterval": 1,
     "counts": [1, 1, 2, 2, 3], "damageMultipliers": [0.3, 0.36, 0.36, 0.42, 0.5], "maxClones": 3},
]
PASSIVE_SKILLS = [
    {"id": "P01", "name": "九阳真气", "stat": "maxHp", "perLevel": 0.1},
    {"id": "P02", "name": "九阴心法", "stat": "damage", "perLevel": 0.08},
    {"id": "P03", "name": "易筋经", "stat": "damageReduction", "perLevel": 0.05},
    {"id": "P04", "name": "凌波步", "stat": "moveSpeed", "perLevel": 0.08},
    {"id": "P05", "name": "太玄心经", "stat": "cooldownReduction", "perLevel": 0.05},
    {"id": "P06", "name": "吸星秘术", "stat": "healOnKillChance", "perLevel": 0.05, "healHp": 1, "maxTriggersPerSecond": 5},
    {"id": "P07", "name": "北冥心法", "stat": "xpMultiplier", "perLevel": 0.1},
    {"id": "P08", "name": "乾坤心诀", "stat": "range", "perLevel": 0.08},
]
ULTIMATE_SKILLS = [
    {"id": "E01", "name": "万剑天诛", "requires": {"W01": 5, "P05": 3}, "replaces": ["W01"], "damage": 25, "cooldown": 3, "count": 20},
    {"id": "E02", "name": "六脉归一", "requires": {"W02": 5, "P02": 3}, "replaces": ["W02"], "damage": 80, "cooldown": 2, "maxHits": 20},
    {"id": "E03", "name": "神龙降世", "requires": {"W03": 5, "P01": 3}, "replaces": ["W03"], "damage": 120, "cooldown": 3, "type": "aoe"},
    {"id": "E04", "name": "冰火九重天", "requires": {"W06": 5, "W07": 5}, "replaces": ["W06", "W07"], "damagePerSecond": 40, "cooldown": 4, "duration": 3, "status": ["slow", "burn"]},
    {"id": "E05", "name": "太极无极阵", "requires": {"W10": 5, "P03": 3}, "replaces": ["W10"], "damage": 35, "count": 8, "damageReduction": 0.1},
    {"id": "E06", "name": "雷动九霄", "requires": {"W08": 5, "P08": 3}, "replaces": ["W08"], "damage": 75, "cooldown": 4, "maxTargets": 20},
]
WUXIA_ENEMIES = [
    {"name": "山贼", "hp": 20, "damage": 8, "speed": 75, "xp": 1},
    {"name": "持刀恶徒", "hp": 35, "damage": 10, "speed": 95, "xp": 2},
    {"name": "江湖刺客", "hp": 25, "damage": 12, "speed": 145, "xp": 3},
    {"name": "魔教弟子", "hp": 60, "damage": 15, "speed": 105, "xp": 4},
    {"name": "铁甲武士", "hp": 120, "damage": 18, "speed": 65, "xp": 6},
    {"name": "邪派术士", "hp": 50, "damage": 12, "speed": 80, "xp": 5, "rangedInterval": 3, "projectileSpeed": 180, "projectileDamage": 12},
]
WUXIA_LEVELS = [
    {"level": 1, "name": "竹林秘境", "duration": 360, "enemyCap": 100, "boss": "黑风寨主", "bossHp": 3000, "bossInterval": 3,
     "spawnRates": [[0, 30, 2], [30, 60, 3], [60, 120, 4], [120, 180, 5], [180, 240, 6], [240, 300, 7], [300, 360, 8]],
     "elite": "山贼头目", "eliteHp": 600, "eliteDamage": 20, "eliteTimes": [180, 270], "reward": 100},
    {"level": 2, "name": "荒漠古道", "duration": 420, "enemyCap": 150, "boss": "大漠狂刀", "bossHp": 5000, "bossInterval": 2.5,
     "spawnRates": [[0, 30, 3], [30, 60, 4], [60, 120, 5], [120, 180, 6], [180, 240, 7], [240, 300, 8], [300, 360, 9], [360, 420, 10]],
     "elite": "大漠刀王", "eliteHp": 1000, "eliteDamage": 25, "eliteTimes": [180, 300], "reward": 200},
    {"level": 3, "name": "魔教总坛", "duration": 480, "enemyCap": 200, "boss": "魔教教主", "bossHp": 8000, "bossInterval": 2,
     "spawnRates": [[0, 30, 4], [30, 60, 5], [60, 120, 6], [120, 180, 7], [180, 240, 8], [240, 300, 9], [300, 360, 10], [360, 420, 11], [420, 480, 12]],
     "elite": "魔教护法", "eliteHp": 1500, "eliteDamage": 30, "eliteTimes": [180, 300, 390], "reward": 300},
]
POWER_LEVELS = [
    {"level": 1, "name": "森林营地", "initialPower": 10, "mapSize": [30, 30], "enemyPowers": [3, 5, 8, 12, 16, 22, 30, 44], "weapons": [5, 10, 20], "boss": "森林巨魔", "bossPower": 150},
    {"level": 2, "name": "荒漠遗迹", "initialPower": 20, "mapSize": [36, 36], "enemyPowers": [6, 9, 14, 20, 28, 40, 55, 70, 90, 115, 145, 180], "weapons": [10, 20, 30, 50], "boss": "遗迹守卫", "bossPower": 500},
    {"level": 3, "name": "魔王城堡", "initialPower": 30, "mapSize": [42, 42], "enemyPowers": [8, 12, 18, 27, 40, 60, 85, 120, 170, 230, 310, 420, 550, 700, 900, 1200], "weapons": [10, 20, 30, 50, 100], "boss": "深渊魔王", "bossPower": 1500},
]


def _scenario(case_id: str, key: str, tests: list[int], steps: list[dict[str, Any]], assertions: list[dict[str, Any]],
              fixture: dict[str, Any] | None = None, kind: str = "runtime") -> dict[str, Any]:
    if case_id == "GAME-TEST-002" and fixture is not None and key not in {"free-upgrade-clock", "second-free-new-active-guarantee"}:
        fixture.setdefault("freeUpgradesGranted", [5, 15])
    return {"id": case_id + "/scenario/" + key, "acceptance_ids": [f"{case_id}/T{n:02}" for n in tests],
            "kind": kind, "seed": 20261016, "fixture": fixture, "steps": steps,
            "assertions": assertions, "status": "not_run"}


def scenario_plan(case_id: str, root: str | Path | None = None) -> dict[str, Any]:
    """Machine-readable tests owned by the harness, never by generated projects.

    ``steps`` call only the declared production bridge. A saved snapshot is a
    JSON document; assertions address ``/<save-name>/...`` in the runner trace.
    A fixture creates an isolated instance of the *same* production Simulation
    class. Fixture tests cannot prove normal-play level completion.
    """
    spec = load_case(case_id, root)
    if spec.sha256 != PLAN_SOURCE_HASHES.get(case_id):
        raise ValueError("The authoritative specification changed; review the trusted scenario expectations before evaluation")
    command = lambda name, **args: {"call": "command", "name": name, "args": args}
    snapshot = lambda name: {"call": "snapshot", "save": name}
    advance = lambda milliseconds: {"call": "advance", "milliseconds": milliseconds}
    assertion = lambda pointer, op, expected: {"pointer": pointer, "op": op, "expected": expected}
    scenarios = [_scenario(case_id, "boot", [1], [command("start", level=1), snapshot("started")],
                           [assertion("/started/phase", "eq", "PLAYING")])]
    scenarios.append(_scenario(case_id, "pause-freezes", [32] if case_id.endswith("001") else [62] if case_id.endswith("002") else [58],
        [command("start", level=1), command("pause"), snapshot("before"), advance(1500), snapshot("after"), command("resume"), snapshot("resumed")],
        [assertion("/after/phase", "eq", "PAUSED"), {"pointer": "/after/simTime", "op": "same", "other_pointer": "/before/simTime"},
         assertion("/resumed/phase", "eq", "PLAYING")]))
    if case_id == "GAME-TEST-001":
        configuration = {"levels": GATE_LEVELS, "initialSoldiers": 1, "maxSoldiers": 200, "minSoldiers": 0,
                         "roadWidth": 12, "xBounds": [-5.5, 5.5], "moveSpeed": 6, "forwardSpeed": 8, "inputSmoothing": 0.1,
                         "attacksPerSecond": 2, "bulletDamage": 1, "projectileSpeed": 30, "projectileLifetime": 3,
                         "normalEnemyHp": 10, "obstacleLoss": {"sandbag": 2, "barbedWire": 3, "mine": 5},
                         "upgradeMax": 10, "upgradeBaseCost": 100}
        for key, test, soldiers, operator, operand, expected in [
            ("addition", 6, 1, "+", 5, 6), ("multiply", 7, 6, "*", 3, 18),
            ("subtract", 8, 18, "-", 4, 14), ("divide-floor", 9, 5, "/", 2, 2),
            ("upper-bound", 12, 199, "+", 5, 200), ("zero-defeat", 13, 1, "-", 3, 0)]:
            scenarios.append(_scenario(case_id, key, [test], [snapshot("before"), advance(300), snapshot("after"), advance(300), snapshot("repeat")],
                [assertion("/after/player/soldiers", "eq", expected), assertion("/repeat/player/soldiers", "eq", expected)]
                + ([assertion("/after/phase", "eq", "DEFEAT")] if expected == 0 else []),
                {"player": {"soldiers": soldiers, "x": 0, "z": 0}, "gates": [{"id": "test-gate", "group": "test-group", "operator": operator, "operand": operand, "x": 0, "z": 1}], "boss": None, "trackEnd": 1000}))
        for kind, loss in configuration["obstacleLoss"].items():
            scenarios.append(_scenario(case_id, "obstacle-" + kind, [18, 51], [advance(300), snapshot("hit"), advance(300), snapshot("repeat")],
                [assertion("/hit/player/soldiers", "eq", 10 - loss), assertion("/repeat/player/soldiers", "eq", 10 - loss)],
                {"player": {"soldiers": 10, "x": 0, "z": 0}, "obstacles": [{"id": "test-hazard", "kind": kind, "x": 0, "z": 1}], "boss": None, "trackEnd": 1000}))
        scenarios.append(_scenario(case_id, "automatic-fire", [5, 14, 15, 16, 17, 48], [advance(3000), snapshot("after")],
            [assertion("/after/metrics/shotsFired", "gt", 0), assertion("/after/metrics/totalDamage", "gt", 0), assertion("/after/metrics/enemiesDefeated", "gte", 1)],
            {"player": {"soldiers": 20, "x": 0, "z": 0}, "enemies": [{"id": "target", "hp": 10, "x": 0, "z": 12}], "boss": None, "trackEnd": 1000}))
        normal_routes = [{"level": level["level"], "test_id": f"T{21 + level['level']:02}",
                          "strategy": {"type": "gate_runner", "gateChoices": [0, 0, 1 if level["level"] == 3 else 0] + [0] * (len(level["gates"]) - 3), "avoidObstacles": True},
                          "initial_fixture_allowed": False, "assertions": [{"pointer": "/final/phase", "op": "eq", "expected": "VICTORY"}]} for level in GATE_LEVELS]
    elif case_id == "GAME-TEST-002":
        configuration = {"levels": WUXIA_LEVELS, "activeSkills": ACTIVE_SKILLS, "passiveSkills": PASSIVE_SKILLS,
                         "ultimateSkills": ULTIMATE_SKILLS, "enemies": WUXIA_ENEMIES,
                         "player": {"hp": 100, "maxHp": 100, "speed": 220, "pickupRadius": 80, "invulnerability": 0.6},
                         "limits": {"hp": 300, "active": 6, "passive": 6, "enemies": 200, "projectiles": 300, "clones": 3,
                                    "speedMultiplier": 1.6, "cooldownReduction": 0.4, "damageReduction": 0.5, "xpMultiplier": 2},
                         "damageLevelMultipliers": [1, 1.2, 1.2, 1.4, 1.4], "targetRadius": 450, "projectileRange": 500,
                         "xpFormula": "ceil(4 + 1.5 * L + 0.12 * L * L)", "freeUpgradeTimes": [5, 15],
                         "status": {"slow": {"duration": 2, "strength": 0.3}, "freeze": {"duration": 1, "bossSlow": 0.3},
                                    "burn": {"duration": 3, "baseDamagePerSecond": 0.2}, "poison": {"duration": 4, "damagePerSecond": 3},
                                    "stun": {"duration": 0.8, "bossImmune": True}, "knockback": {"distance": 40, "bossImmune": True}},
                         "drops": {"healingChance": 0.01, "healingHp": 20, "magnetChance": 0.001, "maxMagnets": 3,
                                   "normalGoldChance": 0.05, "normalGold": 1, "eliteGold": 10, "eliteXp": 20},
                         "pickupSpeed": 350, "bossWarningSeconds": 0.8, "permanentUpgradeMax": 10,
                         "permanentUpgrades": [{"id": "hp", "name": "生命强化", "perLevel": 10}, {"id": "damage", "name": "攻击强化", "perLevel": 0.05},
                                               {"id": "speed", "name": "身法强化", "perLevel": 0.03}, {"id": "xp", "name": "悟性强化", "perLevel": 0.05},
                                               {"id": "cooldown", "name": "聚气强化", "perLevel": 0.02}, {"id": "gold", "name": "聚宝强化", "perLevel": 0.05}]}
        for skill in ACTIVE_SKILLS:
            for level in range(1, 6):
                scenarios.append(_scenario(case_id, f"active-{skill['id']}-L{level}", [14, 15, 81],
                    [advance(15000), snapshot("after")], [assertion("/after/metrics/totalDamage", "gt", 0), assertion("/after/metrics/skillAttacks/" + skill["id"], "gt", 0)],
                    {"player": {"hp": 100, "maxHp": 100, "x": 0, "y": 0}, "skills": {"active": [{"id": skill["id"], "level": level}], "passive": []},
                     "enemyGenerator": {"layout": "grid", "count": 48, "spacing": 30, "hp": 100000, "damage": 0, "speed": 0}, "spawnEnabled": False}))
        for ultimate in ULTIMATE_SKILLS:
            active = [{"id": key, "level": value} for key, value in ultimate["requires"].items() if key.startswith("W")]
            passive = [{"id": key, "level": value} for key, value in ultimate["requires"].items() if key.startswith("P")]
            scenarios.append(_scenario(case_id, "evolution-" + ultimate["id"], [18, 19, 20, 22, 23, 24] + ([21] if ultimate["id"] == "E04" else []),
                [command("chooseUpgrade", id=ultimate["id"]), snapshot("evolved"), advance(15000), snapshot("after")],
                [assertion("/evolved/skills/ultimate", "contains_id", ultimate["id"]), assertion("/after/metrics/totalDamage", "gt", 0), assertion("/evolved/skills/active", "count_lte", 6),
                 assertion("/evolved/skills/passive", "eq", passive)]
                + [assertion("/evolved/skills/active", "not_contains_id", key) for key in ultimate["replaces"]],
                {"phase": "LEVEL_UP", "skills": {"active": active, "passive": passive},
                 "enemyGenerator": {"layout": "grid", "count": 48, "spacing": 30, "hp": 100000, "damage": 0, "speed": 0}, "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "simultaneous-independent-attacks", [4, 26, 85],
            [advance(4500), snapshot("after")],
            [assertion("/after/metrics/skillAttacks/W01", "gte", 2), assertion("/after/metrics/skillAttacks/W02", "gte", 3),
             assertion("/after/metrics/skillAttacks/W03", "gte", 2), assertion("/after/metrics/totalDamage", "gt", 0)],
            {"skills": {"active": [{"id": "W01", "level": 1}, {"id": "W02", "level": 1}, {"id": "W03", "level": 1}], "passive": []},
             "enemies": [{"id": "target", "hp": 100000, "damage": 0, "speed": 0, "x": 40, "y": 0}], "spawnEnabled": False}))
        for passive in PASSIVE_SKILLS:
            for level in range(1, 6):
                stat, value = {
                    "P01": ("maxHp", 100 * (1 + 0.1 * level)),
                    "P02": ("damageMultiplier", 1 + 0.08 * level),
                    "P03": ("damageReduction", 0.05 * level),
                    "P04": ("moveSpeed", 220 * (1 + 0.08 * level)),
                    "P05": ("cooldownReduction", 0.05 * level),
                    "P06": ("healOnKillChance", 0.05 * level),
                    "P07": ("xpMultiplier", 1 + 0.1 * level),
                    "P08": ("rangeMultiplier", 1 + 0.08 * level),
                }[passive["id"]]
                scenarios.append(_scenario(case_id, f"passive-{passive['id']}-L{level}", [16, 17],
                    [snapshot("after")], [assertion("/after/skills/passive", "contains_id", passive["id"]),
                        assertion("/after/effectiveStats/" + stat, "approx", value)],
                    {"skills": {"active": [{"id": "W01", "level": 1}], "passive": [{"id": passive["id"], "level": level}]}, "spawnEnabled": False}))
        normal_routes = [{"level": level["level"], "test_id": f"T{43 + level['level']:02}",
                          "strategy": {"type": "survivors", "upgradePriority": ["E01", "W01", "P05", "W03", "P02", "W10", "P01"], "movement": "avoid-nearest-enemies-collect-xp"},
                          "maxSimSeconds": level["duration"] + 300, "initial_fixture_allowed": False,
                          "assertions": [{"pointer": "/final/phase", "op": "eq", "expected": "VICTORY"}]} for level in WUXIA_LEVELS]
    elif case_id == "GAME-TEST-003":
        configuration = {"levels": POWER_LEVELS, "movementSpeed": 5, "playerRadius": 0.4,
                         "attackRange": 1.2, "pickupRange": 0.8, "attackDuration": 0.6, "absorptionDuration": 0.5,
                         "powerMin": 1, "powerMax": 999999, "comparison": "P > E", "enemyAbsorption": 1,
                         "weapons": [{"name": name, "bonus": bonus} for name, bonus in zip(["木剑", "铁剑", "精钢剑", "战斧", "魔法剑", "圣剑"], [5, 10, 20, 30, 50, 100])],
                         "enemyTypes": ["哥布林", "骷髅兵", "巨型蜘蛛", "兽人战士", "黑暗骑士", "恶魔守卫"]}
        for name, test, player, enemy, expected in [("weaker", 16, 10, 3, 13), ("equal", 17, 10, 10, 10), ("stronger", 18, 10, 15, 10)]:
            scenarios.append(_scenario(case_id, name, [test, 21, 23, 34, 75] + ([9, 10, 20, 76] if player > enemy else [5, 22]),
                [snapshot("before"), command("selectTarget", id="target"), advance(3000), snapshot("after")],
                [assertion("/after/player/power", "eq", expected)] + ([{"pointer": "/after/player/x", "op": "same", "other_pointer": "/before/player/x"}] if player <= enemy else [assertion("/after/entities", "no_active_id", "target")]),
                {"player": {"power": player, "x": 0, "z": 0}, "enemies": [{"id": "target", "power": enemy, "x": 3, "z": 0}], "boss": None}))
        scenarios.append(_scenario(case_id, "weapon-once", [11, 12, 13, 19, 44, 77],
            [command("selectTarget", id="weapon"), advance(3000), snapshot("after"), command("selectTarget", id="weapon"), advance(3000), snapshot("repeat")],
            [assertion("/after/player/power", "eq", 33), assertion("/repeat/player/power", "eq", 33)],
            {"player": {"power": 13, "x": 0, "z": 0}, "weapons": [{"id": "weapon", "bonus": 20, "x": 3, "z": 0}], "boss": None}))
        scenarios.append(_scenario(case_id, "no-automatic-attack", [15, 78], [advance(3000), snapshot("after")],
            [assertion("/after/player/power", "eq", 10), assertion("/after/metrics/enemiesDefeated", "eq", 0)],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "target", "power": 3, "x": 1, "z": 0}], "boss": None}))
        normal_routes = [{"level": level["level"], "test_id": f"T{37 + level['level']:02}",
                          "strategy": {"type": "power_puzzle", "enemyOrder": level["enemyPowers"], "collectWeapons": True, "bossLast": True},
                          "maxSimSeconds": 600,
                          "initial_fixture_allowed": False, "assertions": [{"pointer": "/final/phase", "op": "eq", "expected": "VICTORY"}]} for level in POWER_LEVELS]
    else:
        raise ValueError("No trusted scenario plan for " + case_id)
    # Additional deterministic boundary, navigation and economy executions. These
    # enter real systems through initial fixtures and user commands, never write
    # a result or a test verdict into a generated game.
    if case_id == "GAME-TEST-001":
        scenarios.append(_scenario(case_id, "forward-motion", [2], [snapshot("before"), advance(1000), snapshot("after")],
            [assertion("/after/player/z", "approx", 8)], {"player": {"soldiers": 1, "x": 0, "z": 0}, "boss": None, "trackEnd": 1000}))
        scenarios.append(_scenario(case_id, "group-consumed-once", [10, 11, 47, 50],
            [advance(300), snapshot("first"), advance(500), snapshot("second")],
            [assertion("/first/player/soldiers", "eq", 6), assertion("/second/player/soldiers", "eq", 6)],
            {"player": {"soldiers": 1, "x": -2, "z": 0}, "gates": [
                {"id": "left", "group": "one", "operator": "+", "operand": 5, "x": -2, "z": 1},
                {"id": "right", "group": "one", "operator": "*", "operand": 10, "x": 2, "z": 1}], "boss": None, "trackEnd": 1000}))
        scenarios.append(_scenario(case_id, "alternative-gate-choice", [11, 50], [advance(300), snapshot("after")],
            [assertion("/after/player/soldiers", "eq", 10)],
            {"player": {"soldiers": 1, "x": 2, "z": 0}, "gates": [
                {"id": "left", "group": "one", "operator": "+", "operand": 5, "x": -2, "z": 1},
                {"id": "right", "group": "one", "operator": "*", "operand": 10, "x": 2, "z": 1}], "boss": None, "trackEnd": 1000}))
        variants = [{"call": "variant", "save": "n" + str(n), "milliseconds": 1000,
                     "fixture": {"player": {"soldiers": n, "x": 0, "z": 0}, "enemies": [{"id": "target", "hp": 100000, "x": 0, "z": 20}], "boss": None, "trackEnd": 1000}} for n in [1, 10]]
        scenarios.append(_scenario(case_id, "each-soldier-firepower", [14, 49], variants,
            [{"pointer": "/n10/metrics/shotsFired", "op": "ratio", "other_pointer": "/n1/metrics/shotsFired", "factor": 10},
             {"pointer": "/n10/metrics/totalDamage", "op": "gt_pointer", "other_pointer": "/n1/metrics/totalDamage"}]))
        scenarios.append(_scenario(case_id, "boss-projectile-victory", [19, 20, 52],
            [snapshot("before"), advance(2000), snapshot("after")], [assertion("/after/phase", "eq", "VICTORY"), assertion("/after/metrics/totalDamage", "gte", 2)],
            {"player": {"soldiers": 20, "x": 0, "z": 0}, "boss": {"id": "boss", "hp": 2, "x": 0, "z": 10}, "trackEnd": 1000}))
        scenarios.append(_scenario(case_id, "road-end-boss-alive", [21], [advance(500), snapshot("after")],
            [assertion("/after/phase", "eq", "DEFEAT")],
            {"player": {"soldiers": 1, "x": 0, "z": 0}, "boss": {"id": "boss", "hp": 100000, "x": 0, "z": 10}, "trackEnd": 1}))
        scenarios.append(_scenario(case_id, "upgrade-price-and-effect", [28, 29, 53],
            [command("purchase", id="initialSoldiers"), snapshot("one"), command("purchase", id="initialSoldiers"), snapshot("two"),
             command("purchase", id="weaponDamage"), command("start", level=1), snapshot("started")],
            [assertion("/one/progress/gold", "eq", 9900), assertion("/two/progress/gold", "eq", 9700),
             assertion("/started/player/soldiers", "eq", 3), assertion("/started/effectiveStats/bulletDamage", "eq", 2)],
            {"phase": "UPGRADE", "progress": {"gold": 10000, "upgrades": {"initialSoldiers": 0, "weaponDamage": 0}}}))
        scenarios.append(_scenario(case_id, "restart-temporary-state", [26],
            [advance(3000), command("restart"), snapshot("after")],
            [assertion("/after/player/soldiers", "eq", 1), assertion("/after/player/z", "eq", 0), assertion("/after/metrics/projectiles", "eq", 0)],
            {"player": {"soldiers": 1, "x": 0, "z": 0}, "progress": {"gold": 0, "upgrades": {"initialSoldiers": 0, "weaponDamage": 0}}}))
        scenarios.append(_scenario(case_id, "200-soldiers-and-projectile-pool", [44], [advance(20000), snapshot("after")],
            [assertion("/after/metrics/shotsFired", "gte", 1000), assertion("/after/metrics/projectiles", "lte", 1400), assertion("/after/metrics/updateLoops", "eq", 1)],
            {"player": {"soldiers": 200, "x": 0, "z": 0}, "enemies": [{"id": "far", "hp": 1000000, "x": 0, "z": 500}], "boss": None, "trackEnd": 1000}))
        render_scenario = _scenario(case_id, "200-soldiers-real-renderer", [43], [advance(1000), snapshot("after")],
            [assertion("/after/player/soldiers", "eq", 200), assertion("/renderEvidence/additionalContexts", "gte", 1),
             assertion("/renderEvidence/drawCalls", "gt", 0)],
            {"player": {"soldiers": 200, "x": 0, "z": 0}, "boss": None, "trackEnd": 1000})
        render_scenario["render"] = True
        scenarios.append(render_scenario)
        for route in normal_routes:
            route["acceptance_extra"] = [25, 27, 52]
            route["assertions"] += [assertion("/final/progress/unlockedLevel", "gte", min(route["level"] + 1, 3)),
                {"pointer": "/final/progress/gold", "op": "delta", "other_pointer": "/initial/progress/gold", "expected": route["level"] * 100}]
        scenarios.append(_scenario(case_id, "exact-two-upgrades-and-enemy-health", [16, 29],
            [{"call": "config", "save": "configuration"}], [{"pointer": "/configuration", "op": "case1_configuration", "expected": True}]))
        for level in range(1, 4):
            scenarios.append(_scenario(case_id, "ordinary-enemy-health-L" + str(level), [16],
                [command("start", level=level), snapshot("after")],
                [{"pointer": "/after/entities", "op": "normal_enemy_health", "expected": 10}],
                {"progress": {"unlockedLevel": 3}}))
        for key in ["initialSoldiers", "weaponDamage"]:
            for level in [0, 1, 2, 9, 10]:
                next_level = min(10, level + 1)
                scenarios.append(_scenario(case_id, f"price-{key}-L{level}", [28, 29],
                    [{**command("purchase", id=key), "allow_rejection": level == 10}, snapshot("purchased"), command("start", level=1), snapshot("after")],
                    [assertion("/purchased/progress/gold", "eq", 10000 - (100 * (level + 1) if level < 10 else 0)),
                     assertion("/purchased/progress/upgrades/" + key, "eq", next_level),
                     assertion("/after/player/soldiers", "eq", next_level + 1) if key == "initialSoldiers" else assertion("/after/effectiveStats/bulletDamage", "eq", next_level + 1)],
                    {"phase": "UPGRADE", "progress": {"gold": 10000, "upgrades": {"initialSoldiers": level if key == "initialSoldiers" else 0,
                                                                                         "weaponDamage": level if key == "weaponDamage" else 0}}}))
    elif case_id == "GAME-TEST-003":
        navigation_fixture = {"player": {"power": 10, "x": 0, "z": 0},
            "enemies": [{"id": "target", "power": 3, "x": 6, "z": 0}],
            "obstacles": [{"id": "wall", "kind": "wall", "x": 3, "z": 0, "width": 2, "depth": 4}], "boss": None}
        scenarios.append(_scenario(case_id, "path-around-wall", [4, 6, 7, 26, 27, 28, 31, 33, 78, 79],
            [command("selectTarget", id="target"), {"call": "until", "pointer": "/phase", "expected": "INTERACTING", "milliseconds": 50,
                "maxMilliseconds": 15000, "save": "atAttack", "history": "path"}, advance(2000), snapshot("after")],
            [assertion("/after/player/power", "eq", 13), {"pointer": "/atAttack", "op": "within_entity_range", "expected": {"id": "target", "range": 1.2}},
             {"pointer": "/path", "op": "movement_path", "expected": {"speed": 5, "obstacles": [{"x": 3, "z": 0, "width": 2, "depth": 4}], "radius": 0.4}}], navigation_fixture))
        scenarios.append(_scenario(case_id, "attack-animation-direction", [8, 32, 35],
            [command("selectTarget", id="target"), {"call": "until", "pointer": "/phase", "expected": "INTERACTING", "milliseconds": 50,
                "maxMilliseconds": 5000, "save": "atAttack"}, advance(2000), snapshot("after")],
            [assertion("/atAttack/player/animation", "in", ["attack", "attacking", "ATTACK"]),
             {"pointer": "/atAttack", "op": "faces_entity", "expected": "target"},
             assertion("/after/entities", "no_active_id", "target")],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "target", "power": 3, "x": 3, "z": 0}], "boss": None}))
        scenarios.append(_scenario(case_id, "ground-movement-not-combat", [14, 15, 80, 81],
            [command("selectTarget", ground={"x": 4, "z": 3}), advance(3000), snapshot("after")],
            [assertion("/after/player/x", "approx", 4), assertion("/after/player/z", "approx", 3), assertion("/after/player/power", "eq", 10)],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "near", "power": 3, "x": 5, "z": 4}], "boss": None}))
        steps = []
        for index in range(30):
            steps.extend([command("selectTarget", id="a" if index % 2 == 0 else "b"), advance(50)])
        steps.extend([command("selectTarget", id="b"), advance(6000), snapshot("after")])
        scenarios.append(_scenario(case_id, "repeated-target-changes", [29, 71], steps,
            [assertion("/after/player/power", "eq", 15), assertion("/after/entities", "no_active_id", "b"),
             assertion("/after/entities", "active_id", "a")],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "a", "power": 3, "x": 6, "z": 0}, {"id": "b", "power": 5, "x": -6, "z": 0}], "boss": None}))
        scenarios.append(_scenario(case_id, "unreachable-target-warning", [30],
            [command("selectTarget", id="target"), advance(1000), snapshot("after")],
            [assertion("/after/player/power", "eq", 10), assertion("/events", "event_message", "无法到达目标")],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "target", "power": 3, "x": 6, "z": 0}],
             "obstacles": [{"id": "sealed", "kind": "wall", "x": 6, "z": 0, "width": 4, "depth": 4}], "boss": None}))
        scenarios.append(_scenario(case_id, "boss-success", [36, 82],
            [command("selectTarget", id="boss"), advance(3000), snapshot("after")], [assertion("/after/phase", "eq", "VICTORY")],
            {"player": {"power": 11, "x": 0, "z": 0}, "boss": {"id": "boss", "power": 10, "x": 3, "z": 0}}))
        scenarios.append(_scenario(case_id, "invalid-boss-keeps-player-alive", [37, 82],
            [command("selectTarget", id="boss"), advance(3000), snapshot("after")],
            [assertion("/after/player/power", "eq", 10), assertion("/after/phase", "eq", "PLAYING"), assertion("/after/player/x", "eq", 0)],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [{"id": "weaker", "power": 3, "x": 5, "z": 5}], "boss": {"id": "boss", "power": 150, "x": 3, "z": 0}}))
        scenarios.append(_scenario(case_id, "deadlock-detection", [], [advance(100), snapshot("after")],
            [assertion("/after/phase", "eq", "DEADLOCK")],
            {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [], "weapons": [], "boss": {"id": "boss", "power": 150, "x": 3, "z": 0}}))
        scenarios.append(_scenario(case_id, "restart-restores-original-enemies", [51, 55],
            [command("start", level=1), snapshot("before"), {"call": "selectWeakEnemy"}, advance(15000), snapshot("changed"), command("restart"), snapshot("after")],
            [assertion("/changed/player/power", "gt", 10), assertion("/after/player/power", "eq", 10),
             assertion("/after/entities", "enemy_powers", POWER_LEVELS[0]["enemyPowers"]),
             {"pointer": "/after/progress/unlockedLevel", "op": "same", "other_pointer": "/before/progress/unlockedLevel"}]))
        for route in normal_routes:
            level = POWER_LEVELS[route["level"] - 1]
            route["acceptance_extra"] = [40 + route["level"], 44, 44 + route["level"], 48, 49, 83]
            route["assertions"] += [{"pointer": "/initial/entities", "op": "enemy_powers", "expected": level["enemyPowers"]},
                {"pointer": "/initial/entities", "op": "weapon_bonuses", "expected": level["weapons"]},
                {"pointer": "/initial/entities", "op": "boss_power", "expected": level["bossPower"]},
                assertion("/initial/player/power", "eq", level["initialPower"]),
                assertion("/final/progress/unlockedLevel", "gte", min(route["level"] + 1, 3))]
    else:
        scenarios.append(_scenario(case_id, "starting-skill", [5], [command("start", level=1), snapshot("after")],
            [assertion("/after/skills/active", "contains_id_level", {"id": "W01", "level": 1})]))
        for level, xp in [(1, 6), (2, 8), (3, 10), (5, 15), (10, 31), (20, 82)]:
            scenarios.append(_scenario(case_id, "experience-L" + str(level), [9, 10, 11, 83, 84],
                [advance(500), snapshot("after")],
                [assertion("/after/player/characterLevel", "eq", level + 1), assertion("/after/player/xp", "eq", 0),
                 assertion("/after/phase", "eq", "LEVEL_UP"), assertion("/after/upgradeChoices", "unique_ids", True),
                 assertion("/after/upgradeChoices", "count_lte", 3), assertion("/after/upgradeChoices", "count_gte", 1)],
                {"player": {"hp": 100, "characterLevel": level, "xp": 0, "x": 0, "y": 0},
                 "gems": [{"id": "xp", "xp": xp, "x": 0, "y": 0}], "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "free-upgrade-clock", [12],
            [advance(5100), snapshot("five"), {"call": "chooseFirstUpgrade"}, advance(10100), snapshot("fifteen")],
            [assertion("/five/phase", "eq", "LEVEL_UP"), assertion("/five/player/characterLevel", "eq", 1),
             assertion("/fifteen/phase", "eq", "LEVEL_UP"), assertion("/fifteen/player/characterLevel", "eq", 1),
             {"pointer": "/five/simTime", "op": "approx", "expected": 5, "tolerance": 0.03},
             {"pointer": "/fifteen/simTime", "op": "approx", "expected": 15, "tolerance": 0.03}],
            {"player": {"hp": 100, "characterLevel": 1, "xp": 0}, "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "second-free-new-active-guarantee", [13],
            [advance(300), snapshot("after")],
            [{"pointer": "/after/upgradeChoices", "op": "new_active_choice", "other_pointer": "/after/skills/active"}],
            {"simTime": 14.9, "freeUpgradesGranted": [5], "player": {"characterLevel": 1, "xp": 0},
             "skills": {"active": [{"id": "W01", "level": 1}], "passive": []}, "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "enemy-pursuit", [7, 82], [advance(1000), snapshot("after")],
            [assertion("/after/_entities/target/x", "lt", 200)],
            {"skills": {"active": [], "passive": []}, "player": {"x": 0, "y": 0},
             "enemies": [{"id": "target", "hp": 100000, "speed": 75, "damage": 0, "x": 200, "y": 0}], "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "enemy-kill-drops-experience", [8, 34], [advance(2000), snapshot("after")],
            [assertion("/after/entities", "kind_present", "gem"), assertion("/after/metrics/enemiesDefeated", "gte", 1)],
            {"skills": {"active": [{"id": "W01", "level": 1}], "passive": []}, "player": {"x": 0, "y": 0},
             "enemies": [{"id": "target", "hp": 1, "speed": 0, "damage": 0, "xp": 1, "x": 200, "y": 0}], "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "contact-invulnerability", [31, 32],
            [advance(100), snapshot("first"), advance(400), snapshot("protected"), advance(200), snapshot("second")],
            [assertion("/first/player/hp", "eq", 92), assertion("/protected/player/hp", "eq", 92), assertion("/second/player/hp", "eq", 84)],
            {"skills": {"active": [], "passive": []}, "player": {"hp": 100, "maxHp": 100, "x": 0, "y": 0},
             "enemies": [{"id": "target", "hp": 100000, "speed": 0, "damage": 8, "x": 0, "y": 0}], "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "zero-hp-defeat", [33], [advance(100), snapshot("after")],
            [assertion("/after/phase", "eq", "DEFEAT")], {"skills": {"active": [], "passive": []},
                "player": {"hp": 1, "maxHp": 100, "x": 0, "y": 0}, "enemies": [{"id": "target", "hp": 100000, "speed": 0, "damage": 8, "x": 0, "y": 0}], "spawnEnabled": False}))
        for key, hp, expected in [("healing", 60, 80), ("healing-cap", 95, 100)]:
            scenarios.append(_scenario(case_id, key, [35, 36], [advance(100), snapshot("after")],
                [assertion("/after/player/hp", "eq", expected)], {"player": {"hp": hp, "maxHp": 100, "x": 0, "y": 0},
                    "drops": [{"id": "heal", "kind": "healing", "x": 0, "y": 0}], "spawnEnabled": False}))
        for ultimate in ULTIMATE_SKILLS:
            active = [{"id": key, "level": max(1, value - 1)} for key, value in ultimate["requires"].items() if key.startswith("W")]
            passive = [{"id": key, "level": value} for key, value in ultimate["requires"].items() if key.startswith("P")]
            reject = {**command("chooseUpgrade", id=ultimate["id"]), "allow_rejection": True}
            scenarios.append(_scenario(case_id, "reject-evolution-" + ultimate["id"], [19, 53], [reject, snapshot("after")],
                [assertion("/after/skills/ultimate", "not_contains_id", ultimate["id"]), assertion("/after/skills/active", "eq", active)],
                {"phase": "LEVEL_UP", "skills": {"active": active, "passive": passive}, "spawnEnabled": False}))
        for category, prefix, limit, new_id, tests in [("active", "W", 6, "W07", [24]), ("passive", "P", 6, "P07", [25])]:
            skills = {"active": [], "passive": []}
            skills[category] = [{"id": prefix + str(index).zfill(2), "level": 1} for index in range(1, limit + 1)]
            scenarios.append(_scenario(case_id, category + "-slot-limit", tests,
                [{**command("chooseUpgrade", id=new_id), "allow_rejection": True}, snapshot("after")],
                [assertion("/after/skills/" + category, "count", 6), assertion("/after/skills/" + category, "not_contains_id", new_id)],
                {"phase": "LEVEL_UP", "skills": skills, "spawnEnabled": False}))
        for index, level in enumerate(WUXIA_LEVELS):
            scenarios.append(_scenario(case_id, "scheduled-boss-" + str(index + 1), [39, 43], [advance(500), snapshot("after")],
                [assertion("/after/entities", "kind_present", "boss"), assertion("/after/phase", "ne", "VICTORY")],
                {"level": index + 1, "simTime": level["duration"] - 0.25, "skills": {"active": [], "passive": []}, "player": {"hp": 100, "x": 0, "y": 0}}))
        scenarios.append(_scenario(case_id, "boss-damage-victory", [42], [advance(2000), snapshot("after")],
            [assertion("/after/phase", "eq", "VICTORY")],
            {"skills": {"active": [{"id": "W01", "level": 5}], "passive": []}, "boss": {"id": "boss", "hp": 1, "x": 40, "y": 0}, "spawnEnabled": False}))
        for status, milliseconds, field, expected in [
            ("slow", 1000, "x", 130), ("freeze", 500, "x", 200), ("stun", 500, "x", 200),
            ("burn", 3000, "hp", 88), ("poison", 4000, "hp", 88), ("knockback", 50, "x", 240)]:
            effect = {"type": status, "duration": {"slow": 2, "freeze": 1, "stun": 0.8, "burn": 3, "poison": 4, "knockback": 0.01}[status],
                      "strength": 0.3, "sourceDamage": 20, "damagePerSecond": 3, "distance": 40, "source": {"x": 0, "y": 0}}
            scenarios.append(_scenario(case_id, "status-" + status, [28], [advance(milliseconds), snapshot("after")],
                [assertion("/after/_entities/target/" + field, "approx", expected)],
                {"skills": {"active": [], "passive": []}, "player": {"x": 0, "y": 0}, "spawnEnabled": False,
                 "enemies": [{"id": "target", "kind": "enemy", "hp": 100, "speed": 100 if status in {"slow", "freeze", "stun"} else 0,
                              "damage": 0, "x": 200, "y": 0, "statuses": [effect]}]}))
        for status in ["freeze", "stun", "knockback", "slow"]:
            scenarios.append(_scenario(case_id, "boss-resists-" + status, [29], [advance(100), snapshot("after")],
                [assertion("/after/_entities/boss/statuses", "status_absent", status)] if status != "slow" else
                [{"pointer": "/after/_entities/boss/statuses", "op": "status_strength_lte", "expected": {"type": "slow", "strength": 0.3}}],
                {"skills": {"active": [], "passive": []}, "player": {"x": 0, "y": 0}, "spawnEnabled": False,
                 "boss": {"id": "boss", "hp": 100000, "x": 200, "y": 0,
                          "statuses": [{"type": status, "duration": 2, "strength": 0.8, "distance": 40, "source": {"x": 0, "y": 0}}]}}))
        scenarios.append(_scenario(case_id, "burn-poison-refresh-not-stack", [30], [advance(20000), snapshot("after")],
            [assertion("/after/_entities/target/statuses", "status_present", "poison"), assertion("/after/_entities/target/statuses", "status_present", "burn"),
             assertion("/after/_entities/target/statuses", "unique_status_types", True)],
            {"skills": {"active": [{"id": "W05", "level": 5}, {"id": "W07", "level": 5}], "passive": []},
             "player": {"x": 0, "y": 0}, "spawnEnabled": False, "freeUpgradesGranted": [5, 15],
             "enemies": [{"id": "target", "hp": 1000000, "speed": 0, "damage": 0, "x": 40, "y": 0}]}))
        scenarios.append(_scenario(case_id, "full-screen-experience-pickup", [37], [advance(500), snapshot("after")],
            [assertion("/after/player/xp", "eq", 3), assertion("/after/entities", "no_kind", "gem")],
            {"player": {"characterLevel": 1, "xp": 0, "x": 0, "y": 0}, "spawnEnabled": False,
             "gems": [{"id": "g1", "xp": 1, "x": 500, "y": 0}, {"id": "g2", "xp": 1, "x": -500, "y": 0}, {"id": "g3", "xp": 1, "x": 0, "y": 500}],
             "drops": [{"id": "magnet", "kind": "magnet", "x": 0, "y": 0}]}))
        for index, level in enumerate(WUXIA_LEVELS):
            for when in level["eliteTimes"]:
                scenarios.append(_scenario(case_id, f"elite-L{index+1}-at-{when}", [38], [advance(500), snapshot("after")],
                    [assertion("/after/entities", "kind_present", "elite")],
                    {"level": index + 1, "simTime": when - 0.25, "skills": {"active": [], "passive": []},
                     "freeUpgradesGranted": [5, 15], "player": {"x": 0, "y": 0}}))
        for index, patterns in enumerate([["charge", "slash"], ["blades", "dash"], ["lightning", "summon", "shockwave"]]):
            # Isolate the full attack cycle from contact lethality. Damage and
            # survivability remain separately exercised with real definitions,
            # including the fixture-free three-level completion routes.
            scenarios.append(_scenario(case_id, "boss-attacks-L" + str(index + 1), [40, 41], [advance(20000), snapshot("after")],
                [{"pointer": "/events", "op": "telegraphed_attacks", "expected": {"patterns": patterns, "minimumWarning": 0.8}}],
                {"level": index + 1, "skills": {"active": [], "passive": []}, "freeUpgradesGranted": [5, 15],
                 "player": {"hp": 300, "maxHp": 300, "x": 0, "y": 0}, "boss": {"id": "boss", "hp": 1000000, "damage": 0, "x": 150, "y": 0}, "spawnEnabled": False}))
        scenarios.append(_scenario(case_id, "maximum-density-runtime", [76], [advance(10000), snapshot("after")],
            [assertion("/after/metrics/enemies", "eq", 200), assertion("/after/metrics/projectiles", "lte", 300),
             assertion("/after/player/hp", "gt", 0), assertion("/after/metrics/updateLoops", "eq", 1)],
            {"player": {"hp": 300, "maxHp": 300, "x": 0, "y": 0}, "skills": {"active": [{"id": "W01", "level": 5}, {"id": "W08", "level": 5}], "passive": []},
             "enemyGenerator": {"count": 200, "radius": 200, "hp": 1000000, "damage": 0, "speed": 0}, "spawnEnabled": False, "freeUpgradesGranted": [5, 15]}))
        render_scenario = _scenario(case_id, "200-enemies-real-renderer", [54, 75], [advance(1000), snapshot("after")],
            [assertion("/after/metrics/enemies", "eq", 200), assertion("/renderEvidence/additionalContexts", "gte", 1),
             assertion("/renderEvidence/drawCalls", "gt", 0)],
            {"player": {"hp": 300, "maxHp": 300}, "enemyGenerator": {"count": 200, "radius": 200, "hp": 1000000, "damage": 0, "speed": 0}, "spawnEnabled": False})
        render_scenario["render"] = True
        scenarios.append(render_scenario)
        for upgrade in configuration["permanentUpgrades"]:
            key = upgrade["id"]
            stat, expected_value = {"hp": ("maxHp", 120), "damage": ("damageMultiplier", 1.1),
                "speed": ("moveSpeed", 233.2), "xp": ("xpMultiplier", 1.1),
                "cooldown": ("cooldownReduction", 0.04), "gold": ("goldGainMultiplier", 1.1)}[key]
            scenarios.append(_scenario(case_id, "permanent-upgrade-" + key, [57, 58],
                [command("purchase", id=key), snapshot("one"), command("purchase", id=key), snapshot("two"), command("start", level=1), snapshot("started")],
                [assertion("/one/progress/gold", "eq", 9900), assertion("/two/progress/gold", "eq", 9700),
                 assertion("/started/progress/upgrades/" + key, "eq", 2)],
                {"phase": "UPGRADES", "progress": {"gold": 10000, "upgrades": {key: 0}}}))
            scenarios[-1]["assertions"].append(assertion("/started/effectiveStats/" + stat, "approx", expected_value))
        scenarios.append(_scenario(case_id, "new-run-clears-temporary-skills", [49, 60],
            [command("restart"), snapshot("after")], [assertion("/after/skills/active", "eq", [{"id": "W01", "level": 1}]),
                assertion("/after/skills/passive", "count", 0), assertion("/after/skills/ultimate", "count", 0),
                assertion("/after/player/characterLevel", "eq", 1), assertion("/after/player/xp", "eq", 0)],
            {"skills": {"active": [{"id": "W01", "level": 5}], "passive": [{"id": "P02", "level": 5}], "ultimate": [{"id": "E01"}]},
             "player": {"characterLevel": 10, "xp": 12}, "spawnEnabled": False}))
        for route in normal_routes:
            route["acceptance_extra"] = [47, 56, 80, 88]
            route["assertions"] += [assertion("/final/progress/unlockedLevel", "gte", min(route["level"] + 1, 3))]
            if route["level"] == 1:
                route["acceptance_extra"] += [50, 51, 52]
                route["assertions"] += [{"pointer": "/checkpoints", "op": "progression", "expected": {"activeBy30": 2, "activeBy60": 3, "ultimateBy180": True}}]
        for level in WUXIA_LEVELS:
            for start, end, rate in level["spawnRates"]:
                scenarios.append(_scenario(case_id, f"spawn-rate-L{level['level']}-t{start}", [6],
                    [advance(10000), snapshot("after")],
                    [{"pointer": "/after/entities", "op": "normal_spawn_count", "expected": {"count": rate * 10, "tolerance": 1}},
                     assertion("/after/player/hp", "gt", 0)],
                    {"level": level["level"], "simTime": start + 0.1, "skills": {"active": [], "passive": [{"id": "P03", "level": 5}]},
                     "player": {"hp": 300, "maxHp": 300, "x": 0, "y": 0}, "enemies": [], "freeUpgradesGranted": [5, 15]}))
        for skill_level, passive_level, expected_damage in [(1, 0, 12), (2, 0, 14.4), (4, 0, 16.8), (4, 5, 23.52)]:
            scenarios.append(_scenario(case_id, f"exact-damage-W01L{skill_level}-P02L{passive_level}", [27],
                [snapshot("before"), advance(5000), snapshot("after")],
                [{"pointer": "", "op": "damage_formula", "expected": {"target": "target", "perHit": expected_damage}}],
                {"skills": {"active": [{"id": "W01", "level": skill_level}], "passive": [{"id": "P02", "level": passive_level}] if passive_level else []},
                 "enemies": [{"id": "target", "hp": 100000, "damage": 0, "speed": 0, "x": 100, "y": 0}], "spawnEnabled": False}))
        drop_fixture = {"player": {"hp": 300, "maxHp": 300, "x": 0, "y": 0}, "skills": {"active": [
            {"id": identifier, "level": 5} for identifier in ["W01", "W02", "W08", "W09"]], "passive": []},
            "enemyGenerator": {"count": 200, "radius": 200, "hp": 0.001, "damage": 0, "speed": 0},
            "spawnEnabled": False, "freeUpgradesGranted": [5, 15]}
        scenarios.append(_scenario(case_id, "multi-seed-real-drop-probabilities", [55],
            [{"call": "dropExperiment", "save": "dropSamples", "samples": 50, "enemiesPerSample": 200,
              "maxMilliseconds": 60000, "fixture": drop_fixture}],
            [{"pointer": "/dropSamples", "op": "drop_statistics", "expected": {"samples": 50, "killsPerSample": 200,
                "gold": 0.05, "healing": 0.01, "magnet": 0.001, "standardDeviations": 4.5}}]))
        scenarios.append(_scenario(case_id, "elite-guaranteed-ten-gold", [55],
            [snapshot("before"), advance(2500), snapshot("after")],
            [{"pointer": "", "op": "elite_gold_reward", "expected": 10}],
            {"player": {"hp": 100, "maxHp": 100, "x": 0, "y": 0},
             "skills": {"active": [{"id": "W01", "level": 1}], "passive": []},
             "enemies": [{"id": "elite", "kind": "elite", "hp": 1, "damage": 0, "speed": 0, "x": 100, "y": 0}], "spawnEnabled": False}))
        for ultimate in ULTIMATE_SKILLS:
            passive = [{"id": key, "level": value} for key, value in ultimate["requires"].items() if key.startswith("P")]
            common = {"player": {"hp": 100, "x": 0, "y": 0}, "spawnEnabled": False, "freeUpgradesGranted": [5, 15],
                      "enemyGenerator": {"layout": "grid", "count": 48, "spacing": 25, "hp": 100000, "damage": 0, "speed": 0}}
            base = {**common, "skills": {"active": [{"id": key, "level": 5} for key in ultimate["replaces"]], "passive": passive, "ultimate": []}}
            evolved = {**common, "skills": {"active": [], "passive": passive, "ultimate": [{"id": ultimate["id"]}]}}
            scenarios.append(_scenario(case_id, "evolution-strength-" + ultimate["id"], [86],
                [{"call": "variant", "save": "base", "milliseconds": 5000, "fixture": base, "saveEvents": True, "saveBefore": True},
                 {"call": "variant", "save": "ultimate", "milliseconds": 5000, "fixture": evolved, "saveEvents": True, "saveBefore": True}],
                [{"pointer": "", "op": "evolution_strength", "expected": {"id": ultimate["id"], "baseIds": ultimate["replaces"],
                    "minimumHit": ultimate.get("damage", 0) * (1.24 if ultimate["id"] == "E02" else 1),
                    "damageOverTime": ultimate["id"] == "E04", "minimumTargetDamage": 120}}]))
        memory = _scenario(case_id, "long-rendered-simulation-memory", [79],
            [{"call": "rendererMemory", "durationSeconds": 720, "minimumWallSeconds": 60, "sampleEvery": 30}],
            [{"pointer": "", "op": "bounded_renderer_session", "expected": {"durationSeconds": 720, "minimumWallSeconds": 60, "minimumSamples": 20}}],
            {"level": 3, "player": {"hp": 300, "maxHp": 300, "x": 0, "y": 0}, "spawnEnabled": False,
             "skills": {"active": [{"id": key, "level": 5} for key in ["W01", "W02", "W05", "W08", "W09", "W10"]],
                        "passive": [{"id": key, "level": 5} for key in ["P02", "P03", "P05", "P08"]]},
             "enemyGenerator": {"count": 200, "radius": 200, "hp": 1000000000, "damage": 0, "speed": 0},
             "boss": None})
        memory.update(executor="renderer_memory", render=True)
        scenarios.append(memory)
    for route in normal_routes:
        route["id"] = case_id + "/scenario/normal-level-" + str(route["level"])
        route["acceptance_ids"] = [case_id + "/" + route["test_id"]] + [case_id + f"/T{number:02}" for number in route.get("acceptance_extra", [])]
        route["kind"] = "gameplay"
        level = route["level"]
        process = {"case_id": case_id, "level": level}
        if case_id == "GAME-TEST-001":
            process.update(bossHp=GATE_LEVELS[level - 1]["bossHp"], gates=GATE_LEVELS[level - 1]["gates"])
        elif case_id == "GAME-TEST-002":
            process["bossHp"] = WUXIA_LEVELS[level - 1]["bossHp"]
        else:
            process["bossPower"] = POWER_LEVELS[level - 1]["bossPower"]
        route["assertions"].append({"pointer": "", "op": "normal_level_process", "expected": process})
    normal_observations = []
    if case_id == "GAME-TEST-003":
        for route in normal_routes:
            normal_observations.append({"id": case_id + "/scenario/normal-label-level-" + str(route["level"]),
                "level": route["level"], "kind": "browser", "initial_fixture_allowed": False, "acceptance_ids": [case_id + "/T03"],
                "assertions": [{"pointer": "", "op": "power_label_coverage", "expected": {"roles": ["enemy", "boss"]}}]})
    mapping = {test["id"]: [s["id"] for s in scenarios + normal_routes + normal_observations if test["id"] in s["acceptance_ids"]] for test in spec.acceptance_tests}
    return {"version": 1, "case_id": case_id, "spec_sha256": spec.sha256,
            "expected_configuration": configuration, "scenarios": scenarios, "normal_play_routes": normal_routes,
            "normal_observation_routes": normal_observations,
            "acceptance_mapping": mapping, "test_requirements": spec.acceptance_tests,
            "required_browser_checks": ["engine-runtime-loaded", "portrait-390x844", "landscape-844x390", "touch-input", "desktop-input",
                                        "orientation-preserves-state", "offline-local-assets", "persistence-refresh", "restart-no-duplicate-loops"],
            "uncovered_tests_are": "not_run", "fixture_tests_prove_level_completion": False}


def _pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with /")
    value = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _normal_level_process(trace: dict[str, Any], expected: dict[str, Any]) -> bool:
    """A victory phase alone cannot establish a real level completion.

    Require the actual Boss to have been alive, intentionally fought and defeated.
    Runner levels additionally expose their full prescribed gate groups and real
    one-per-group arithmetic transitions. Only ordinary input traces qualify.
    """
    if trace.get("mode") != "normal_play" or trace.get("fixture") is not None:
        return False
    inputs = trace["inputs"]
    if not inputs or inputs[0].get("name") != "start" or inputs[0].get("args", {}).get("level") != expected["level"]:
        return False
    states = [trace["initial"], *trace["checkpoints"], *trace.get("process_observations", []), trace["final"]]
    if states[0]["phase"] != "PLAYING" or states[-1]["phase"] != "VICTORY":
        return False
    alive = [entity for state in states for entity in state["entities"]
             if (entity.get("kind") == "boss" or entity.get("group") == "boss")
             and entity.get("active", True) and entity.get("hp", 1) > 0]
    # A sufficiently strong legitimate build can defeat a newly spawned Boss
    # inside a single fixed-input interval. A real production spawn event with
    # its entity values is also an observation; a config entry is not.
    events = trace["events"]
    for event in events:
        data = event.get("data", {})
        entity = data.get("entity", data)
        if "spawn" in str(event.get("type", "")).lower() and entity.get("kind") == "boss" and entity.get("hp", 0) > 0:
            boss = dict(entity)
            boss.setdefault("id", entity.get("entityId", entity.get("bossId")))
            if boss["id"] is not None:
                alive.append(boss)
    ids = {entity["id"] for entity in alive}
    if len(ids) != 1:
        return False
    boss_id = next(iter(ids))
    def event_target(event):
        data = event.get("data", {})
        for key in ("targetId", "entityId", "bossId", "enemyId", "id", "target"):
            value = data.get(key)
            if value is not None:
                return value.get("id") if isinstance(value, dict) else value
        return None

    deaths = [event for event in events if event_target(event) == boss_id
              and any(word in str(event.get("type", "")).lower() for word in ("death", "defeat", "kill"))]
    final_boss = [entity for entity in states[-1]["entities"] if entity["id"] == boss_id]
    if not deaths or any(entity.get("active", True) and entity.get("hp", 1) > 0 for entity in final_boss):
        return False
    if expected["case_id"] == "GAME-TEST-003":
        if not all(entity["power"] == expected["bossPower"] for entity in alive):
            return False
        selected = [item for item in inputs if item.get("name") == "selectTarget" and item.get("args", {}).get("id") == boss_id]
        if not selected:
            return False
        power = selected[-1]["before"]["playerPower"]
        return power > expected["bossPower"] and states[-1]["player"]["power"] >= power + expected["bossPower"]

    declared_maximum = [entity.get("maxHp", entity.get("maxHP")) for entity in alive
                        if "maxHp" in entity or "maxHP" in entity]
    if any(value != expected["bossHp"] for value in declared_maximum):
        return False
    maximum = [entity.get("maxHp", entity.get("maxHP", entity["hp"])) for entity in alive]
    if not any(value == expected["bossHp"] for value in maximum):
        raise KeyError("Boss original maxHp (or an undamaged alive snapshot) is needed to verify the prescribed level HP")
    damage = [event.get("data", {}).get("amount", event.get("data", {}).get("damage")) for event in events
              if event_target(event) == boss_id and "damage" in str(event.get("type", "")).lower()]
    if not damage or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in damage):
        return False
    if sum(damage) + 1e-6 < expected["bossHp"]:
        return False
    if expected["case_id"] == "GAME-TEST-002":
        return states[-1]["player"]["hp"] > 0
    if states[-1]["player"]["soldiers"] <= 0:
        return False

    normalize = lambda op: {"×": "*", "÷": "/"}.get(op, op)
    groups = {}
    gates = {}
    for state in states:
        for entity in state["entities"]:
            if entity.get("kind") != "gate":
                continue
            group = str(entity["group"])
            groups.setdefault(group, {"z": entity["z"], "options": set()})["options"].add((normalize(entity["operator"]), entity["operand"]))
            gates[entity["id"]] = entity
    ordered = sorted(groups.values(), key=lambda group: group["z"])
    prescribed = [{(normalize(value[0]), int(value[1:])) for value in pair} for pair in expected["gates"]]
    if len(ordered) != len(prescribed) or any(group["options"] != pair for group, pair in zip(ordered, prescribed)):
        return False
    triggered = set()
    transitions = 0
    for event in events:
        if "gate" not in str(event.get("type", "")).lower():
            continue
        data = event.get("data", {})
        gate_id = data.get("gateId", data.get("id", data.get("entityId")))
        gate = gates.get(gate_id)
        if gate is None:
            continue
        group = str(gate["group"])
        if group in triggered:
            return False
        old = data.get("oldCount", data.get("before"))
        new = data.get("newCount", data.get("result", data.get("after")))
        if not isinstance(old, int) or isinstance(old, bool) or not isinstance(new, int) or isinstance(new, bool):
            raise KeyError("Real gate transition event oldCount/newCount")
        op, operand = normalize(gate["operator"]), gate["operand"]
        calculated = old + operand if op == "+" else old - operand if op == "-" else old * operand if op == "*" else math.floor(old / operand)
        if new != max(0, min(200, calculated)):
            return False
        triggered.add(group)
        transitions += 1
    return transitions > 0


def evaluate_assertion(document: Any, assertion: dict[str, Any]) -> bool:
    """Run a fixed, non-evaluating assertion vocabulary against real observations."""
    actual = _pointer(document, assertion["pointer"])
    expected = assertion.get("expected")
    operator = assertion["op"]
    if operator == "normal_level_process":
        return _normal_level_process(actual, expected)
    if operator == "power_label_coverage":
        # A following camera need not show the entire map simultaneously.
        # Each actual initial hostile must instead have been observed with its
        # own correct, visible label while it was still active during the route.
        if actual.get("mode") != "normal_play" or actual.get("fixture") is not None:
            return False
        required = {entity["id"] for entity in actual["initial"]["entities"]
                    if entity.get("kind") in expected["roles"] and entity.get("active", True)}
        if not required:
            return False
        seen = set()
        for frame in actual["power_label_frames"]:
            active = {entity["id"]: entity for entity in frame["state"]["entities"]
                      if entity.get("active", True) and entity.get("kind") in expected["roles"]}
            for observation in frame["observation"]["entities"]:
                entity = active.get(observation["id"])
                if entity is None or not observation["inView"] or observation["power"] != entity["power"]:
                    continue
                if any(label["power"] == entity["power"] and label.get("id") == entity["id"]
                       for label in observation["matchingLabels"]):
                    seen.add(entity["id"])
        return required.issubset(seen)
    if operator == "case1_configuration":
        upgrades = actual.get("permanentUpgrades", actual.get("upgrades"))
        if upgrades is None:
            raise KeyError("config.permanentUpgrades")
        upgrades = list(upgrades.values()) if isinstance(upgrades, dict) else upgrades
        if len(upgrades) != 2 or {row["id"] for row in upgrades} != {"initialSoldiers", "weaponDamage"}:
            return False
        enemies = actual.get("enemies", actual.get("enemyTypes", []))
        enemies = list(enemies.values()) if isinstance(enemies, dict) else enemies
        ordinary = [row for row in enemies if row.get("kind", "enemy") not in {"boss", "elite"}]
        if not ordinary:
            if "normalEnemyHp" not in actual:
                raise KeyError("config.enemies ordinary definitions or normalEnemyHp")
            return actual["normalEnemyHp"] == 10
        return all(row["hp"] == 10 for row in ordinary)
    if operator == "normal_enemy_health":
        ordinary = [row for row in actual if row.get("kind") == "enemy" and row.get("active", True)]
        if not ordinary:
            raise KeyError("initial production ordinary enemy entities")
        return all(row.get("maxHp", row["hp"]) == expected and 0 < row["hp"] <= expected for row in ordinary)
    if operator == "normal_spawn_count":
        count = sum(1 for row in actual if row.get("kind") == "enemy" and row.get("active", True)
                    and not row.get("defeated") and not row.get("elite") and not row.get("isElite"))
        return abs(count - expected["count"]) <= expected["tolerance"]
    if operator == "damage_formula":
        target, per_hit = expected["target"], expected["perHit"]
        before = next(row for row in actual["before"]["entities"] if row["id"] == target)
        after = next(row for row in actual["after"]["entities"] if row["id"] == target)
        hits = []
        for event in actual["events"]:
            data = event.get("data", {})
            if "damage" in str(event.get("type", "")).lower() and data.get("targetId", data.get("target")) == target:
                hits.append(data.get("amount", data.get("damage")))
        if not hits:
            raise KeyError("actual per-target damage events {targetId,amount}")
        return (all(isinstance(hit, (int, float)) and not isinstance(hit, bool) and abs(hit - per_hit) <= 1e-6 for hit in hits)
                and abs(before["hp"] - after["hp"] - sum(hits)) <= 1e-5 and after["hp"] < before["hp"])
    if operator == "drop_statistics":
        if len(actual) != expected["samples"] + 1 or len({sample["seed"] for sample in actual[:-1]}) != expected["samples"]:
            return False
        outcomes = []
        aliases = {"gold": "gold", "coin": "gold", "healing": "healing", "heal": "healing", "medicine": "healing", "magnet": "magnet"}
        for sample in actual:
            if sample["after"]["metrics"]["enemiesDefeated"] - sample["before"]["metrics"]["enemiesDefeated"] != expected["killsPerSample"]:
                return False
            drops, witnessed = {kind: 0 for kind in ["gold", "healing", "magnet"]}, False
            ids = set()
            for event in sample["events"]:
                data = event.get("data", {})
                event_type = str(event.get("type", "")).lower()
                if "drop" not in event_type or any(word in event_type for word in ["pick", "collect", "despawn", "remove"]):
                    continue
                kind = aliases.get(data.get("kind", data.get("itemType", data.get("dropType"))))
                if not kind:
                    continue
                identity = data.get("id", data.get("dropId", event.get("seq")))
                if identity in ids:
                    continue
                ids.add(identity)
                witnessed = True
                if kind == "gold" and data.get("value", data.get("amount", 1)) != 1:
                    return False
                drops[kind] += 1
            if not witnessed:
                # Uncollected physical items are an independent observation of
                # actual production loot. These fixtures keep player stationary
                # and kills outside pickup range; collected items require events.
                for row in sample["after"]["entities"]:
                    kind = aliases.get(row.get("kind"))
                    if kind and row.get("active", True) and not row.get("collected"):
                        witnessed = True
                        if kind == "gold" and row.get("value", row.get("amount", 1)) != 1:
                            return False
                        drops[kind] += 1
            if not witnessed:
                raise KeyError("actual drop-created events or uncollected drop entities")
            outcomes.append(drops)
        if actual[-1]["seed"] != actual[0]["seed"] or outcomes[-1] != outcomes[0]:
            return False
        trials = expected["samples"] * expected["killsPerSample"]
        for kind in ["gold", "healing", "magnet"]:
            observed = sum(sample[kind] for sample in outcomes[:-1])
            probability = expected[kind]
            sigma = math.sqrt(trials * probability * (1 - probability))
            if observed == 0 or abs(observed - trials * probability) > expected["standardDeviations"] * sigma + 0.5:
                return False
        return True
    if operator == "elite_gold_reward":
        before, after = actual["before"], actual["after"]
        if after["metrics"]["enemiesDefeated"] - before["metrics"]["enemiesDefeated"] != 1:
            return False
        gold = [event["data"] for event in actual["events"] if "drop" in str(event.get("type", "")).lower()
                and not any(word in str(event.get("type", "")).lower() for word in ["pick", "collect", "remove"])
                and event.get("data", {}).get("kind", event.get("data", {}).get("itemType")) in {"gold", "coin"}]
        if gold:
            return sum(row.get("value", row.get("amount", 1)) for row in gold) == expected
        items = [row for row in after["entities"] if row.get("kind") in {"gold", "coin"} and row.get("active", True)]
        if items:
            return sum(row.get("value", row.get("amount", 1)) for row in items) == expected
        return after["progress"]["gold"] - before["progress"]["gold"] == expected
    if operator == "evolution_strength":
        def damage_events(events, ids):
            return [event["data"] for event in events if "damage" in str(event.get("type", "")).lower()
                    and event.get("data", {}).get("skillId", event.get("data", {}).get("sourceSkill")) in ids
                    and isinstance(event["data"].get("amount", event["data"].get("damage")), (int, float))]
        base_hits = damage_events(actual["base_events"], expected["baseIds"])
        ultimate_hits = damage_events(actual["ultimate_events"], [expected["id"]])
        if not base_hits or not ultimate_hits:
            raise KeyError("per-target real damage events {targetId,skillId,amount} for both base and evolved attacks")
        amount = lambda row: row.get("amount", row.get("damage"))
        for name, hits in [("base", base_hits), ("ultimate", ultimate_hits)]:
            totals = {}
            for row in hits:
                if isinstance(amount(row), bool) or not math.isfinite(amount(row)) or amount(row) <= 0:
                    return False
                target = row.get("targetId", row.get("target"))
                if target is None:
                    raise KeyError("damage event targetId")
                totals[target] = totals.get(target, 0) + amount(row)
            before = {row["id"]: row for row in actual[name + "_before"]["entities"]}
            after = {row["id"]: row for row in actual[name]["entities"]}
            if any(target not in before or target not in after or not math.isfinite(before[target]["hp"]) or not math.isfinite(after[target]["hp"])
                   or abs(before[target]["hp"] - after[target]["hp"] - total) > 1e-4 for target, total in totals.items()):
                return False
        if expected["damageOverTime"]:
            totals = {}
            for row in ultimate_hits:
                target = row.get("targetId", row.get("target"))
                totals[target] = totals.get(target, 0) + amount(row)
            return len(totals) >= 2 and max(totals.values()) >= expected["minimumTargetDamage"] and max(totals.values()) > max(map(amount, base_hits)) * 2
        return max(map(amount, ultimate_hits)) >= expected["minimumHit"] - 1e-6 and max(map(amount, ultimate_hits)) > max(map(amount, base_hits)) * 1.4
    if operator == "bounded_renderer_session":
        samples = actual["samples"]
        if len(samples) < expected["minimumSamples"] or actual["elapsedWallSeconds"] < expected["minimumWallSeconds"]:
            return False
        if samples[-1]["snapshot"]["simTime"] - samples[0]["snapshot"]["simTime"] < expected["durationSeconds"] - 1:
            return False
        if actual["drawCalls"] < 100 or actual["additionalContexts"] < 1 or not actual["noninterference"]["liveStateUnchanged"] or not actual["noninterference"]["storageUnchanged"]:
            return False
        warmed = [row for row in samples if row["snapshot"]["simTime"] - samples[0]["snapshot"]["simTime"] >= 60]
        baseline = warmed[0]
        resources = baseline["engineInfo"]["resources"]
        if len(resources) < 3 or baseline["engineInfo"].get("running") is not True:
            raise KeyError("real renderer engineInfo.running/resources")
        for row in warmed:
            state = row["snapshot"]
            if state["phase"] != "PLAYING" or not math.isfinite(state["player"]["hp"]) or state["player"]["hp"] <= 0:
                return False
            if row["heap"]["usedSize"] > max(baseline["heap"]["usedSize"] * 1.5, baseline["heap"]["usedSize"] + 8_000_000):
                return False
            if row["dom"]["nodes"] > baseline["dom"]["nodes"] + 500 or row["dom"]["jsEventListeners"] > baseline["dom"]["jsEventListeners"] + 30:
                return False
            for key, value in resources.items():
                current = row["engineInfo"]["resources"][key]
                if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0 for number in [value, current]):
                    return False
                if current > max(value * 1.25, value + 40):
                    return False
        return samples[-1]["snapshot"]["metrics"]["totalDamage"] > baseline["snapshot"]["metrics"]["totalDamage"]
    if operator == "eq":
        return type(actual) is type(expected) and actual == expected or (
            not isinstance(actual, bool) and not isinstance(expected, bool) and isinstance(actual, (int, float)) and isinstance(expected, (int, float)) and actual == expected)
    if operator == "ne":
        return not evaluate_assertion(document, {**assertion, "op": "eq"})
    if operator == "same":
        return actual == _pointer(document, assertion["other_pointer"])
    if operator in {"gt_pointer", "ratio", "delta"}:
        other = _pointer(document, assertion["other_pointer"])
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (actual, other)):
            return False
        if operator == "gt_pointer":
            return actual > other
        if operator == "delta":
            return abs(actual - other - expected) <= assertion.get("tolerance", 1e-6)
        return other > 0 and abs(actual - other * assertion["factor"]) <= assertion.get("tolerance", 1e-6)
    if operator in {"gt", "gte", "lt", "lte", "approx"}:
        if isinstance(actual, bool) or isinstance(expected, bool) or not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
            return False
        if not math.isfinite(actual) or not math.isfinite(expected):
            return False
        if operator == "approx":
            tolerance = assertion.get("tolerance", 1e-6)
            if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
                raise ValueError("Invalid numeric assertion tolerance")
            return abs(actual - expected) <= tolerance
        return {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected, "lte": actual <= expected}[operator]
    if operator == "in":
        return actual in expected
    if operator == "contains":
        return expected in actual
    if operator in {"contains_id", "not_contains_id"}:
        contains = isinstance(actual, list) and any(isinstance(item, dict) and item.get("id") == expected for item in actual)
        return contains if operator == "contains_id" else isinstance(actual, list) and not contains
    if operator == "contains_id_level":
        return any(item.get("id") == expected["id"] and item.get("level") == expected["level"] for item in actual if isinstance(item, dict))
    if operator in {"active_id", "no_active_id"}:
        active = any(item.get("id") == expected and item.get("active", True) and not item.get("defeated") and not item.get("collected") for item in actual)
        return active if operator == "active_id" else not active
    if operator == "kind_present":
        return any(item.get("kind") == expected and item.get("active", True) for item in actual)
    if operator == "no_kind":
        return not any(item.get("kind") == expected and item.get("active", True) for item in actual)
    if operator in {"status_present", "status_absent", "unique_status_types", "status_strength_lte"}:
        if not isinstance(actual, list):
            return False
        types = [status.get("type") for status in actual]
        if operator == "unique_status_types":
            return len(types) == len(set(types))
        if operator == "status_present":
            return expected in types
        if operator == "status_absent":
            return expected not in types
        matching = [status for status in actual if status.get("type") == expected["type"]]
        return len(matching) == 1 and 0 < matching[0]["strength"] <= expected["strength"]
    if operator == "unique_ids":
        ids = [item["id"] if isinstance(item, dict) else item for item in actual]
        return len(ids) == len(set(ids))
    if operator == "new_active_choice":
        owned = {item["id"] for item in _pointer(document, assertion["other_pointer"])}
        return any(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].startswith("W") and item["id"] not in owned for item in actual)
    if operator == "event_message":
        return any(expected in str(event.get("data", {}).get("message", "")) for event in actual)
    if operator == "telegraphed_attacks":
        normalize = lambda value: re.sub(r"[^a-z]", "", str(value).lower())
        warnings = [event for event in actual if "warning" in normalize(event.get("type")) or "telegraph" in normalize(event.get("type"))]
        attacks = [event for event in actual if "attack" in normalize(event.get("type")) and "warning" not in normalize(event.get("type"))
                   and ("boss" in normalize(event.get("type")) or event.get("data", {}).get("actorKind") == "boss")]
        if not set(expected["patterns"]).issubset({event.get("data", {}).get("pattern") for event in attacks}):
            return False
        for attack in attacks:
            pattern = attack.get("data", {}).get("pattern")
            matching = [warning for warning in warnings if warning.get("data", {}).get("pattern") == pattern
                and warning.get("data", {}).get("duration", 0) >= expected["minimumWarning"]
                and expected["minimumWarning"] - 0.025 <= attack["simTime"] - warning["simTime"] <= warning["data"]["duration"] + 0.05]
            if not matching:
                return False
        return bool(attacks)
    if operator == "progression":
        if not isinstance(actual, list) or not actual:
            return False
        for key, limit in [("activeBy30", 30), ("activeBy60", 60)]:
            if key in expected and not any(0 <= state["simTime"] <= limit + 0.025 and len(state.get("skills", {}).get("active", [])) >= expected[key] for state in actual):
                return False
        if expected.get("ultimateBy180") and not any(0 <= state["simTime"] <= 180.025 and state.get("skills", {}).get("ultimate") for state in actual):
            return False
        return True
    if operator == "within_entity_range":
        target = next(entity for entity in actual["entities"] if entity["id"] == expected["id"])
        player = actual["player"]
        return math.hypot(player["x"] - target["x"], player["z"] - target["z"]) <= expected["range"] + 0.025
    if operator == "faces_entity":
        target = next(entity for entity in actual["entities"] if entity["id"] == expected)
        player = actual["player"]
        dx, dz = target["x"] - player["x"], target["z"] - player["z"]
        facing = player.get("facing")
        if isinstance(facing, dict):
            fx, fz = facing["x"], facing["z"]
        else:
            fx, fz = math.sin(player["rotationY"]), math.cos(player["rotationY"])
        return (dx * fx + dz * fz) / max(1e-9, math.hypot(dx, dz) * math.hypot(fx, fz)) >= 0.98
    if operator == "movement_path":
        if len(actual) < 2:
            return False
        for previous, current in zip(actual, actual[1:]):
            a, b = previous["player"], current["player"]
            dt = current["simTime"] - previous["simTime"]
            if dt < 0 or math.hypot(a["x"] - b["x"], a["z"] - b["z"]) > expected["speed"] * dt + 0.03:
                return False
            for obstacle in expected.get("obstacles", []):
                closest_x = min(obstacle["x"] + obstacle["width"] / 2, max(obstacle["x"] - obstacle["width"] / 2, b["x"]))
                closest_z = min(obstacle["z"] + obstacle["depth"] / 2, max(obstacle["z"] - obstacle["depth"] / 2, b["z"]))
                if math.hypot(b["x"] - closest_x, b["z"] - closest_z) < expected.get("radius", 0) - 0.015:
                    return False
        return True
    if operator in {"enemy_powers", "weapon_bonuses", "boss_power"}:
        if operator == "enemy_powers":
            return sorted(entity["power"] for entity in actual if entity.get("kind") == "enemy" and entity.get("active", True)) == sorted(expected)
        if operator == "weapon_bonuses":
            return sorted(entity.get("bonus", entity.get("powerBonus")) for entity in actual if entity.get("kind") == "weapon" and entity.get("active", True)) == sorted(expected)
        bosses = [entity for entity in actual if entity.get("kind") == "boss" and entity.get("active", True)]
        return len(bosses) == 1 and bosses[0]["power"] == expected
    if operator == "nonempty":
        return bool(actual)
    if operator in {"count", "count_lte", "count_gte"}:
        count = len(actual)
        return count == expected if operator == "count" else count <= expected if operator == "count_lte" else count >= expected
    raise ValueError("Unsupported assertion operator: " + str(operator))


def evaluate_evidence(spec: SpecDocument | dict[str, Any], reports: dict[str, Any]) -> dict[str, Any]:
    """Evaluate hashed JSON evidence; no pass/score field from a model is trusted.

    Each check binds both source hashes, supplies an executed trusted producer,
    and points assertions into files under artifact_root. A check containing only
    an inline boolean cannot pass. Multiple checks for one test all must pass.
    A manual check additionally requires a named observer and explicit notes.
    """
    data = spec.to_dict() if isinstance(spec, SpecDocument) else spec
    spec_hash = data["sha256"]
    project_hash = reports.get("project_sha256")
    valid_context = reports.get("spec_sha256") == spec_hash and isinstance(project_hash, str) and len(project_hash) == 64 and all(c in "0123456789abcdef" for c in project_hash)
    root = Path(reports.get("artifact_root", ".")).resolve()
    checks = reports.get("checks", [])
    if not isinstance(checks, list):
        raise ValueError("checks must be a list")
    plan_error = None
    try:
        plan = scenario_plan(data["case_id"]) if data["case_id"] in PLAN_SOURCE_HASHES else None
    except (OSError, ValueError) as exc:
        plan, plan_error, valid_context = None, str(exc), False
    if plan and plan["spec_sha256"] != spec_hash:
        valid_context = False
    trusted_scenarios = {scenario["id"]: scenario for scenario in plan["scenarios"] + plan["normal_play_routes"] + plan.get("normal_observation_routes", [])} if plan else {}
    results, failures = [], []
    for test in data["acceptance_tests"]:
        matching = [check for check in checks if check.get("requirement_id") == test["id"]]
        visual_allowed = test["test_id"] in VISUAL_TESTS.get(data["case_id"], {})
        requires_mechanical = VISUAL_TESTS.get(data["case_id"], {}).get(test["test_id"], True)
        manual = "manual" in test["verification"] and not visual_allowed
        result = {"id": test["id"], "test_id": test["test_id"], "text": test["text"], "source": test["source"],
                  "status": "manual_required" if manual else "visual_required" if visual_allowed and not requires_mechanical else "not_run", "evidence": [], "errors": []}
        if manual:
            result["manual_instructions"] = "On a named physical mainstream mobile device, play every level in portrait and landscape; record device/browser versions, frame-time capture and observed defects. Screenshots or desktop emulation do not establish real-device smoothness."
        if not matching:
            results.append(result)
            continue
        required_scenarios = plan["acceptance_mapping"].get(test["id"], []) if plan else []
        executed_scenarios = set()
        mechanical_passed, visual_passed = False, False
        for check in matching:
            errors, documents = [], {}
            if not valid_context:
                errors.append("Specification/project source hash binding is missing or stale")
            if check.get("project_sha256") != project_hash or check.get("spec_sha256", spec_hash) != spec_hash:
                errors.append("Check source hash does not match the evaluated build")
            if check.get("executed") is not True:
                errors.append("Check was not executed")
            if check.get("producer") not in {"playwright", "node", "subprocess", "human", "vision_reviewer"}:
                errors.append("Evidence must originate from an executed browser, process or human observation")
            if check.get("kind") not in {"runtime", "gameplay", "browser", "engine", "build", "manual", "visual"}:
                errors.append("Unknown evidence kind")
            if check.get("kind") == "visual" and (not visual_allowed or check.get("producer") != "vision_reviewer"):
                errors.append("Visual review cannot replace gameplay, numerical, device-performance or human tests")
            if check.get("producer") == "vision_reviewer" and check.get("kind") != "visual":
                errors.append("Vision reviewer cannot issue mechanical or human evidence")
            if manual and check.get("kind") != "manual":
                errors.append("This criterion requires a human visual/device observation")
            if check.get("kind") == "manual" and not (check.get("observer") and check.get("notes") and check.get("observed_at")):
                errors.append("Manual verification needs observer, timestamp and concrete notes")
            artifacts = check.get("artifacts", [])
            if not artifacts:
                errors.append("No hashed artifact was supplied")
            for artifact in artifacts:
                try:
                    candidate = root / artifact["path"]
                    path = candidate.resolve()
                    if not path.is_relative_to(root) or candidate.is_symlink():
                        raise ValueError("Artifact path escapes its evidence directory")
                    payload = path.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                        raise ValueError("Artifact content hash does not match")
                    if path.suffix == ".json":
                        document = json.loads(payload)
                        # Generated game code cannot silently attach a trace from a
                        # different source snapshot to this run.
                        if isinstance(document, dict) and document.get("project_sha256", project_hash) != project_hash:
                            raise ValueError("Artifact belongs to a different source snapshot")
                        documents[artifact["path"]] = document
                    result["evidence"].append({"path": artifact["path"], "sha256": artifact["sha256"]})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(str(exc))
            if check.get("kind") == "visual":
                errors.extend(_visual_evidence_errors(test, check, documents, artifacts, project_hash, spec_hash))
            assertions = check.get("assertions", [])
            if not assertions:
                errors.append("No mechanical assertions were executed")
            scenario = trusted_scenarios.get(check.get("scenario_id"))
            if scenario:
                expected_assertions = {json.dumps(assertion, sort_keys=True) for assertion in scenario["assertions"]}
                supplied_assertions = {json.dumps({key: value for key, value in assertion.items() if key != "artifact"}, sort_keys=True) for assertion in assertions}
                if not expected_assertions.issubset(supplied_assertions):
                    errors.append("Check omitted or changed a harness-owned scenario assertion")
                if scenario.get("initial_fixture_allowed") is False and (check.get("mode") != "normal_play" or check.get("fixture") is not None):
                    errors.append("Level completion requires a normal-input run without an initial fixture")
            for assertion in assertions:
                try:
                    document = documents[assertion["artifact"]]
                    if not evaluate_assertion(document, assertion):
                        errors.append("Assertion failed: " + json.dumps(assertion, ensure_ascii=False, sort_keys=True))
                except (KeyError, IndexError, TypeError, ValueError, StopIteration, ZeroDivisionError, OverflowError) as exc:
                    errors.append("Invalid evidence assertion: " + str(exc))
            if not errors and scenario:
                executed_scenarios.add(scenario["id"])
            if not errors:
                if check.get("kind") == "visual":
                    visual_passed = True
                else:
                    mechanical_passed = True
            result["errors"].extend(errors)
        missing = sorted(set(required_scenarios) - executed_scenarios)
        result["missing_scenarios"] = missing
        result["status"] = ("failed" if result["errors"] else "not_run" if missing or visual_allowed and requires_mechanical and not mechanical_passed
                            else "visual_required" if visual_allowed and not visual_passed else "passed")
        if result["errors"]:
            failures.append(test["id"])
        results.append(result)
    counts = {status: sum(item["status"] == status for item in results) for status in ("passed", "failed", "not_run", "manual_required", "visual_required")}
    complete = bool(results) and counts["passed"] == len(results)
    return {"version": 1, "case_id": data["case_id"], "spec_sha256": spec_hash, "project_sha256": project_hash, "source_sha256": project_hash,
            "status": "passed" if complete else "failed" if failures else "incomplete", "complete": complete,
            "counts": counts, "tests": results, "unexecuted_is_passed": False,
            "source_requirement_count": len(data["requirements"]), "source_constraint_count": len(data["constraints"]),
            "trusted_plan_error": plan_error,
            "scope": "The complete numbered section-14 acceptance suite; raw section requirements remain in the implementation plan"}


def _visual_evidence_errors(test, check, documents, artifacts, project_hash, spec_hash):
    """Re-check a separate AI reviewer without treating it as a human observer."""
    errors = []
    try:
        review = documents[check["review_artifact"]]
        prompt = documents[review["prompt_artifact"]]
        if review.get("producer") != "vision_reviewer" or review.get("reviewer") != "vision_reviewer" or review.get("role") != "qa" or review.get("executed") is not True:
            errors.append("Review provenance must identify an executed independent QA vision reviewer")
        if review.get("project_sha256") != project_hash or review.get("spec_sha256") != spec_hash:
            errors.append("Visual review source hashes do not match")
        if not review.get("model") or review.get("model") != check.get("model") or review.get("model") != prompt.get("model"):
            errors.append("Visual model provenance is missing or inconsistent")
        identity = prompt.get("prompt_identity")
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not isinstance(identity, dict) or digest != review.get("prompt_sha256") or digest != check.get("prompt_sha256") or digest != prompt.get("prompt_sha256"):
            errors.append("Visual prompt identity does not match the recorded exact prompt hash")
        payload = prompt.get("payload", {})
        if payload.get("project_sha256") != project_hash or payload.get("spec_sha256") != spec_hash or not any(c.get("id") == test["id"] for c in payload.get("criteria", [])):
            errors.append("Visual prompt did not request this criterion against these sources")
        hashes = {artifact["path"]: artifact["sha256"] for artifact in artifacts}
        shots = {shot["path"]: shot for shot in review.get("screenshots", [])}
        if not shots or any(hashes.get(path) != shot.get("sha256") for path, shot in shots.items()):
            errors.append("Every reviewed screenshot must be a verified hashed artifact")
        if isinstance(identity, dict) and identity.get("image_sha256") != [shot.get("sha256") for shot in review.get("screenshots", [])]:
            errors.append("Prompt image identities differ from reviewed screenshots")
        decisions = [item for item in review.get("reviews", []) if item.get("requirement_id") == test["id"]]
        if len(decisions) != 1:
            errors.append("Visual review must contain exactly one decision for this criterion")
            return errors
        decision = decisions[0]
        if decision.get("valid") is not True or decision.get("verdict") != "pass" or decision.get("errors"):
            errors.append("The independent visual criterion did not pass its evidence checks")
        facts = decision.get("facts", [])
        locations, views, scenes = set(), set(), set()
        if len(facts) < 2:
            errors.append("Visual evidence needs at least two concrete observed facts")
        for fact in facts:
            shot = shots.get(fact.get("screenshot"))
            if not shot:
                errors.append("Visual fact cites an unreviewed screenshot")
                continue
            box = fact.get("bbox", [])
            if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in box) or box[2] <= 0 or box[3] <= 0 or box[0] + box[2] > 1.001 or box[1] + box[3] > 1.001:
                errors.append("Visual fact has no valid precise image location")
                continue
            if len(fact.get("appearance", "")) < 8 or len(fact.get("element", "")) < 2:
                errors.append("Visual observation is a generic verdict rather than a concrete image fact")
            generic = re.compile(r"^(?:整体|界面|画面|游戏|布局|视觉|效果|文本|全部|所有|要求|均|很|非常|良好|清晰|可读|美观|合理|符合|满足|通过|正常|无问题|未发现问题|[，。；、\s])+$")
            if generic.fullmatch(fact.get("appearance", "").strip()) or generic.fullmatch(fact.get("element", "").strip()):
                errors.append("Visual facts contain only generic pass wording")
            locations.add((fact["screenshot"], fact["element"], tuple(box)))
            views.add(shot.get("name"))
            scenes.add(shot.get("scene"))
        if len(locations) < 2:
            errors.append("Visual facts must identify two distinct visible locations/elements")
        case_id = test["id"].split("/")[0]
        portrait = {"GAME-TEST-001": "T37", "GAME-TEST-002": "T69", "GAME-TEST-003": "T63"}.get(case_id)
        landscape = {"GAME-TEST-001": "T38", "GAME-TEST-002": "T70", "GAME-TEST-003": "T64"}.get(case_id)
        if test["test_id"] == portrait and "portrait" not in views or test["test_id"] == landscape and "landscape" not in views:
            errors.append("Required orientation was not visually observed")
        if case_id == "GAME-TEST-002" and test["test_id"] in {"T63", "T64"}:
            needed = "upgrade_cards" if test["test_id"] == "T63" else "ultimate_evolution"
            if needed not in scenes:
                errors.append("The actual required upgrade/evolution scene was not observed")
        if case_id == "GAME-TEST-003" and test["test_id"] == "T25" and not {"power_change_before", "power_change_after"}.issubset(scenes):
            errors.append("Color changes require actual comparable before/after scenes")
        if case_id == "GAME-TEST-003" and test["test_id"] == "T70" and {shot.get("name") for shot in shots.values()} - views:
            errors.append("Power readability must be reviewed in every supplied viewport")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        errors.append("Invalid visual evidence: " + str(exc))
    return errors
