import json
import tempfile
import unittest
from pathlib import Path

from bloodforge.compiler import compile_scene
from bloodforge.config import Config
from bloodforge.corpus import ingest
from bloodforge.learning import (
    eligible_contents, evaluate_recipe, infer_recipe, mine_stairs, split_groups,
)
from bloodforge.native import encode_map
from bloodforge.scene import LevelScene, StairSpec
from bloodforge.store import database


def _bytes(index: int, *, origin_x: int | None = None, origin_y: int | None = None) -> bytes:
    directions = ("east", "south", "west", "north")
    stair = StairSpec(
        id=f"synthetic-{index}", count=3 + index % 4,
        width=768 + 32 * index, tread=384 + 16 * index,
        rise=128 + 8 * (index % 5),
        origin_x=1000 * index if origin_x is None else origin_x,
        origin_y=2000 * index if origin_y is None else origin_y,
        floor_z=32768 + 512 * index, ceiling_z=0,
        direction=directions[index % len(directions)],
        floor_tile=20 + index, ceiling_tile=60 + index, wall_tile=100 + index,
    )
    return encode_map(compile_scene(LevelScene((stair,))).native_map)


class LearningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.base = Path(self.temp.name)
        self.root = self.base / "corpus"
        self.data = self.base / "data"
        self.root.mkdir()
        self.config = Config(self.root, self.data)
        self.sources: dict[str, bytes] = {}
        for index in range(30):
            rel = f"campaign/E1M{index + 1:02d}.MAP"
            self.sources[rel] = _bytes(index)
        # Identical bytes and a translated revision must remain in the same
        # frozen split group when both are eligible.
        self.sources["campaign/E1M01-copy.MAP"] = self.sources["campaign/E1M01.MAP"]
        self.sources["campaign/E1M01-revision.MAP"] = _bytes(
            0, origin_x=45000, origin_y=-31000)
        # This active generated alias quarantines its content from design
        # evidence even though an identical campaign path also exists.
        self.sources["generated/alias-of-E1M02.MAP"] = self.sources["campaign/E1M02.MAP"]
        for relative, raw in self.sources.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def tearDown(self):
        self.temp.cleanup()

    def test_ingest_mine_split_infer_and_evaluate_without_contamination(self):
        ingested = ingest(self.config)
        self.assertEqual(ingested["failed"], 0)
        mining = mine_stairs(self.config)
        self.assertEqual(mining["status"], "candidates-only")
        self.assertEqual(mining["errors"], [])
        self.assertGreaterEqual(mining["candidates"], 25)

        with database(self.config.knowledge_db) as db:
            eligible = {row["sha256"] for row in eligible_contents(db, "blood-campaign")}
            generated_hash = db.execute(
                "SELECT sha256 FROM sources WHERE relative_path='generated/alias-of-E1M02.MAP'"
            ).fetchone()[0]
            self.assertNotIn(generated_hash, eligible)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM patterns WHERE sha256=?", (generated_hash,)
            ).fetchone())
            with self.assertRaisesRegex(ValueError, "excluded from construction evidence"):
                eligible_contents(db, "generated")

            split = split_groups(db, "blood-campaign")
            original_hash = db.execute(
                "SELECT sha256 FROM sources WHERE relative_path='campaign/E1M01.MAP'"
            ).fetchone()[0]
            copy_hash = db.execute(
                "SELECT sha256 FROM sources WHERE relative_path='campaign/E1M01-copy.MAP'"
            ).fetchone()[0]
            revision_hash = db.execute(
                "SELECT sha256 FROM sources WHERE relative_path='campaign/E1M01-revision.MAP'"
            ).fetchone()[0]
            self.assertEqual(original_hash, copy_hash)
            self.assertEqual(split["groups"][original_hash], split["groups"][revision_hash])
            self.assertEqual(split["assignments"][original_hash], split["assignments"][revision_hash])
            self.assertGreaterEqual(split["group_count"], 15)
            source_bytes_before = {rel: (self.root / rel).read_bytes() for rel in self.sources}

        learned = infer_recipe(self.config)
        self.assertIn(learned["status"], {"candidate", "reproduced"})
        self.assertGreaterEqual(learned["independent_groups"], 2)

        with database(self.config.knowledge_db) as db:
            row = db.execute("SELECT definition_json,status FROM recipes WHERE id=?",
                             (learned["recipe_id"],)).fetchone()
            definition = json.loads(row["definition_json"])
            frozen_assignments = definition["split"]["assignments"]
            heldout_snapshot = definition["heldout_snapshot"]
            # Inference evidence comes from training only, never held-out maps.
            evidence = json.loads(db.execute("SELECT training_json FROM recipes WHERE id=?",
                                             (learned["recipe_id"],)).fetchone()[0])
            self.assertFalse(set(evidence["map_hashes"]) & {
                sha for sha, side in frozen_assignments.items() if side == "heldout"
            })
            self.assertTrue(any(side == "heldout" for side in frozen_assignments.values()))
            self.assertEqual(len(heldout_snapshot), learned["heldout_maps"])

        # The direct mining entry point must reject a quarantined population too.
        with self.assertRaisesRegex(ValueError, "excluded from construction evidence"):
            mine_stairs(self.config, "generated")

        first_evaluation = evaluate_recipe(self.config, learned["recipe_id"])
        self.assertEqual(first_evaluation["heldout_candidates"], len(heldout_snapshot))
        self.assertGreater(first_evaluation["structurally_reproduced"], 0)
        self.assertFalse(first_evaluation["promoted_to_verified"])
        first_artifacts = [Path(item["artifact_path"]).read_bytes()
                           for item in first_evaluation["results"]
                           if item.get("artifact_path")]
        self.assertTrue(first_artifacts)
        first_report = Path(first_evaluation["report_path"])
        first_report_bytes = first_report.read_bytes()

        # Deactivate one frozen heldout source only after its first evaluation.
        # Evaluation must retain the original candidate denominator and record
        # the missing eligibility as evidence rather than silently dropping it.
        target = heldout_snapshot[0]
        target_hash = target["sha256"]
        expected_ineligible = sum(member["sha256"] == target_hash for member in heldout_snapshot)
        with database(self.config.knowledge_db) as db:
            with db:
                cursor = db.execute("UPDATE sources SET active=0 WHERE sha256=?", (target_hash,))
            self.assertGreater(cursor.rowcount, 0)

        evaluated = evaluate_recipe(self.config, learned["recipe_id"])
        self.assertEqual(evaluated["heldout_candidates"], len(heldout_snapshot))
        self.assertEqual(evaluated["counts"].get("ineligible_evidence"), expected_ineligible)
        self.assertFalse(evaluated["promoted_to_verified"])
        ineligible = [item for item in evaluated["results"]
                      if item["status"] == "ineligible_evidence"]
        self.assertEqual(len(ineligible), expected_ineligible)
        self.assertTrue(all(item["source"]["sha256"] == target_hash for item in ineligible))

        # Earlier content-addressed maps and benchmark report remain available;
        # the recipe's weaker reproduced status is also retained, never verified.
        for item in first_evaluation["results"]:
            if item.get("artifact_path"):
                self.assertEqual(Path(item["artifact_path"]).read_bytes(),
                                 first_artifacts.pop(0))
        self.assertEqual(first_report.read_bytes(), first_report_bytes)
        self.assertEqual({rel: (self.root / rel).read_bytes() for rel in self.sources},
                         source_bytes_before)

        with database(self.config.knowledge_db) as db:
            final_recipe = db.execute("SELECT status FROM recipes WHERE id=?",
                                      (learned["recipe_id"],)).fetchone()[0]
            self.assertNotEqual(final_recipe, "verified")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recipes WHERE status='verified'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM patterns WHERE status='verified'").fetchone()[0], 0)
            outcomes = [json.loads(row[0]) for row in db.execute(
                "SELECT result_json FROM benchmarks WHERE recipe_id=?", (learned["recipe_id"],))]
            self.assertTrue(outcomes)
            self.assertTrue(all(outcome["status"] != "verified" for outcome in outcomes))
            self.assertTrue(all(outcome["split_group"] for outcome in outcomes))


if __name__ == "__main__":
    unittest.main()
