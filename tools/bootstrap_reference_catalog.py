"""Fetch small public, immutable source references; never install or execute them.

Only raw.githubusercontent.com is used, without authentication or local credentials.
The catalog contains static source evidence, not validated runtime behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request


REPOSITORIES = {
    "nusr/wukong-survivors": "bb28e7091ca6072731d74a71a36c5d38877c761c",
    "phaserjs/examples": "68140eca72525d5034b783a262270715ddc17b78",
    "BabylonJS/Babylon.js": "1431d93dbd9dd6051575fafcc43db9806d0483c4",
    "qiao/PathFinding.js": "2904a9afba483c02b5a61eb60fefd63e9b2db78c",
}

SOURCE_PATHS = {
    "nusr/wukong-survivors": [
        "README.md", "LICENSE", "package.json", "src/game/GameScene.ts",
        "src/game/weapon.ts", "src/game/enemy.ts", "src/game/experience.ts",
        "src/game/player.ts", "src/game/RewardSelectionUI.ts", "src/store/app.ts",
        "src/store/save.ts", "src/constant/rewards.ts",
    ],
    "phaserjs/examples": [
        "README.md", "package.json", "public/src/paths/followers/basic follower.js",
        "public/src/physics/arcade/closest furthest.js",
        "public/src/physics/arcade/topdown shooter combat mechanics.js",
        "public/src/physics/arcade/bullets group.js",
    ],
    "BabylonJS/Babylon.js": [
        "license.md", "packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts",
    ],
    "qiao/PathFinding.js": [
        "README.md", "package.json", "src/finders/AStarFinder.js", "src/core/Util.js",
    ],
}

EXPECTED_SHA256 = {
    "nusr/wukong-survivors/README.md": "01f38200c381ffdead8b1b95c7f12d6b91e392c5012acbe12d0a582b5c7fa620",
    "nusr/wukong-survivors/LICENSE": "4d0743123aa16a8fc15f611ef54d435c9de647a149d5f42c25bcfbe02a19c487",
    "nusr/wukong-survivors/package.json": "82f29c097ebb549383bd56ab95f0236a227f28f5b33e75e27a9e45214ac7fb02",
    "nusr/wukong-survivors/src/game/GameScene.ts": "05f08fe2ca09d2f7f992c9951a56fa23629f9553e061ae61070fd782dd0376ce",
    "nusr/wukong-survivors/src/game/weapon.ts": "7d140ac8cae4313c1a88325b67ee5ce5deeb3c52c0ace32ddbec9239a21e7a92",
    "nusr/wukong-survivors/src/game/enemy.ts": "d6344c6c8823c2b228c4d0e61c4f21eae994058eef659e0a2d2553bb85157646",
    "nusr/wukong-survivors/src/game/experience.ts": "2bec5ecd617ed4d1f51206b5fb59884a8ddf6a3aa962184741cf63b2fb0d38cf",
    "nusr/wukong-survivors/src/game/player.ts": "b8b263cd40684d1fe85ca23d5f621e7c639ad21f82eee57d0dc39dd8f52cdb1e",
    "nusr/wukong-survivors/src/game/RewardSelectionUI.ts": "729dc685fe8e56aadb627f851ed7734bd1590ba602ca3ec7c5178ac50a6f12c2",
    "nusr/wukong-survivors/src/store/app.ts": "9a881eb46ae1da3cdd47e22fc61558fde1545a3a8d8618916e35a1851b120079",
    "nusr/wukong-survivors/src/store/save.ts": "ee44539deb61978a0625d310654c85322dcad2b84034feeefeaa32170c67eb0e",
    "nusr/wukong-survivors/src/constant/rewards.ts": "2ce7e99e5ebf04f97bf973a34cbc5ae4ec55f41c1c2b2daaa6c700aa53362ee0",
    "phaserjs/examples/README.md": "e474b6c49e2ebf9aaf8c8832e38885407cd36cc57763e86d523f06d3d2cbc35c",
    "phaserjs/examples/package.json": "2ab699b1f5f849d6ad21273c6145992afc9bf566c09b1a9b619756e3c0b61776",
    "phaserjs/examples/public/src/paths/followers/basic follower.js": "3e37af700a46058b67c398cf50ab609d6462a7d696f2a38a37f0bf5df22f6796",
    "phaserjs/examples/public/src/physics/arcade/closest furthest.js": "9dc727bd8b85ff4f54954771b23001fb533b106a6753d6c39de2c082987ee18d",
    "phaserjs/examples/public/src/physics/arcade/topdown shooter combat mechanics.js": "55d9e732ee372528fd63929bc016d6398ac837b4ea1b8931e73f2cba265834b5",
    "phaserjs/examples/public/src/physics/arcade/bullets group.js": "358b3cb3f21f5f0e9ac47c04b854f09e68d1f5dcc24afe6bc90367553852fcd3",
    "BabylonJS/Babylon.js/license.md": "9362ea9ea17cb221a20cbbd63f404710834cc273d2a9a7b60314fd68ab702cd6",
    "BabylonJS/Babylon.js/packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts": "d5b8479a020b4f0746a1bd71a6c5159d6b15b2437bda02d5264c578dd3a4d70a",
    "qiao/PathFinding.js/README.md": "6517cd3e48cabb16fc55d72613fc0f9ba1988a12bf85911f10fa65d1a18f2f4d",
    "qiao/PathFinding.js/package.json": "17609851c7ca364168f98b1fea91d09dc6f65f50dcbcaafcea869edc7548010e",
    "qiao/PathFinding.js/src/finders/AStarFinder.js": "d1e8e12a2c84f8af2175136b08bc435a9ec9854e3c17d84cc17d37533d5633d0",
    "qiao/PathFinding.js/src/core/Util.js": "4ea100d831cd50f7f84dc080f0edcd021ad78f96ae8a364b5b2b175d7ea28300",
}
MAX_SOURCE_BYTES = 512 * 1024


def source_url(repository: str, path: str) -> str:
    return "https://raw.githubusercontent.com/{}/{}/{}".format(
        repository, REPOSITORIES[repository], urllib.parse.quote(path, safe="/")
    )


def cached_path(root: Path, repository: str, path: str) -> Path:
    return root / "sources" / repository / REPOSITORIES[repository] / path


def download_sources(root: Path, *, offline: bool = False) -> dict:
    records = []
    for repository, paths in SOURCE_PATHS.items():
        for path in paths:
            target = cached_path(root, repository, path)
            url = source_url(repository, path)
            if offline:
                data = target.read_bytes()
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "game-material-references/0.2"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.geturl() != url:
                        raise ValueError("Unexpected source redirect: " + response.geturl())
                    data = response.read(MAX_SOURCE_BYTES + 1)
                if len(data) > MAX_SOURCE_BYTES:
                    raise ValueError("Source exceeds size limit: " + path)
            digest = hashlib.sha256(data).hexdigest()
            expected = EXPECTED_SHA256[repository + "/" + path]
            if digest != expected:
                raise ValueError("Pinned source hash mismatch: " + repository + "/" + path)
            data.decode("utf-8")
            if not offline:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            records.append({
                "repository": repository, "revision": REPOSITORIES[repository], "path": path,
                "sourceUri": url, "cachePath": target.relative_to(root).as_posix(),
                "sha256": digest, "bytes": len(data),
            })
    return {"schemaVersion": "0.2", "isMock": False, "files": records}


def permalink(repository: str, path: str) -> str:
    return "https://github.com/{}/blob/{}/{}".format(
        repository, REPOSITORIES[repository], urllib.parse.quote(path, safe="/")
    )


def build_materials(root: Path) -> dict:
    """Build provider claims from inspected sources, with immutable evidence links.

    Marker checks protect against stale metadata. They are not behavioral tests,
    an AST-level symbol resolver, or a compatibility assessment.
    """
    materials = []

    def add(candidate_id, repository, kind, title, summary, paths, symbols,
            coverage, tags, *, markers, engine=None, language=None,
            inputs=(), purposes=("mechanic",), limitations=()):
        texts = {p: cached_path(root, repository, p).read_text(encoding="utf-8") for p in paths}
        corpus = "\n".join(texts.values())
        for symbol in symbols:
            for identifier in symbol.split("."):
                if not re.search(r"\b" + re.escape(identifier) + r"\b", corpus):
                    raise ValueError("Symbol identifier absent from inspected source: " + symbol)
        evidence = []
        for path, marker, description in markers:
            text = cached_path(root, repository, path).read_text(encoding="utf-8")
            offset = text.find(marker)
            if offset < 0:
                raise ValueError("Source evidence marker absent: " + path + ": " + marker)
            line = text.count("\n", 0, offset) + 1
            evidence.append({"description": description, "sourceUri": permalink(repository, path) + "#L" + str(line)})

        license_path, license_id = {
            "nusr/wukong-survivors": ("LICENSE", "MIT"),
            "phaserjs/examples": ("README.md", "MIT"),
            "BabylonJS/Babylon.js": ("license.md", "Apache-2.0"),
            "qiao/PathFinding.js": ("README.md", "MIT"),
        }[repository]
        evidence.append({
            "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
            "sourceUri": permalink(repository, license_path),
        })
        common_limitations = [
            "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
            "Only the listed small source/document excerpts are cached; this is not a runnable checkout or a packaged game/asset delivery.",
            "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        ]
        if repository == "phaserjs/examples":
            common_limitations += [
                "README explicitly states source code is MIT and assets are not; no referenced example asset files were downloaded.",
                "README identifies the current site as Phaser 4 examples/development builds, while package.json retains a Phaser 3 name and ISC metadata; exact engine-release compatibility and license metadata discrepancy require review.",
            ]
        elif repository == "nusr/wukong-survivors":
            common_limitations.append(
                "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
            )
        candidate = {
            "candidateId": candidate_id, "kind": kind, "title": title, "summary": summary,
            "source": {
                "uri": "https://github.com/" + repository + "/tree/" + REPOSITORIES[repository]
                if kind == "game_repository" else permalink(repository, paths[0]),
                "repositoryUrl": "https://github.com/" + repository,
                "revision": REPOSITORIES[repository], "paths": paths, "symbols": symbols,
            },
            "coverageHints": coverage,
            "license": {"identifier": license_id, "sourceUri": permalink(repository, license_path), "scope": "source_code"},
            "evidence": evidence, "limitations": common_limitations + list(limitations),
        }
        materials.append({
            "candidate": candidate,
            "searchText": " ".join([title, summary] + coverage + tags),
            "tags": tags, "engines": [engine] if engine else [],
            "languages": [language] if language else [], "inputModes": list(inputs),
            "purposes": list(purposes),
        })

    wukong = "nusr/wukong-survivors"
    game = "src/game/GameScene.ts"
    weapons = "src/game/weapon.ts"
    enemies = "src/game/enemy.ts"
    experience = "src/game/experience.ts"
    player = "src/game/player.ts"
    save = "src/store/save.ts"
    app = "src/store/app.ts"

    add("ref-wukong-survivors", wukong, "game_repository",
        "Wukong Survivors — Phaser/React survivor game repository",
        "真实悟空题材幸存者游戏仓库：俯视角角色移动、自动武器、敌人波次、掉落拾取、局内升级和永久成长。Repository and source evidence are pinned, without claiming a tested playable build.",
        ["README.md", "package.json", game, weapons, enemies, experience, save],
        ["GameScene", "WeaponManager", "EnemySpawner", "ExperienceManager", "useSaveStore"],
        ["survivors-like archetype", "top-down automatic combat", "enemy waves", "experience pickups", "permanent upgrades"],
        ["survivors", "vampire survivors", "roguelike", "archetype", "幸存者", "割草", "吸血鬼幸存者", "肉鸽", "局内成长"],
        markers=[("README.md", "Wukong Survivors is a roguelike survivor game", "Author describes a survivor game built with Phaser, React and Vite."),
                 (game, "export class GameScene", "GameScene composes player, enemies, weapons, collectibles and UI."),
                 ("package.json", '"phaser": "^4.2.1"', "Pinned manifest declares Phaser ^4.2.1, rather than assuming Phaser 3 compatibility.")],
        engine="phaser", language="typescript", inputs=("keyboard", "touch"), purposes=("archetype",))

    add("ref-wukong-combat-targeting", wukong, "source_code",
        "Wukong — cooldown attacks and nearest-enemy targeting",
        "WeaponManager filters dead enemies and sorts by player distance before weapon updates; base Weapon fires on cooldown, creates projectiles and aims at sorted targets. 供自动攻击、最近敌人选取和弹道机制审查。",
        [weapons, game], ["WeaponManager.update", "Weapon.update", "Weapon.fire", "Weapon.generateProjectileAngle"],
        ["automatic cooldown attack", "nearest target ordering", "projectile direction", "damage collision integration"],
        ["combat", "target", "targeting", "auto attack", "cooldown", "projectile", "自动攻击", "最近敌人", "选取目标", "战斗", "弹道", "攻击间隔"],
        markers=[(weapons, "const sortedEnemies = list.sort", "WeaponManager.update sorts active targets by Euclidean distance."),
                 (weapons, "if (time - this.lastFired >= this.coolDown)", "Weapon.update gates fire calls by cooldown."),
                 (game, "private checkCollisions", "GameScene contains collision and damage integration for weapons and enemies.")],
        engine="phaser", language="typescript")

    add("ref-wukong-wave-spawning", wukong, "source_code",
        "Wukong — timed enemy waves with increasing difficulty",
        "EnemySpawner accumulates delta time, spawns bounded waves, chooses enemy types and increases wave size/frequency and population cap over time. 适合波次生成、难度曲线与屏外刷怪参考。",
        [enemies], ["EnemySpawner.update", "EnemySpawner.spawnWave", "EnemySpawner.increaseDifficulty", "EnemySpawner.selectEnemyType"],
        ["timed wave spawning", "time-scaled difficulty", "active enemy population cap", "offscreen spawn position"],
        ["wave", "spawn", "survival", "enemy", "difficulty", "director", "波次", "刷怪", "怪物生成", "敌人数量", "难度递增", "时间驱动"],
        markers=[(enemies, "private spawnWave", "spawnWave computes a time-scaled count bounded by the population cap."),
                 (enemies, "private increaseDifficulty", "increaseDifficulty changes count, spawn interval and population bounds."),
                 (enemies, "private spawnEnemy", "spawnEnemy calculates positions around the player.")],
        engine="phaser", language="typescript")

    add("ref-wukong-collectibles-xp", wukong, "source_code",
        "Wukong — magnetized experience gems and gold pickups",
        "CollectibleItem checks player distance, attracts items in a magnet radius and collects nearby drops; ExperienceManager routes gems to player experience and coins to saved gold. 供击杀掉落、拾取半径与经验经济循环参考。",
        [experience, player, game], ["CollectibleItem.update", "CollectibleItem.collect", "ExperienceManager.update", "ExperienceManager.spawnGem", "ExperienceManager.spawnCoin"],
        ["magnetized pickup attraction", "experience gem collection", "gold coin collection", "drop spawning hooks"],
        ["pickup", "collect", "magnet", "experience", "xp", "economy", "掉落", "经验球", "金币", "磁吸", "拾取", "收集半径"],
        markers=[(experience, "if (distance < adjustedMagnetRadius)", "CollectibleItem.update activates attraction inside its adjusted magnet radius."),
                 (experience, "this.player.addExperience(collectible.value)", "ExperienceManager applies collected gem value to player experience."),
                 (experience, "useSaveStore.getState().addGold(this.value)", "Collected coins increment persistent gold.")],
        engine="phaser", language="typescript")

    add("ref-wukong-run-progression", wukong, "source_code",
        "Wukong — experience thresholds and in-run upgrade choice",
        "Player.levelUp carries excess XP, scales the next threshold and opens a choice menu; WeaponManager exposes upgrade options and GameScene applies the selected upgrade. 局内等级、武器成长和升级选择界面的源码参考。",
        [player, weapons, game], ["Player.addExperience", "Player.levelUp", "WeaponManager.getUpgradeOptions", "GameScene.showLevelUpMenu", "GameScene.selectUpgrade"],
        ["experience level threshold", "carry-over experience", "weapon upgrade options", "pause and select upgrade"],
        ["progression", "level up", "upgrade", "growth", "reward choice", "升级", "经验阈值", "成长", "局内升级", "升级选项", "武器升级"],
        markers=[(player, "private levelUp", "Player.levelUp updates level, XP carry-over and next threshold."),
                 (weapons, "public getUpgradeOptions", "WeaponManager offers available weapon and upgrade options."),
                 (game, "public selectUpgrade", "GameScene applies a chosen weapon upgrade or acquisition.")],
        engine="phaser", language="typescript",
        limitations=("The inspected addExperience implementation checks one level threshold per call; repeated multi-level gains were not behavior-tested.",))

    add("ref-wukong-persistent-economy", wukong, "source_code",
        "Wukong — persistent gold economy and permanent upgrades",
        "Zustand save state supplies addGold/spendGold, checks upgrade cost and level caps, records permanent upgrades and persists JSON to localStorage. 可用于金币消费、永久属性成长、退款与存档方案审查。",
        [save, "src/constant/rewards.ts", game], ["useSaveStore", "spendGold", "upgradePermanent", "resetPermanentUpgrades"],
        ["gold balance", "cost-gated permanent upgrade", "upgrade level cap", "localStorage persistence", "upgrade refund"],
        ["economy", "currency", "shop", "purchase", "permanent progression", "金币", "经济", "消费", "商店", "永久升级", "存档", "退款"],
        markers=[(save, "spendGold: (amount: number) =>", "spendGold debits only when the stored balance covers the amount."),
                 (save, "upgradePermanent: (upgradeId: PermanentUpgradeType) =>", "upgradePermanent checks cost and maximum level before spending gold."),
                 (save, "storage: createJSONStorage(() => localStorage)", "Save store explicitly persists JSON in browser localStorage.")],
        engine="phaser", language="typescript",
        limitations=("This is client-side game state, not a server-authoritative or monetary payment system.",))

    add("ref-wukong-unlock-progression", wukong, "source_code",
        "Wukong — character and chapter unlock conditions",
        "checkUnlocks computes available characters and chapters from kills, survival time, gold and completed chapters; selection actions check unlocked IDs. 供长期成就、章节解锁与角色成长结构参考。",
        [app, save], ["checkUnlocks", "useAppStore", "selectCharacter", "selectMap"],
        ["character unlock gates", "chapter unlock gates", "kill/time/gold achievements", "unlocked selection enforcement"],
        ["progression", "unlock", "achievement", "meta progression", "解锁", "成就", "角色解锁", "章节解锁", "长期成长"],
        markers=[(app, "const checkUnlocks =", "checkUnlocks derives available maps and characters from saved milestones."),
                 (app, 'case "kills":', "Character unlock switch checks kills, time, gold and chapter conditions."),
                 (app, "if (unlockedCharacterIds.includes(characterId))", "Character selection checks whether the requested ID is unlocked.")],
        engine="phaser", language="typescript")

    phaser = "phaserjs/examples"
    follower = "public/src/paths/followers/basic follower.js"
    closest = "public/src/physics/arcade/closest furthest.js"
    combat = "public/src/physics/arcade/topdown shooter combat mechanics.js"
    bullets = "public/src/physics/arcade/bullets group.js"

    add("ref-phaser-path-follower", phaser, "source_code",
        "Official Phaser example — spline route following",
        "Builds Phaser.Curves.Spline from route coordinates, creates follower sprites and calls startFollow with a duration. 预定义路径/路点跟随的最小参考，不包含障碍物寻路。",
        [follower], ["Example.create", "Phaser.Curves.Spline", "startFollow"],
        ["predefined waypoint route", "spline path following", "duration-based follower movement"],
        ["waypoint", "navigation", "path", "path following", "route", "spline", "路径", "路点", "导航", "沿路径移动", "巡逻路线"],
        markers=[(follower, "new Phaser.Curves.Spline(points)", "Example creates a spline from a flat coordinate list."),
                 (follower, "ball1.startFollow(4000)", "Follower starts a four-second traversal of that route.")],
        engine="phaser", language="javascript",
        limitations=("This example follows a predefined spline and does not compute obstacle-avoiding routes or dynamically replan a path.",))

    add("ref-phaser-closest-target", phaser, "source_code",
        "Official Phaser example — nearest and furthest physics-body query",
        "Calls Arcade physics.closest and furthest around a pointer-controlled cursor and draws connections to the selected bodies. 供最近目标查询和空间选择参考，非完整敌我过滤策略。",
        [closest], ["Example.update", "physics.closest", "physics.furthest"],
        ["nearest body query", "furthest body query", "pointer-driven target point"],
        ["target", "targeting", "nearest", "closest", "distance", "选敌", "目标选择", "最近目标", "距离查询", "自动瞄准"],
        markers=[(closest, "this.physics.closest(this.cursor)", "update queries the closest physics body to the cursor."),
                 (closest, "this.physics.furthest(this.cursor)", "update also queries the furthest physics body.")],
        engine="phaser", language="javascript", inputs=("pointer",),
        limitations=("The example demonstrates a body query, not team filtering, attack range checks, line of sight, or lock-on persistence.",))

    add("ref-phaser-topdown-combat", phaser, "source_code",
        "Official Phaser example — top-down shooter combat mechanics",
        "Demonstrates keyboard movement, pointer aim, pooled player/enemy bullets, collision callbacks, health damage and enemy firing cooldown. 俯视角射击的输入、伤害与敌人攻击实现参考。",
        [combat], ["Example.enemyHitCallback", "Example.playerHitCallback", "Example.enemyFire", "Bullet.fire"],
        ["top-down shooter", "bullet collision damage", "health reduction", "enemy firing cooldown", "keyboard and mouse aim"],
        ["combat", "damage", "bullet", "shooter", "health", "cooldown", "战斗", "伤害", "碰撞", "生命值", "子弹", "射击"],
        markers=[(combat, "enemyHitCallback (enemyHit, bulletHit)", "Enemy collision callback checks active objects, subtracts health and disables the bullet."),
                 (combat, "enemyFire (time)", "enemyFire emits a bullet when its cooldown elapses."),
                 (combat, "requestPointerLock()", "The demo explicitly requests mouse pointer lock.")],
        engine="phaser", language="javascript", inputs=("keyboard", "pointer"),
        limitations=("The mouse pointer-lock/WASD example does not establish touch controls or mobile gameplay compatibility.",))

    add("ref-phaser-bullet-pool", phaser, "source_code",
        "Official Phaser example — reusable projectile group",
        "Preallocates five Bullet sprites, obtains inactive projectiles with getFirstDead, reactivates on fire and disables them beyond the screen. 适用于弹幕、自动武器与低分配开销的子弹池实现参考。",
        [bullets], ["Bullet.fire", "Bullet.preUpdate", "Bullets.fireBullet"],
        ["projectile pooling", "inactive object reuse", "out-of-bounds deactivation", "pointer fire input"],
        ["combat", "projectile", "bullet", "pool", "object reuse", "子弹池", "对象池", "复用", "弹幕", "射击", "性能"],
        markers=[(bullets, "frameQuantity: 5", "Bullets preallocates a bounded pool of five projectiles."),
                 (bullets, "this.getFirstDead(false)", "fireBullet obtains an inactive projectile instead of growing the pool."),
                 (bullets, "if (this.y <= -32)", "Bullet.preUpdate disables sprites after they pass the upper boundary.")],
        engine="phaser", language="javascript", inputs=("pointer",))

    babylon = "BabylonJS/Babylon.js"
    recast = "packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts"
    add("ref-babylon-recast-navigation", babylon, "source_code",
        "Official Babylon.js — Recast navigation paths and crowd destinations",
        "RecastJSPlugin builds a navmesh, projects points and computes paths; RecastJSCrowd adds agents and assigns destinations with agentGoto. 3D导航网格、路径点和群集代理移动的源码参考。",
        [recast], ["RecastJSPlugin.createNavMesh", "RecastJSPlugin.getClosestPoint", "RecastJSPlugin.computePath", "RecastJSCrowd.addAgent", "RecastJSCrowd.agentGoto"],
        ["3D navigation mesh", "path waypoint query", "destination-driven agent movement", "crowd navigation"],
        ["waypoint", "navigation", "navmesh", "pathfinding", "recast", "crowd", "3d", "路径", "路点", "寻路", "导航网格", "群集", "代理移动"],
        markers=[(recast, "createNavMesh(meshes:", "createNavMesh exposes navmesh construction from meshes and parameters."),
                 (recast, "computePath(start: Vector3, end: Vector3)", "computePath converts a Recast route to Babylon Vector3 points."),
                 (recast, "agentGoto(index: number, destination: Vector3)", "RecastJSCrowd forwards an agent destination into the crowd engine.")],
        engine="babylon", language="typescript",
        limitations=("Requires a separately supplied/initialized Recast runtime and scene/navmesh setup; those dependencies were not fetched or run, and their licenses are outside this cached source-code evidence.",))

    pathfinding = "qiao/PathFinding.js"
    astar = "src/finders/AStarFinder.js"
    util = "src/core/Util.js"
    add("ref-pathfinding-grid-astar", pathfinding, "algorithm_documentation",
        "PathFinding.js — A* grid routes and path smoothing documentation",
        "README documents grid-based A* with heuristic/diagonal options and smoothing; inspected findPath uses a heap/open set, grid neighbors and backtrace. 引擎无关的网格障碍寻路与路径点压缩/平滑参考。",
        ["README.md", astar, util], ["AStarFinder.findPath", "smoothenPath", "compressPath"],
        ["grid obstacle pathfinding", "A* heuristic search", "diagonal movement options", "path smoothing and compression"],
        ["waypoint", "navigation", "pathfinding", "astar", "a*", "grid", "route", "寻路", "路径", "路点", "导航", "网格", "障碍物", "路径平滑"],
        markers=[("README.md", "var finder = new PF.AStarFinder", "README explains construction and use of the A* finder."),
                 (astar, "AStarFinder.prototype.findPath =", "findPath implements heap-based grid search and backtraces a reached target."),
                 (util, "function smoothenPath(grid, path)", "smoothenPath checks grid walkability along proposed shortcuts."),
                 (util, "function compressPath(path)", "compressPath removes redundant intermediate route points.")],
        language="javascript",
        limitations=("Returns grid-coordinate routes, not game-engine movement or character steering. CommonJS imports and the heap dependency need integration; dynamic obstacles and current build compatibility were not tested.",))

    ids = [item["candidate"]["candidateId"] for item in materials]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate reference candidate IDs")
    return {"schemaVersion": "0.2", "isMock": False, "materials": materials}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Verify cached bytes without network access")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "catalogs" / "references"
    manifest = download_sources(root, offline=args.offline)
    catalog = build_materials(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "materials.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Verified {} pinned source files; generated {} real candidates".format(len(manifest["files"]), len(catalog["materials"])))


if __name__ == "__main__":
    main()
