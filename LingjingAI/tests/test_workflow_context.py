import tempfile
import unittest
from pathlib import Path
from workflow import WorkflowStore

CATALOG = {"image": [], "video": [{"id": "film-model", "max_refs": 2, "params": {"duration": {"options": ["6"]}}}]}


class ConnectedSpecificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.store = WorkflowStore(self.root)
        spec = self.root / "blue-cloth.md"
        spec.write_text("Use exactly one faded blue cloth. Never duplicate it on head and hand.", encoding="utf-8")
        self.asset = self.store.import_asset({"id": "cloth-spec", "path": str(spec), "role": "prop_spec", "reviewStatus": "ready"})
        self.graph = {"version": 1, "revision": 0, "nodes": [
            {"id": "spec", "type": "asset", "assetId": "cloth-spec", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "prompt", "type": "prompt", "position": {"x": 300, "y": 0}, "data": {"text": "A hand pauses on a brick floor."}},
            {"id": "video", "type": "video", "shotId": "M002", "position": {"x": 600, "y": 0}, "data": {"modelId": "film-model", "params": {"duration": "6"}, "trimSeconds": 4, "trimStartSeconds": 1}},
        ], "edges": [
            {"id": "e1", "source": "spec", "sourcePort": "text", "target": "prompt", "targetPort": "context"},
            {"id": "e2", "source": "prompt", "sourcePort": "text", "target": "video", "targetPort": "prompt"},
        ]}
        self.store.save(self.graph)

    def tearDown(self):
        self.tmp.cleanup()

    def test_connected_text_is_in_actual_generation_prompt(self):
        task = self.store.compile(catalogs=CATALOG)["tasks"][0]
        self.assertTrue(task["ready"])
        self.assertIn("exactly one faded blue cloth", task["prompt"])
        self.assertEqual(task["trimStartSeconds"], 1)

    def test_revoked_text_spec_blocks_downstream_generation(self):
        self.store.patch_asset("cloth-spec", {"reviewStatus": "blocked"})
        task = self.store.compile(catalogs=CATALOG)["tasks"][0]
        self.assertFalse(task["ready"])

    def test_changed_head_handle_invalidates_previous_result_signature(self):
        first = self.store.compile(catalogs=CATALOG)["tasks"][0]["signature"]
        graph = self.store.workflow()
        graph["nodes"][2]["data"]["trimStartSeconds"] = 0
        self.store.save(graph)
        second = self.store.compile(catalogs=CATALOG)["tasks"][0]["signature"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
