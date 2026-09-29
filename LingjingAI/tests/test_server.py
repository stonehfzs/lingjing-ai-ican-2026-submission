"""Offline regression tests. No Design account, network, or real project is used."""
from __future__ import annotations

import concurrent.futures
import copy
from email.message import Message
import http.client
import io
import json
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as studio
from gateway import Gateway, GatewayError
from workspaces import WorkspaceRegistry, WorkspaceStoreProxy


IMAGE_MODEL = {"id": "test-image", "name": "Fake image", "backend": "fake", "max_refs": 2,
               "promptMaxLength": 1000,
               "params": {"aspect_ratio": {"default": "16:9", "options": ["16:9", "1:1"]}}}
VIDEO_MODEL = {"id": "wan3.0-video", "name": "Fake video", "backend": "fake", "max_refs": 2,
               "params": {"duration": {"type": "slider", "min": 2, "max": 30, "default": "2"},
                          "aspect_ratio": {"default": "16:9", "options": ["16:9"]}}}


class FakeGateway:
    base_url = "http://127.0.0.1:9"

    def __init__(self, workspace):
        self.root = workspace
        self.submit_calls = []
        self.query_calls = []
        self.submit_result = {"task_id": "upstream-task"}
        self.query_result = {"status": "processing"}
        self.submit_error = self.query_error = None

    def models(self, kind):
        return copy.deepcopy([IMAGE_MODEL if kind == "image" else VIDEO_MODEL])

    def submit(self, *args):
        self.submit_calls.append(copy.deepcopy(args))
        if self.submit_error:
            raise self.submit_error
        return copy.deepcopy(self.submit_result)

    def query(self, task_id):
        self.query_calls.append(task_id)
        if self.query_error:
            raise self.query_error
        return copy.deepcopy(self.query_result)

    def workspace(self):
        return {"dir": str(self.root)}


class IsolatedStudioTest(unittest.TestCase):
    def setUp(self):
        # mkdir's normal inherited ACL works in restricted Windows workspaces;
        # tempfile's mode=0700 can make its directory inaccessible there.
        self.root = Path(__file__).resolve().parent / (".tmp-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.assertTrue(self.root.resolve().is_relative_to(Path(__file__).resolve().parent))
        self.addCleanup(shutil.rmtree, self.root)
        self.workspace = self.root / "upstream"
        self.workspace.mkdir()
        self.gw = FakeGateway(self.workspace)
        self.pool = mock.Mock()
        self.patches = mock.patch.multiple(studio, DATA=self.root / "data", MEDIA=self.root / "media",
                                         PROJECT=self.root / "data/project.json", JOBS=self.root / "data/jobs.json",
                                         REGISTRY=WorkspaceRegistry(self.root), WORKFLOW=WorkspaceStoreProxy(WorkspaceRegistry(self.root)),
                                         GW=self.gw, POOL=self.pool, INFLIGHT=set(), STOP=threading.Event())
        self.patches.start()
        self.addCleanup(self.patches.stop)
        # Fail closed even if a code path accidentally ignores the fake gateway.
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            patcher = mock.patch(target, side_effect=AssertionError("Network is forbidden in this test suite"))
            patcher.start()
            self.addCleanup(patcher.stop)
        studio.initialise()

    def request(self, **changes):
        body = {"confirmed": True, "requestId": "offline-request-001", "kind": "image",
                "shotId": "shot-001", "modelId": "test-image", "prompt": "A paper boat",
                "params": {"aspect_ratio": "16:9"}, "imagePaths": []}
        body.update(changes)
        return body

    def job(self, **changes):
        return studio.prepare_job(self.request(**changes))[0]

    def completed_image(self):
        path = self.workspace / "output.png"
        path.write_bytes(b"fake-image-bytes" * 30)
        self.gw.query_result = {"status": "succeeded", "result": {"path": "output.png"}}
        return path

    def handler(self, path="/api/jobs", body=None, headers=None):
        handler = object.__new__(studio.Handler)
        handler.path = path
        handler.server = SimpleNamespace(server_port=8766)
        handler.headers = Message()
        content = json.dumps(body or {}).encode()
        for key, value in {"Host": "127.0.0.1:8766", "Content-Type": "application/json",
                           "Content-Length": str(len(content)), "X-Studio-Request": "1", **(headers or {})}.items():
            handler.headers[key] = value
        handler.rfile, handler.wfile = io.BytesIO(content), io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        return handler

    def test_explicit_confirmation_required_before_any_submission(self):
        with self.assertRaises(studio.StudioError) as caught:
            studio.prepare_job(self.request(confirmed=False))
        self.assertEqual(caught.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assertEqual(self.gw.submit_calls, [])

    def test_same_request_is_durable_and_scheduled_only_once(self):
        body = self.request()
        with mock.patch.object(studio, "schedule") as schedule:
            first, second = self.handler(body=body), self.handler(body=body)
            first.guarded("POST")
            second.guarded("POST")
        self.assertEqual(first.send_response.call_args.args[0], 202)
        self.assertEqual(second.send_response.call_args.args[0], 200)
        self.assertEqual(schedule.call_count, 1)
        self.assertEqual(len(studio.read_json(studio.JOBS)), 1)

    def test_concurrent_identical_requests_create_one_job(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: studio.prepare_job(self.request()), range(16)))
        self.assertEqual(sum(fresh for _, fresh in results), 1)
        self.assertEqual(len({job["id"] for job, _ in results}), 1)
        self.assertEqual(len(studio.read_json(studio.JOBS)), 1)

    def test_reusing_request_id_with_changed_parameters_conflicts(self):
        self.job()
        with self.assertRaises(studio.StudioError) as caught:
            studio.prepare_job(self.request(prompt="Different paid request"))
        self.assertEqual((caught.exception.code, caught.exception.status), ("IDEMPOTENCY_CONFLICT", 409))

    def test_submit_job_cannot_resubmit_same_paid_job(self):
        job = self.job()
        studio.submit_job(job["id"])
        studio.submit_job(job["id"])
        self.assertEqual(len(self.gw.submit_calls), 1)
        self.assertEqual(studio.find_job(job["id"])["taskId"], "upstream-task")

    def test_lost_response_remains_unknown_without_retry(self):
        self.gw.submit_error = GatewayError("SUBMISSION_STATUS_UNKNOWN", "lost response", submission_uncertain=True)
        job = self.job()
        studio.submit_job(job["id"])
        self.assertEqual(studio.find_job(job["id"])["status"], "unknown")
        existing, fresh = studio.prepare_job(self.request())
        self.assertFalse(fresh)
        self.assertEqual(existing["id"], job["id"])
        studio.refresh_job(job["id"])
        studio.submit_job(job["id"])
        self.assertEqual(len(self.gw.submit_calls), 1)
        self.assertEqual(self.gw.query_calls, [])

    def test_missing_task_id_is_unknown_not_failed(self):
        self.gw.submit_result = {"ok": True}
        job = self.job()
        studio.submit_job(job["id"])
        self.assertEqual(studio.find_job(job["id"])["status"], "unknown")
        self.assertEqual(len(self.gw.submit_calls), 1)

    def test_confirmed_rejection_is_failed_and_not_resubmitted(self):
        self.gw.submit_error = GatewayError("MODEL_REJECTED", "rejected", status=400)
        job = self.job()
        studio.submit_job(job["id"])
        self.assertEqual(studio.find_job(job["id"])["status"], "failed")
        studio.submit_job(job["id"])
        self.assertEqual(len(self.gw.submit_calls), 1)

    def test_schedule_coalesces_inflight_work(self):
        studio.schedule("job-id", studio.refresh_job)
        studio.schedule("job-id", studio.refresh_job)
        self.assertEqual(self.pool.submit.call_count, 1)

    def test_revision_conflict_preserves_newer_project(self):
        initial = studio.read_json(studio.PROJECT)
        newer = copy.deepcopy(initial)
        newer["title"] = "Newer title"
        saved = studio.save_project(newer)
        with self.assertRaises(studio.StudioError) as caught:
            studio.save_project(initial)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(studio.read_json(studio.PROJECT), saved)
        snapshots = list((studio.DATA / "snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(studio.read_json(snapshots[0]), initial)

    def test_invalid_coordinate_and_dangling_edge_rejected(self):
        for invalid in (float("nan"), float("inf"), True, 100001):
            with self.subTest(coordinate=invalid):
                project = studio.read_json(studio.PROJECT)
                project["shots"][0]["position"]["x"] = invalid
                with self.assertRaises(studio.StudioError):
                    studio.save_project(project)
        project = studio.read_json(studio.PROJECT)
        project["edges"][0]["target"] = "missing-shot"
        with self.assertRaises(studio.StudioError):
            studio.save_project(project)

    def test_static_encoded_traversal_is_rejected(self):
        secret = self.root / "outside.png"
        secret.write_bytes(b"private" * 30)
        for relative in ("../outside.png", "%2e%2e%2foutside.png", "..%5coutside.png", str(secret)):
            with self.subTest(relative=relative):
                handler = self.handler()
                with self.assertRaises(studio.StudioError):
                    handler.send_file(studio.MEDIA, relative)
                self.assertEqual(handler.wfile.getvalue(), b"")

    def test_byte_range_serves_only_requested_media(self):
        (studio.MEDIA / "sample.png").write_bytes(b"0123456789")
        handler = self.handler(headers={"Range": "bytes=2-5"})
        handler.send_file(studio.MEDIA, "sample.png")
        self.assertEqual(handler.send_response.call_args.args, (206,))
        self.assertEqual(handler.wfile.getvalue(), b"2345")

    def test_cross_origin_or_missing_write_header_is_rejected(self):
        for headers in ({"Host": "attacker.example:8766"}, {"Origin": "https://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-Studio-Request": ""}):
            with self.subTest(headers=headers):
                handler = self.handler(headers=headers)
                with self.assertRaises(studio.StudioError):
                    handler.check_origin(write=True)

    def test_success_localizes_output_and_increments_project_revision(self):
        source = self.completed_image()
        original = source.read_bytes()
        job = self.job()
        revision = studio.read_json(studio.PROJECT)["revision"]
        studio.submit_job(job["id"])
        result = studio.refresh_job(job["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(Path(result["mediaPath"]).read_bytes(), original)
        self.assertTrue(Path(result["mediaPath"]).is_relative_to(studio.MEDIA))
        self.assertEqual(result["mediaUrl"], f"/media/{job['id']}/source1.png")
        source.unlink()
        self.assertEqual(Path(result["mediaPath"]).read_bytes(), original)
        project = studio.read_json(studio.PROJECT)
        self.assertEqual(project["revision"], revision + 1)
        self.assertEqual(project["shots"][0]["lastJobId"], job["id"])
        self.assertEqual(len(self.gw.submit_calls), 1)

    def test_video_two_second_generation_preserves_source_and_trims_locally(self):
        original = b"fake-two-second-video" * 30
        (self.workspace / "output.mp4").write_bytes(original)
        job = self.job(kind="video", modelId="wan3.0-video", params={"duration": "2", "aspect_ratio": "16:9"}, trimSeconds=1)
        studio.submit_job(job["id"])
        self.gw.query_result = {"status": "succeeded", "result": {"path": "output.mp4"}}

        def local_media_command(args, **kwargs):
            Path(args[-1]).write_bytes(b"local-preview-or-thumbnail" * 20)
            return SimpleNamespace(returncode=0)

        with mock.patch.object(studio, "ffmpeg_binary", side_effect=lambda name: name), \
             mock.patch.object(studio.subprocess, "run", side_effect=local_media_command) as local_run, \
             mock.patch("workflow.probe_media", return_value={"duration": 2, "probeStatus": "verified", "streams": [{"codec_type": "video"}]}), \
             mock.patch.object(studio.subprocess, "check_output", return_value=b'{"format":{"duration":"1.000000"}}'):
            result = studio.refresh_job(job["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(self.gw.submit_calls), 1)
        self.assertEqual(self.gw.submit_calls[0][3]["duration"], "2")
        self.assertEqual(Path(result["sourcePath"]).read_bytes(), original)
        self.assertNotEqual(result["sourcePath"], result["mediaPath"])
        self.assertTrue(Path(result["mediaPath"]).is_file())
        self.assertEqual(result["mediaInfo"]["format"]["duration"], "1.000000")
        self.assertEqual(local_run.call_count, 2)
        registered = next(a for a in studio.WORKFLOW.assets()["assets"] if a["id"] == result["assetId"])
        self.assertEqual(Path(registered["path"]).read_bytes(), original)

    def test_relative_output_cannot_escape_upstream_workspace(self):
        (self.root / "outside.png").write_bytes(b"private" * 30)
        job = self.job()
        with self.assertRaises(studio.StudioError) as caught:
            studio.collect_media(job, {"result": {"path": "../outside.png"}})
        self.assertEqual(caught.exception.code, "DOWNLOAD_FAILED")
        self.assertEqual(list((studio.MEDIA / job["id"]).iterdir()), [])

    def test_download_failure_recovers_existing_task_without_generation(self):
        job = self.job()
        studio.submit_job(job["id"])
        self.gw.query_result = {"status": "succeeded", "result": {"path": "output.png"}}
        self.assertEqual(studio.refresh_job(job["id"])["status"], "download_failed")
        self.completed_image()
        self.assertEqual(studio.refresh_job(job["id"])["status"], "succeeded")
        self.assertEqual(len(self.gw.submit_calls), 1)
        self.assertEqual(self.gw.query_calls, ["upstream-task", "upstream-task"])

    def test_query_error_keeps_task_and_is_recoverable(self):
        job = self.job()
        studio.submit_job(job["id"])
        self.gw.query_error = GatewayError("GATEWAY_TIMEOUT", "offline")
        result = studio.refresh_job(job["id"])
        self.assertEqual(result["status"], "processing")
        self.assertEqual(result["taskId"], "upstream-task")
        self.gw.query_error = None
        self.completed_image()
        self.assertEqual(studio.refresh_job(job["id"])["status"], "succeeded")
        self.assertEqual(len(self.gw.submit_calls), 1)

    def test_project_write_failure_does_not_lose_completed_media_attachment(self):
        self.completed_image()
        job = self.job()
        studio.submit_job(job["id"])
        with mock.patch.object(studio, "save_project", side_effect=OSError("temporary disk failure")):
            studio.refresh_job(job["id"])
        studio.refresh_job(job["id"])
        shot = studio.read_json(studio.PROJECT)["shots"][0]
        self.assertEqual(shot.get("lastJobId"), job["id"])
        self.assertTrue(shot.get("mediaUrl"))
        self.assertEqual(len(self.gw.submit_calls), 1)

    def test_restart_marks_incomplete_submission_unknown_without_retry(self):
        job = self.job()
        studio.update_job(job["id"], status="submitting")
        with mock.patch.object(sys, "argv", ["server.py"]), \
             mock.patch.object(studio.threading, "Thread"), \
             mock.patch.object(studio, "ThreadingHTTPServer") as httpd, \
             mock.patch("builtins.print"):
            studio.main()
        self.assertEqual(studio.find_job(job["id"])["status"], "unknown")
        self.assertEqual(self.gw.submit_calls, [])
        httpd.assert_called_once()

    def test_public_job_does_not_expose_signed_urls_or_payload(self):
        job = self.job()
        job["upstream"] = {"url": "https://example.invalid/output?secret=token"}
        public = studio.public_job(job)
        for name in ("upstream", "payload", "requestHash", "gatewayResponse"):
            self.assertNotIn(name, public)


class GatewayTransportTest(unittest.TestCase):
    def setUp(self):
        self.gw = Gateway("http://127.0.0.1:9")
        self.gw._opener = mock.MagicMock()

    def test_remote_gateway_and_filename_traversal_are_rejected(self):
        for url in ("https://127.0.0.1:8001", "http://example.com", "http://u:p@localhost:8001", "http://localhost:8001/path"):
            with self.subTest(url=url), self.assertRaises(GatewayError):
                Gateway(url)
        for filename in ("../x.png", "..\\x.png", "C:\\x.png", "a..png", "x.mp4"):
            with self.subTest(filename=filename), self.assertRaises(GatewayError):
                Gateway.build_body("image", IMAGE_MODEL, "boat", {}, [], filename)

    def test_timeout_billable_post_is_once_and_unknown(self):
        self.gw._opener.open.side_effect = TimeoutError("timed out")
        with self.assertRaises(GatewayError) as caught:
            self.gw._request("POST", "/fake-submit", {}, submission=True)
        self.assertTrue(caught.exception.submission_uncertain)
        self.assertEqual(self.gw._opener.open.call_count, 1)

    def test_wan_does_not_submit_an_unsupported_one_second_duration(self):
        with self.assertRaises(GatewayError) as caught:
            Gateway.build_body("video", VIDEO_MODEL, "boat", {"duration": "1"}, [], "test.mp4")
        self.assertEqual(caught.exception.code, "INVALID_DURATION")
        self.gw._opener.open.assert_not_called()
        body = Gateway.build_body("video", VIDEO_MODEL, "boat", {"duration": "2"}, [], "test.mp4")
        self.assertEqual(body["params"]["duration"], "2")

    def test_server_error_billable_post_is_once_and_unknown(self):
        self.gw._opener.open.side_effect = urllib.error.HTTPError("http://127.0.0.1:9/fake", 503, "unavailable", {}, io.BytesIO(b'{"code":"RETRY_LATER"}'))
        with self.assertRaises(GatewayError) as caught:
            self.gw._request("POST", "/fake-submit", {}, submission=True)
        self.assertTrue(caught.exception.submission_uncertain)
        self.assertEqual(self.gw._opener.open.call_count, 1)

    def test_truncated_response_is_unknown_and_never_retried(self):
        self.gw._opener.open.return_value.__enter__.return_value.read.side_effect = http.client.IncompleteRead(b'{"task_', 100)
        with self.assertRaises(GatewayError) as caught:
            self.gw._request("POST", "/fake-submit", {}, submission=True)
        self.assertTrue(caught.exception.submission_uncertain)
        self.assertEqual(self.gw._opener.open.call_count, 1)

    def test_non_json_submission_response_is_unknown(self):
        self.gw._opener = mock.MagicMock()
        self.gw._opener.open.return_value.__enter__.return_value.read.return_value = b"<html>bad gateway</html>"
        with self.assertRaises(GatewayError) as caught:
            self.gw._request("POST", "/fake-submit", {}, submission=True)
        self.assertTrue(caught.exception.submission_uncertain)
        self.assertEqual(self.gw._opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
