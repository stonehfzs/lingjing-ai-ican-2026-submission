import concurrent.futures
import contextvars
import json
from pathlib import Path
import tempfile
import unittest

from workspaces import WorkspaceRegistry, WorkspacePath, scope, current, scoped_urls
from workflow import WorkflowError, write


class WorkspaceIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        write(self.root / "data/project.json", {"id": "original", "title": "Original", "shots": []})
        self.registry = WorkspaceRegistry(self.root)
        self.registry.persist()
        self.a = self.registry.create({"name": "A", "script": "A script", "activate": False})
        self.b = self.registry.create({"name": "B", "script": "B script", "activate": False})

    def tearDown(self):
        self.temp.cleanup()

    def test_new_project_is_empty_and_does_not_copy_cangtou_shots(self):
        project = json.loads(self.a.project.read_text(encoding="utf-8"))
        self.assertEqual(project["script"], "A script")
        self.assertEqual(project["shots"], [])
        self.assertNotEqual(self.a.root, self.b.root)

    def test_switch_does_not_redirect_already_captured_background_context(self):
        data = WorkspacePath(self.registry, "data")
        with scope(self.a):
            captured = contextvars.copy_context()
        self.registry.activate(self.b.id)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            path = pool.submit(captured.run, lambda: str(data / "jobs.json")).result()
        self.assertEqual(Path(path), self.a.jobs)
        self.assertEqual(current(self.registry).id, self.b.id)

    def test_parallel_requests_write_only_their_own_workspace(self):
        data = WorkspacePath(self.registry, "data")
        def writer(workspace, text):
            with scope(workspace):
                for i in range(10):
                    (data / f"record-{i}.txt").write_text(text)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: writer(*pair), [(self.a, "A"), (self.b, "B")]))
        self.assertEqual((self.a.data / "record-9.txt").read_text(), "A")
        self.assertEqual((self.b.data / "record-9.txt").read_text(), "B")

    def test_media_urls_keep_project_identity_after_switch(self):
        value = {"mediaUrl": "/media/same.mp3", "prompt": "Do not rewrite /media text", "rows": [{"thumbnailUrl": "/media/thumb.png"}]}
        first = scoped_urls(value, self.a.id)
        self.assertEqual(first["mediaUrl"], f"/w/{self.a.id}/media/same.mp3")
        self.assertEqual(first["prompt"], value["prompt"])
        self.assertEqual(scoped_urls(first, self.b.id), first)

    def test_nonempty_source_folder_cannot_be_overwritten(self):
        original = self.root / "source-script"
        original.mkdir()
        (original / "script.md").write_text("keep")
        with self.assertRaises(WorkflowError):
            self.registry.create({"name": "Unsafe", "workspacePath": str(original)})
        self.assertEqual((original / "script.md").read_text(), "keep")

    def test_registry_reload_and_archive_keep_files(self):
        self.registry.activate(self.a.id)
        self.registry.update(self.a.id, {"archived": True})
        reloaded = WorkspaceRegistry(self.root)
        self.assertTrue(reloaded.get(self.a.id).archived)
        self.assertTrue(self.a.project.is_file())
        self.assertNotEqual(reloaded.listing()["activeWorkspaceId"], self.a.id)
        reloaded.update(self.a.id, {"archived": False})
        reloaded.activate(self.a.id)
        self.assertEqual(reloaded.listing()["activeWorkspaceId"], self.a.id)


if __name__ == "__main__":
    unittest.main()
