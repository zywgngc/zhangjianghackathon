import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from game_harness.acceptance import evaluate_assertion, evaluate_evidence, scenario_plan
from game_harness.specifications import load_case


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_case("GAME-TEST-001")
        self.project_hash = "a" * 64
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def evidence(self, trace=None, test="T01", scenario="boot"):
        trace = trace or {"project_sha256": self.project_hash, "started": {"phase": "PLAYING"}}
        content = json.dumps(trace).encode()
        (self.root / "trace.json").write_bytes(content)
        assertion = {"artifact": "trace.json", "pointer": "/started/phase", "op": "eq", "expected": "PLAYING"}
        return {"spec_sha256": self.spec.sha256, "project_sha256": self.project_hash, "artifact_root": str(self.root),
                "checks": [{"requirement_id": self.spec.case_id + "/" + test, "scenario_id": self.spec.case_id + "/scenario/" + scenario,
                            "project_sha256": self.project_hash, "kind": "runtime", "producer": "node", "executed": True,
                            "artifacts": [{"path": "trace.json", "sha256": hashlib.sha256(content).hexdigest()}], "assertions": [assertion]}]}

    def test_missing_evidence_is_never_passed(self):
        report = evaluate_evidence(self.spec, {"checks": []})
        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["passed"], 0)
        self.assertEqual(sum(report["counts"].values()), 54)
        self.assertEqual(report["tests"][-1]["status"], "visual_required")

    def test_hashed_executed_trace_passes_only_the_test_it_proves(self):
        report = evaluate_evidence(self.spec, self.evidence())
        self.assertEqual(report["tests"][0]["status"], "passed")
        self.assertEqual(report["counts"]["passed"], 1)
        self.assertFalse(report["complete"])

    def test_failed_observation_is_failed_even_if_model_claims_pass(self):
        reports = self.evidence({"started": {"phase": "MENU"}})
        reports["checks"][0]["passed"] = True
        self.assertEqual(evaluate_evidence(self.spec, reports)["tests"][0]["status"], "failed")

    def test_stale_source_tampered_artifact_and_model_producer_are_rejected(self):
        for tampering in ["source", "artifact", "producer", "assertion", "unexecuted", "hash"]:
            with self.subTest(tampering=tampering):
                reports = self.evidence()
                if tampering == "source":
                    reports["spec_sha256"] = "b" * 64
                elif tampering == "artifact":
                    (self.root / "trace.json").write_text('{"started":{"phase":"PLAYING"}} ')
                elif tampering == "producer":
                    reports["checks"][0]["producer"] = "llm"
                elif tampering == "assertion":
                    reports["checks"][0]["assertions"] = [{"artifact": "trace.json", "pointer": "/started/phase", "op": "eq", "expected": "MENU"}]
                elif tampering == "unexecuted":
                    reports["checks"][0]["executed"] = False
                else:
                    reports["checks"][0]["project_sha256"] = "c" * 64
                self.assertEqual(evaluate_evidence(self.spec, reports)["tests"][0]["status"], "failed")

    def test_inline_boolean_without_artifact_cannot_pass(self):
        reports = self.evidence()
        reports["checks"][0]["artifacts"] = []
        reports["checks"][0]["assertions"] = []
        reports["checks"][0]["passed"] = True
        self.assertEqual(evaluate_evidence(self.spec, reports)["tests"][0]["status"], "failed")

    def test_foreign_or_escaping_artifact_is_rejected(self):
        reports = self.evidence({"project_sha256": "b" * 64, "started": {"phase": "PLAYING"}})
        self.assertEqual(evaluate_evidence(self.spec, reports)["tests"][0]["status"], "failed")
        reports = self.evidence()
        reports["checks"][0]["artifacts"][0]["path"] = "../trace.json"
        self.assertEqual(evaluate_evidence(self.spec, reports)["tests"][0]["status"], "failed")

    def test_one_skill_test_cannot_pass_the_all_skills_requirement(self):
        spec = load_case("GAME-TEST-002")
        report = evaluate_evidence(spec, {"spec_sha256": spec.sha256, "project_sha256": self.project_hash,
            "artifact_root": str(self.root), "checks": []})
        self.assertEqual(report["tests"][13]["status"], "not_run")
        plan = scenario_plan(spec.case_id)
        self.assertEqual(len(plan["acceptance_mapping"][spec.case_id + "/T14"]), 60)
        self.assertEqual(len(plan["acceptance_mapping"][spec.case_id + "/T16"]), 40)
        self.assertEqual(len(plan["acceptance_mapping"][spec.case_id + "/T18"]), 6)

    def test_fixed_configurations_preserve_all_levels_numbers_and_exact_skill_counts(self):
        gate = scenario_plan("GAME-TEST-001")["expected_configuration"]
        self.assertEqual([level["gates"][-1] for level in gate["levels"]], [["×2", "-5"], ["+20", "÷3"], ["×2", "-15"]])
        wuxia = scenario_plan("GAME-TEST-002")["expected_configuration"]
        self.assertEqual([len(wuxia[key]) for key in ["activeSkills", "passiveSkills", "ultimateSkills"]], [12, 8, 6])
        self.assertEqual([level["duration"] for level in wuxia["levels"]], [360, 420, 480])
        self.assertEqual([level["bossHp"] for level in wuxia["levels"]], [3000, 5000, 8000])
        power = scenario_plan("GAME-TEST-003")["expected_configuration"]
        self.assertEqual([len(level["enemyPowers"]) for level in power["levels"]], [8, 12, 16])
        self.assertEqual(power["levels"][2]["enemyPowers"][-3:], [700, 900, 1200])

    def test_boolean_is_not_a_number_and_nan_does_not_pass_comparisons(self):
        self.assertFalse(evaluate_assertion({"x": True}, {"pointer": "/x", "op": "eq", "expected": 1}))
        self.assertFalse(evaluate_assertion({"x": float("nan")}, {"pointer": "/x", "op": "gte", "expected": 0}))
        self.assertTrue(evaluate_assertion({"x": 1.5}, {"pointer": "/x", "op": "approx", "expected": 1.5000001}))
        with self.assertRaises(ValueError):
            evaluate_assertion({"x": 1}, {"pointer": "/x", "op": "python", "expected": "arbitrary code"})

    def test_navigation_evidence_rejects_teleport_and_collision(self):
        points = [(0, 0), (1, 0), (1, 3), (5, 3), (5, 0)]
        trace = {"path": [{"simTime": index, "player": {"x": x, "z": z}} for index, (x, z) in enumerate(points)]}
        assertion = {"pointer": "/path", "op": "movement_path", "expected": {"speed": 5, "radius": 0.4,
            "obstacles": [{"x": 3, "z": 0, "width": 2, "depth": 4}]}}
        self.assertTrue(evaluate_assertion(trace, assertion))
        trace["path"][1]["player"] = {"x": 3, "z": 0}
        self.assertFalse(evaluate_assertion(trace, assertion))
        trace["path"][1]["player"] = {"x": 50, "z": 0}
        self.assertFalse(evaluate_assertion(trace, assertion))

    def test_boss_warnings_require_real_pattern_timing(self):
        events = [{"type": "boss_attack_warning", "simTime": 2.2, "data": {"pattern": "charge", "duration": 0.8}},
                  {"type": "boss_attack", "simTime": 3.0, "data": {"pattern": "charge"}}]
        assertion = {"pointer": "/events", "op": "telegraphed_attacks", "expected": {"patterns": ["charge"], "minimumWarning": 0.8}}
        self.assertTrue(evaluate_assertion({"events": events}, assertion))
        events[1]["simTime"] = 2.5
        self.assertFalse(evaluate_assertion({"events": events}, assertion))

    def test_exact_damage_requires_real_hits_and_hp_accounting(self):
        trace = {"before": {"entities": [{"id": "target", "hp": 100}]},
                 "after": {"entities": [{"id": "target", "hp": 52.96}]},
                 "events": [{"type": "damage", "data": {"targetId": "target", "amount": 23.52}}] * 2}
        assertion = {"pointer": "", "op": "damage_formula", "expected": {"target": "target", "perHit": 23.52}}
        self.assertTrue(evaluate_assertion(trace, assertion))
        trace["after"]["entities"][0]["hp"] = 1
        self.assertFalse(evaluate_assertion(trace, assertion))
        trace["events"] = []
        with self.assertRaises(KeyError):
            evaluate_assertion(trace, assertion)

    def test_spawn_count_and_exact_two_shop_definitions(self):
        assertion = {"pointer": "", "op": "normal_spawn_count", "expected": {"count": 20, "tolerance": 1}}
        enemies = [{"kind": "enemy", "active": True}] * 20 + [{"kind": "elite"}]
        self.assertTrue(evaluate_assertion(enemies, assertion))
        self.assertFalse(evaluate_assertion(enemies[:18], assertion))
        configuration = {"permanentUpgrades": [{"id": "initialSoldiers"}, {"id": "weaponDamage"}], "enemies": [{"hp": 10}, {"hp": 10}]}
        check = {"pointer": "", "op": "case1_configuration", "expected": True}
        self.assertTrue(evaluate_assertion(configuration, check))
        configuration["enemies"][1]["hp"] = 15
        self.assertFalse(evaluate_assertion(configuration, check))
        configuration["permanentUpgrades"].append({"id": "fireRate"})
        self.assertFalse(evaluate_assertion(configuration, check))

    def test_drop_probability_requires_actual_multiseed_kills_and_reproducible_loot(self):
        samples = []
        for seed in range(50):
            events = [{"seq": i + 1, "type": "drop_created", "data": {"id": str(i), "kind": kind, "value": 1}}
                      for i, kind in enumerate(["gold"] * 10 + ["healing"] * 2 + (["magnet"] if seed < 10 else []))]
            samples.append({"seed": seed, "before": {"metrics": {"enemiesDefeated": 0}},
                            "after": {"metrics": {"enemiesDefeated": 200}, "entities": []}, "events": events})
        samples.append(json.loads(json.dumps(samples[0])))
        assertion = {"pointer": "", "op": "drop_statistics", "expected": {"samples": 50, "killsPerSample": 200,
                     "gold": .05, "healing": .01, "magnet": .001, "standardDeviations": 4.5}}
        self.assertTrue(evaluate_assertion(samples, assertion))
        samples[-1]["events"].pop()
        self.assertFalse(evaluate_assertion(samples, assertion))
        samples[-1] = json.loads(json.dumps(samples[0]))
        samples[1]["after"]["metrics"]["enemiesDefeated"] = 199
        self.assertFalse(evaluate_assertion(samples, assertion))

    def test_evolution_strength_requires_damage_events_to_match_real_hp_loss(self):
        trace = {}
        for name, damage, skill in [("base", 16.8, "W01"), ("ultimate", 25, "E01")]:
            trace[name + "_before"] = {"entities": [{"id": "target", "hp": 1000}]}
            trace[name] = {"entities": [{"id": "target", "hp": 1000 - damage}]}
            trace[name + "_events"] = [{"type": "damage", "data": {"skillId": skill, "targetId": "target", "amount": damage}}]
        assertion = {"pointer": "", "op": "evolution_strength", "expected": {"id": "E01", "baseIds": ["W01"], "minimumHit": 25, "damageOverTime": False}}
        self.assertTrue(evaluate_assertion(trace, assertion))
        trace["ultimate"]["entities"][0]["hp"] = 999
        self.assertFalse(evaluate_assertion(trace, assertion))

    def test_long_session_checks_browser_heap_draws_and_real_resource_growth(self):
        samples = [{"snapshot": {"simTime": elapsed, "phase": "PLAYING", "player": {"hp": 300}, "metrics": {"totalDamage": elapsed * 100}},
                    "engineInfo": {"running": True, "resources": {"textures": 4, "objects": 220, "scenes": 1}},
                    "heap": {"usedSize": 10_000_000}, "dom": {"nodes": 300, "jsEventListeners": 20}}
                   for elapsed in range(0, 721, 30)]
        trace = {"samples": samples, "elapsedWallSeconds": 65, "drawCalls": 1000, "additionalContexts": 1,
                 "noninterference": {"liveStateUnchanged": True, "storageUnchanged": True}}
        assertion = {"pointer": "", "op": "bounded_renderer_session", "expected": {"durationSeconds": 720, "minimumWallSeconds": 60, "minimumSamples": 20}}
        self.assertTrue(evaluate_assertion(trace, assertion))
        trace["samples"][-1]["heap"]["usedSize"] = 30_000_000
        self.assertFalse(evaluate_assertion(trace, assertion))
        trace["samples"][-1]["heap"]["usedSize"] = 10_000_000
        trace["samples"][-1]["engineInfo"]["resources"]["objects"] = 500
        self.assertFalse(evaluate_assertion(trace, assertion))


if __name__ == "__main__":
    unittest.main()
