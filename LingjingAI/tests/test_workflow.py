"""Offline workflow regressions: only temporary projects and fake paid endpoints."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as studio
from workflow import WorkflowError, WorkflowStore, validate_structure


CATALOG = {
    "image": [{"id": "fake-image", "name": "Offline image", "max_refs": 2,
               "promptMaxLength": 1000, "params": {}}],
    "video": [{"id": "fake-video", "name": "Offline video", "max_refs": 2,
               "promptMaxLength": 1000,
               "params": {
                   "duration": {"type": "slider", "min": 2, "max": 10, "default": "2"},
                   "resolution": {"options": ["480P", "1080P"], "default": "480P"},
                   "image_mode": {"options": ["reference", "first-last-frame"], "default": "reference"}},
               "paramConstraints": [{"if": {"param": "image_mode", "eq": "first-last-frame"},
                                     "disable": {"param": "resolution", "options": ["1080P"]}}]}],
}


class FakeGateway:
    base_url = "http://127.0.0.1:9"

    def __init__(self):
        self.catalog = copy.deepcopy(CATALOG)
        self.model_calls = []
        self.submit_calls = []

    def models(self, kind):
        self.model_calls.append(kind)
        return copy.deepcopy(self.catalog[kind])

    def submit(self, *args):
        self.submit_calls.append(copy.deepcopy(args))
        return {"task_id": "fake-task-" + str(len(self.submit_calls))}


def node(nid, kind="video", *, data=None, shot_id="shot-001"):
    values = {"prompt": "A paper boat in morning light", "provider": "minimax-design",
              "modelId": "fake-" + kind, "params": {"duration": "2"} if kind == "video" else {}}
    if data is not None:
        values.update(data)
    return {"id": nid, "type": kind, "shotId": shot_id,
            "position": {"x": 10, "y": 20}, "data": values}


def edge(source, target, source_port="image", target_port="references", eid=None):
    return {"id": eid or source + "-" + target, "source": source, "target": target,
            "sourcePort": source_port, "targetPort": target_port}


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / (".tmp-workflow-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.assertTrue(self.root.resolve().is_relative_to(Path(__file__).resolve().parent))
        self.addCleanup(shutil.rmtree, self.root)
        self.store = WorkflowStore(self.root)
        self.gateway = FakeGateway()
        patcher = mock.patch.multiple(
            studio, DATA=self.root / "data", MEDIA=self.root / "media",
            PROJECT=self.root / "data/project.json", JOBS=self.root / "data/jobs.json",
            WORKFLOW=self.store, GW=self.gateway, POOL=mock.Mock(), INFLIGHT=set(),
            STOP=threading.Event())
        patcher.start()
        self.addCleanup(patcher.stop)
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            network = mock.patch(target, side_effect=AssertionError("No network in workflow tests"))
            network.start()
            self.addCleanup(network.stop)
        studio.initialise()
        studio.atomic_json(studio.DATA / "model-catalog.json", CATALOG)

    def save(self, nodes, edges=None):
        return self.store.save({"version": 1, "revision": self.store.workflow()["revision"],
                                "nodes": nodes, "edges": edges or []})

    def asset(self, *, aid="portrait", review="ready", role="reference", suffix=".png"):
        path = self.root / (aid + suffix)
        path.write_bytes(("fixture " + aid).encode())
        return self.store.import_asset({"id": aid, "path": str(path), "reviewStatus": review,
                                        "role": role, "logicalRoles": [aid]})

    def referenced_graph(self, *, review="ready", target_port="references", role="reference"):
        asset = self.asset(review=review, role=role)
        self.save([node("ref", "asset", data={"assetId": asset["id"]}), node("clip")],
                  [edge("ref", "clip", target_port=target_port)])
        return asset

    def request(self, nodes=None, **changes):
        body = {"nodeIds": ["clip"] if nodes is None else nodes,
                "requestId": "workflow-run-001", "confirmed": True}
        body.update(changes)
        return body

    def assert_unsubmitted(self):
        self.assertEqual(self.gateway.submit_calls, [])
        studio.POOL.submit.assert_not_called()

    def test_typed_ports_reject_video_as_image_reference(self):
        graph = {"nodes": [node("one"), node("two")],
                 "edges": [edge("one", "two", "video", "references")]}
        with self.assertRaises(WorkflowError) as caught:
            validate_structure(graph)
        self.assertEqual(caught.exception.code, "PORT_TYPE_MISMATCH")

    def test_dag_rejects_cycle_across_otherwise_compatible_ports(self):
        graph = {"nodes": [node("one", "review"), node("two", "review")],
                 "edges": [edge("one", "two", "out", "media"), edge("two", "one", "out", "media")]}
        with self.assertRaises(WorkflowError) as caught:
            validate_structure(graph)
        self.assertEqual(caught.exception.code, "CYCLE_DETECTED")

    def test_duplicate_edges_and_multiple_prompt_inputs_are_rejected(self):
        nodes = [node("prompt1", "prompt", data={"text": "first"}),
                 node("prompt2", "prompt", data={"text": "second"}), node("clip")]
        for edges in ([edge("prompt1", "clip", "text", "prompt", "e1"),
                       edge("prompt1", "clip", "text", "prompt", "e2")],
                      [edge("prompt1", "clip", "text", "prompt"),
                       edge("prompt2", "clip", "text", "prompt")]):
            with self.subTest(edges=edges), self.assertRaises(WorkflowError):
                validate_structure({"nodes": nodes, "edges": edges})

    def test_import_copies_asset_and_path_is_immutable(self):
        asset = self.asset()
        copied = Path(asset["path"])
        self.assertTrue(copied.is_relative_to(self.root / "media/assets"))
        self.assertEqual(copied.read_bytes(), (self.root / "portrait.png").read_bytes())
        (self.root / "portrait.png").unlink()
        self.assertTrue(copied.is_file())
        with self.assertRaises(WorkflowError):
            self.store.patch_asset(asset["id"], {"path": str(self.root / "elsewhere.png")})
        self.assertEqual(self.store.assets()["assets"][0]["path"], str(copied))

    def test_import_rejects_missing_relative_remote_and_unsupported_files(self):
        unsupported = self.root / "program.exe"
        unsupported.write_bytes(b"fixture")
        for path in (str(self.root / "missing.png"), "relative.png", "https://example.com/a.png", str(unsupported)):
            with self.subTest(path=path), self.assertRaises(WorkflowError):
                self.store.import_asset({"path": path})
        self.assertEqual(self.store.assets()["assets"], [])

    def test_same_asset_id_cannot_silently_replace_reviewed_file(self):
        original = self.asset()
        replacement = self.root / "new.png"
        replacement.write_bytes(b"different identity")
        with self.assertRaises(WorkflowError) as caught:
            self.store.import_asset({"id": original["id"], "path": str(replacement), "reviewStatus": "ready"})
        self.assertEqual(caught.exception.code, "ASSET_ID_CONFLICT")
        self.assertEqual(self.store.assets()["assets"][0]["sha256"], original["sha256"])

    def test_unreviewed_reference_blocks_compile_and_paid_run_until_reviewed(self):
        asset = self.referenced_graph(review="candidate")
        compiled = studio.workflow_compile({"nodeIds": ["clip"]})
        self.assertFalse(compiled["ready"])
        self.assertFalse(compiled["tasks"][0]["ready"])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assert_unsubmitted()
        self.store.patch_asset(asset["id"], {"reviewStatus": "ready"})
        self.assertTrue(studio.workflow_compile({"nodeIds": ["clip"]})["ready"])

    def test_deleted_managed_file_blocks_even_when_registry_says_ready(self):
        asset = self.referenced_graph()
        Path(asset["path"]).unlink()
        self.assertFalse(studio.workflow_compile({"nodeIds": ["clip"]})["ready"])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assert_unsubmitted()

    def test_required_reference_and_logical_role_need_real_connections(self):
        self.asset()
        for data in ({"requiredAssetIds": ["portrait"]}, {"requiredRoles": ["portrait"]}):
            with self.subTest(data=data):
                self.save([node("clip", data=data)])
                self.assertFalse(studio.workflow_compile()["ready"])
        self.referenced_graph()
        graph = self.store.workflow()
        graph["nodes"][1]["data"].update(requiredAssetIds=["portrait"], requiredRoles=["portrait"])
        self.store.save(graph)
        self.assertTrue(studio.workflow_compile()["ready"])

    def test_image_reference_and_connected_prompt_reach_actual_gateway_arguments(self):
        asset = self.referenced_graph()
        graph = self.store.workflow()
        graph["nodes"].append(node("words", "prompt", data={"text": "Use this connected prompt"}))
        graph["edges"].append(edge("words", "clip", "text", "prompt"))
        self.store.save(graph)
        with mock.patch.object(studio, "schedule", side_effect=lambda jid, fn: fn(jid)):
            result = studio.run_workflow(self.request())
        self.assertEqual(len(result["jobIds"]), 1)
        self.assertEqual(len(self.gateway.submit_calls), 1)
        args = self.gateway.submit_calls[0]
        self.assertEqual(args[2], "Use this connected prompt")
        self.assertEqual(args[4], [asset["path"]])
        job = studio.find_job(result["jobIds"][0])
        self.assertEqual(job["workflowNodeId"], "clip")
        self.assertEqual(len(job["workflowSignature"]), 64)

    def test_identity_reference_cannot_be_used_as_shot_first_frame(self):
        self.referenced_graph(role="identity", target_port="firstFrame")
        self.assertFalse(studio.workflow_compile()["ready"])

    def test_explicit_first_frame_sets_model_mode(self):
        self.referenced_graph(role="first-frame", target_port="firstFrame")
        compiled = studio.workflow_compile()
        self.assertTrue(compiled["ready"])
        self.assertEqual(compiled["tasks"][0]["params"]["image_mode"], "first-last-frame")

    def test_offline_cached_catalog_rejects_invalid_model_parameters(self):
        for params in ({"unsupported": "value"}, {"resolution": "999P"}, {"duration": "99"},
                       {"duration": "NaN"}, {"image_mode": "first-last-frame", "resolution": "1080P"}):
            with self.subTest(params=params):
                self.save([node("clip", data={"params": params})])
                self.assertFalse(studio.workflow_compile()["ready"])
        self.assertEqual(self.gateway.model_calls, [])

    def test_offline_length_validation_includes_negative_prompt(self):
        self.save([node("clip", data={"prompt": "A" * 995, "negativePrompt": "B" * 30})])
        self.assertFalse(studio.workflow_compile()["ready"])

    def test_run_revalidates_catalog_after_offline_compile(self):
        self.save([node("clip", data={"params": {"duration": "8"}})])
        self.assertTrue(studio.workflow_compile()["ready"])
        self.gateway.catalog["video"][0]["params"]["duration"]["max"] = 4
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assertGreater(len(self.gateway.model_calls), 0)
        self.assert_unsubmitted()

    def test_run_rechecks_asset_review_after_successful_offline_compile(self):
        asset = self.referenced_graph()
        self.assertTrue(studio.workflow_compile()["ready"])
        self.store.patch_asset(asset["id"], {"reviewStatus": "blocked"})
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()

    def test_imagegen_is_external_and_never_fakes_completion_or_runs(self):
        self.save([node("portrait", "image", data={"provider": "imagegen"})])
        compiled = studio.workflow_compile()
        self.assertEqual(compiled["tasks"][0]["status"], "external")
        self.assertFalse(compiled["tasks"][0]["ready"])
        self.assertEqual(compiled["nodes"][0]["outputs"], [])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request(["portrait"]))
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()

    def test_unfinished_generated_image_blocks_downstream_video(self):
        self.save([node("portrait", "image", data={"provider": "imagegen"}), node("clip")],
                  [edge("portrait", "clip")])
        result = studio.workflow_compile({"nodeIds": ["clip"]})
        self.assertFalse(result["ready"])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assert_unsubmitted()

    def test_ready_to_generate_image_is_not_yet_a_usable_video_reference(self):
        self.save([node("portrait", "image"), node("clip")], [edge("portrait", "clip")])
        compiled = studio.workflow_compile({"nodeIds": ["clip"]})
        self.assertFalse(compiled["ready"])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request())
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()

    def test_mixed_external_and_paid_selection_submits_nothing(self):
        self.save([node("clip"), node("portrait", "image", data={"provider": "imagegen"})])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request(["clip", "portrait"]))
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()

    def test_112_tasks_are_prepared_without_implicit_run(self):
        self.save([node("clip-" + str(i)) for i in range(112)])
        self.assertEqual(len(studio.workflow_compile()["tasks"]), 112)
        for changes in ({"nodeIds": None}, {"nodeIds": []},
                        {"nodeIds": ["clip-" + str(i) for i in range(112)]},
                        {"nodeIds": ["clip-0"], "confirmed": False}):
            with self.subTest(changes=changes), self.assertRaises(studio.StudioError):
                studio.run_workflow(self.request(**changes))
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()
        with mock.patch.object(studio, "schedule") as schedule:
            result = studio.run_workflow(self.request(["clip-0"]))
        self.assertEqual(len(result["jobIds"]), 1)
        self.assertEqual(schedule.call_count, 1)

    def test_confirmation_is_boolean_true_and_precedes_catalog_access(self):
        self.save([node("clip")])
        for value in (None, False, 1, "true"):
            with self.subTest(value=value), self.assertRaises(studio.StudioError):
                studio.run_workflow(self.request(confirmed=value))
        self.assertEqual(self.gateway.model_calls, [])
        self.assert_unsubmitted()

    def test_run_idempotency_survives_reopening_store_and_clearing_inflight(self):
        self.save([node("clip")])
        with mock.patch.object(studio, "schedule") as schedule:
            first = studio.run_workflow(self.request())
            studio.WORKFLOW = WorkflowStore(self.root)
            studio.INFLIGHT = set()
            second = studio.run_workflow(self.request())
        self.assertEqual(first["jobIds"], second["jobIds"])
        self.assertEqual(len(studio.read_json(studio.JOBS)), 1)
        self.assertEqual(schedule.call_count, 1)

    def test_run_id_cannot_be_reused_for_changed_selection(self):
        self.save([node("clip"), node("second")])
        with mock.patch.object(studio, "schedule"):
            studio.run_workflow(self.request())
            with self.assertRaises(studio.StudioError) as caught:
                studio.run_workflow(self.request(["second"]))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(studio.read_json(studio.JOBS)), 1)

    def test_late_preflight_failure_does_not_submit_earlier_valid_node(self):
        self.save([node("clip"), node("missing-shot", shot_id="does-not-exist")])
        with mock.patch.object(studio, "schedule") as schedule:
            with self.assertRaises(studio.StudioError):
                studio.run_workflow(self.request(["clip", "missing-shot"]))
        schedule.assert_not_called()
        self.assert_unsubmitted()
        jobs = studio.read_json(studio.JOBS)
        self.assertTrue(all(job["status"] != "queued" for job in jobs))
        for job in jobs:
            studio.submit_job(job["id"])
        self.assert_unsubmitted()

    def test_compile_failure_in_one_selected_node_blocks_entire_batch(self):
        self.save([node("clip"), node("bad", data={"requiredAssetIds": ["missing-reference"]})])
        with self.assertRaises(studio.StudioError):
            studio.run_workflow(self.request(["clip", "bad"]))
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assert_unsubmitted()

    def test_old_success_for_same_shot_does_not_complete_new_workflow(self):
        self.save([node("clip")])
        output = self.root / "old.mp4"
        output.write_bytes(b"prior trial")
        jobs = [{"id": "old", "shotId": "shot-001", "status": "succeeded", "mediaPath": str(output)}]
        compiled = self.store.compile(catalogs=CATALOG, jobs=jobs)
        self.assertEqual(compiled["tasks"][0]["status"], "ready")
        self.assertEqual(compiled["nodes"][0]["outputs"], [])

    def test_completion_requires_exact_signature_and_existing_media(self):
        self.save([node("clip")])
        signature = self.store.compile(catalogs=CATALOG)["tasks"][0]["signature"]
        output = self.root / "complete.mp4"
        output.write_bytes(b"completed fixture")
        jobs = [{"id": "match", "status": "succeeded", "workflowSignature": signature,
                 "mediaPath": str(output)}]
        self.assertEqual(self.store.compile(catalogs=CATALOG, jobs=jobs)["tasks"][0]["status"], "complete")
        self.save([node("clip", data={"prompt": "A changed scene"})])
        self.assertEqual(self.store.compile(catalogs=CATALOG, jobs=jobs)["tasks"][0]["status"], "ready")
        self.save([node("clip")])
        output.unlink()
        self.assertEqual(self.store.compile(catalogs=CATALOG, jobs=jobs)["tasks"][0]["status"], "ready")

    def test_switching_provider_cannot_claim_old_generation_as_imagegen_output(self):
        self.save([node("portrait", "image")])
        signature = self.store.compile(catalogs=CATALOG)["tasks"][0]["signature"]
        output = self.root / "earlier-provider.png"
        output.write_bytes(b"old provider result")
        jobs = [{"id": "old-provider", "status": "succeeded", "workflowSignature": signature,
                 "mediaPath": str(output)}]
        self.save([node("portrait", "image", data={"provider": "imagegen"})])
        result = self.store.compile(catalogs=CATALOG, jobs=jobs)
        self.assertEqual(result["tasks"][0]["status"], "external")
        self.assertEqual(result["nodes"][0]["outputs"], [])

    def test_changing_trim_cannot_mark_old_full_length_media_complete(self):
        self.save([node("clip")])
        signature = self.store.compile(catalogs=CATALOG)["tasks"][0]["signature"]
        output = self.root / "untrimmed.mp4"
        output.write_bytes(b"two second video fixture")
        jobs = [{"id": "untrimmed", "status": "succeeded", "workflowSignature": signature,
                 "mediaPath": str(output)}]
        self.save([node("clip", data={"trimSeconds": 1})])
        task = self.store.compile(catalogs=CATALOG, jobs=jobs)["tasks"][0]
        self.assertNotEqual(task["signature"], signature)
        self.assertNotEqual(task["status"], "complete")

    def test_review_node_does_not_release_media_before_explicit_approval(self):
        asset = self.asset(aid="video-source", suffix=".mp4")
        self.save([node("source", "asset", data={"assetId": asset["id"]}),
                   node("review", "review", data={"approved": False}), node("reuse", "reuse")],
                  [edge("source", "review", "video", "media"), edge("review", "reuse", "out", "source")])
        states = {item["id"]: item for item in self.store.compile(catalogs=CATALOG)["nodes"]}
        self.assertEqual(states["review"]["outputs"], [])
        self.assertEqual(states["reuse"]["outputs"], [])
        graph = self.store.workflow()
        graph["nodes"][1]["data"]["approved"] = True
        self.store.save(graph)
        states = {item["id"]: item for item in self.store.compile(catalogs=CATALOG)["nodes"]}
        self.assertEqual(states["reuse"]["outputs"][0]["path"], asset["path"])

    def test_completed_generated_reference_is_used_only_with_matching_signature(self):
        self.save([node("keyframe", "image"), node("clip")], [edge("keyframe", "clip")])
        first = self.store.compile(["keyframe"], catalogs=CATALOG)
        path = self.root / "generated.png"
        path.write_bytes(b"generated fixture")
        jobs = [{"id": "generated-job", "status": "succeeded", "mediaPath": str(path),
                 "workflowSignature": first["tasks"][0]["signature"]}]
        ready = self.store.compile(["clip"], catalogs=CATALOG, jobs=jobs)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["tasks"][0]["imagePaths"], [str(path)])
        graph = self.store.workflow()
        graph["nodes"][0]["data"]["prompt"] = "Another first frame"
        self.store.save(graph)
        self.assertFalse(self.store.compile(["clip"], catalogs=CATALOG, jobs=jobs)["ready"])

    def test_graph_save_snapshots_and_conflicts_do_not_modify_original_project(self):
        original_project = studio.PROJECT.read_bytes()
        first = self.save([node("clip")])
        changed = copy.deepcopy(first)
        changed["nodes"][0]["position"]["x"] = 700
        second = self.store.save(changed)
        with self.assertRaises(WorkflowError) as caught:
            self.store.save(first)
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.store.workflow(), second)
        snapshots = list((self.root / "data/workflow-snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(json.loads(snapshots[0].read_text(encoding="utf-8")), first)
        self.assertEqual(studio.PROJECT.read_bytes(), original_project)


if __name__ == "__main__":
    unittest.main()
