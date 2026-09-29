"""Isolated creator/audio workflow integration; no network or original projects."""
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
from creator import script_document, shot_readiness, review_bundle
from gateway import GatewayError
from workflow import write
from workspaces import WorkspaceRegistry, WorkspacePath, WorkspaceStoreProxy, scope


VIDEO = {"id": "wan3.0-video", "model_name": "wan3.0-video", "backend": "wan_i2v",
         "max_refs": 10, "max_audio_refs": 5, "max_video_refs": 5,
         "params": {"duration": {"options": ["5"], "default": "5"},
                    "image_mode": {"options": ["reference"], "default": "reference"},
                    "generate_audio": {"options": ["true", "false"], "default": "true"}}}
SPEECH = {"id": "speech-2.8-hd", "backend": "minimax_tts", "params": {}, "promptMaxLength": 10000}


class FakeGateway:
    base_url = "http://127.0.0.1:9"

    def __init__(self):
        self.submissions = []
        self.query_calls = []
        self.query_result = {"status": "processing"}
        self.submit_error = None

    def models(self, kind):
        return copy.deepcopy([VIDEO] if kind == "video" else [SPEECH] if kind == "speech" else [])

    def submit(self, *args):
        self.submissions.append(copy.deepcopy(args))
        if self.submit_error:
            raise self.submit_error
        return {"task_id": "fake-video-task"}

    def query(self, task_id):
        self.query_calls.append(task_id)
        return copy.deepcopy(self.query_result)


def performance(aid, text, cue_ids=None, **extra):
    return {"id": aid, "name": aid, "mediaType": "audio", "role": "dialogue_performance",
            "reviewStatus": "ready", "spokenText": text, "cueIds": cue_ids or [], **extra}


def bound(aid, **extra):
    return {"assetId": aid, "usage": "post_mix", "role": "dialogue_performance", "enabled": True, **extra}


class ReadinessTest(unittest.TestCase):
    def readiness(self, plan, bindings, assets):
        return shot_readiness({"audioPlan": plan}, {"status": "ready"}, bindings, assets)

    def test_script_headings_match_complete_shot_ids_and_explicit_blocks(self):
        project = {"id": "unrelated-film", "script": "# S001 第一镜\n\n第一段。\n\n# S0010 第二镜\n\n第二段。\n\n# 场景说明\n\n未分镜段落。",
                   "shots": [{"id": "S001"}, {"id": "S0010"}]}
        first = script_document(project)
        blocks = {block["text"]: block for block in first["blocks"]}
        self.assertEqual(blocks["第一段。"]["shotIds"], ["S001"])
        self.assertEqual(blocks["第二段。"]["shotIds"], ["S0010"])
        self.assertEqual(blocks["未分镜段落。"]["shotIds"], [])
        project["shots"][0]["sourceBlockIds"] = [blocks["未分镜段落。"]["id"]]
        again = {block["text"]: block for block in script_document(project)["blocks"]}
        self.assertEqual(again["未分镜段落。"]["shotIds"], ["S001"])
        self.assertEqual(again["未分镜段落。"]["id"], blocks["未分镜段落。"]["id"])

    def test_voice_preview_relabelled_in_binding_cannot_cover_real_cue(self):
        plan = {"lines": [{"text": "正式台词"}], "dialogueCueIds": ["cue-1"]}
        preview = performance("voice", "正式台词", ["cue-1"], role="voice_identity")
        state = self.readiness(plan, [bound("voice", cueId="cue-1")], {"voice": preview})
        self.assertFalse(state["audioReady"])

    def test_all_explicit_cue_ids_are_required_even_without_duplicate_lines(self):
        state = self.readiness({"dialogueCueIds": ["cue-1", "cue-2"]},
                               [bound("one", cueId="cue-1")],
                               {"one": performance("one", "first", ["cue-1"])})
        self.assertFalse(state["audioReady"])

    def test_cues_can_be_covered_without_redundantly_listing_dialogue_cue_ids(self):
        plan = {"cues": [{"id": "cue-1", "text": "first"}, {"id": "cue-2", "text": "second"}]}
        one = performance("one", "first", ["cue-1"])
        two = performance("two", "second", ["cue-2"])
        self.assertFalse(self.readiness(plan, [bound("one")], {"one": one})["audioReady"])
        self.assertTrue(self.readiness(plan, [bound("one"), bound("two")], {"one": one, "two": two})["audioReady"])

    def test_same_words_from_two_characters_still_require_both_cues(self):
        plan = {"lines": [{"id": "line-1", "characterId": "mother", "text": "知道了。"},
                          {"id": "line-2", "characterId": "daughter", "text": "知道了。"}]}
        state = self.readiness(plan, [bound("mother", cueId="line-1")],
                               {"mother": performance("mother", "知道了。", ["line-1"], characterId="mother")})
        self.assertFalse(state["audioReady"])

    def test_candidate_or_disabled_cue_does_not_complete_audio(self):
        plan = {"lines": [{"text": "first"}, {"text": "second"}], "dialogueCueIds": ["cue-1", "cue-2"]}
        assets = {"one": performance("one", "first", ["cue-1"]),
                  "two": performance("two", "second", ["cue-2"], reviewStatus="candidate")}
        bindings = [bound("one"), bound("two")]
        self.assertFalse(self.readiness(plan, bindings, assets)["audioReady"])
        assets["two"]["reviewStatus"] = "ready"
        bindings[1]["enabled"] = False
        self.assertFalse(self.readiness(plan, bindings, assets)["audioReady"])
        bindings[1]["enabled"] = True
        self.assertTrue(self.readiness(plan, bindings, assets)["audioReady"])

    def test_disabled_ambience_does_not_satisfy_required_sound_effects(self):
        plan = {"soundEffects": "Rain on the window"}
        assets = {"rain": performance("rain", "", role="ambience")}
        bindings = [bound("rain", role="ambience", enabled=False)]
        self.assertFalse(self.readiness(plan, bindings, assets)["audioReady"])
        bindings[0]["enabled"] = True
        self.assertTrue(self.readiness(plan, bindings, assets)["audioReady"])


class CreatorIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / (".tmp-creator-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.assertTrue(self.root.resolve().is_relative_to(Path(__file__).parent.resolve()))
        self.addCleanup(shutil.rmtree, self.root)
        write(self.root / "data/project.json", {"id": "legacy-test", "title": "Legacy", "shots": []})
        self.registry = WorkspaceRegistry(self.root)
        self.a = self.registry.create({"name": "First screenplay", "script": "# Original script\n\nA unique paragraph.\n\nAnother paragraph."})
        self.b = self.registry.create({"name": "Other screenplay", "activate": False})
        self.gw, self.pool = FakeGateway(), mock.Mock()
        patcher = mock.patch.multiple(studio, REGISTRY=self.registry, GW=self.gw, POOL=self.pool,
                                      DATA=WorkspacePath(self.registry, "data"), MEDIA=WorkspacePath(self.registry, "media"),
                                      PROJECT=WorkspacePath(self.registry, "project"), JOBS=WorkspacePath(self.registry, "jobs"),
                                      WORKFLOW=WorkspaceStoreProxy(self.registry), INFLIGHT=set(), STOP=threading.Event())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.context = scope(self.a)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
            network = mock.patch(target, side_effect=AssertionError("No network in creator tests"))
            network.start()
            self.addCleanup(network.stop)
        studio.create_shot({"id": "S001", "title": "First", "modelId": VIDEO["id"], "params": {"duration": "5"}, "videoPrompt": "A scene"})
        studio.create_shot({"id": "S002", "title": "Second", "modelId": VIDEO["id"], "params": {"duration": "5"}, "videoPrompt": "Another scene"})

    def project(self):
        return studio.read_json(studio.PROJECT)

    def asset(self, aid, kind="audio", **extra):
        path = self.a.media / (aid + {"audio": ".wav", "image": ".png", "video": ".mp4"}[kind])
        path.write_bytes(b"offline fixture" * 30)
        row = {"id": aid, "name": aid, "path": str(path), "mediaType": kind, "role": "reference", "reviewStatus": "ready", "duration": 3, **extra}
        document = self.a.store.assets()
        document["assets"].append(row)
        write(self.a.store.assets_path, document)
        return row

    def fake_import(self, spec):
        document = studio.WORKFLOW.assets()
        row = next((a for a in document["assets"] if a["id"] == spec["id"]), None)
        if row is None:
            row = {**copy.deepcopy(spec), "mediaType": "audio", "mediaUrl": "/media/registered-" + spec["id"] + ".wav", "duration": 3}
            document["assets"].append(row)
            write(studio.WORKFLOW.assets_path, document)
        return row

    def speech_body(self, **extra):
        return {"confirmed": True, "requestId": "speech-cue-test", "voiceAssetId": "voice",
                "modelId": SPEECH["id"], "text": "Actual dialogue", "params": {}, "shotId": "S001", "cueId": "cue-1", **extra}

    def test_selected_script_paragraph_creates_linked_shot_without_rewriting_script(self):
        before = self.project()["script"]
        document = script_document(self.project())
        paragraph = next(block for block in document["blocks"] if block["text"] == "A unique paragraph.")
        result = studio.create_shot({"title": "From paragraph", "sourceBlockIds": [paragraph["id"]]})
        bundle = review_bundle(self.project(), result["shot"]["id"], self.a.store.workflow(), self.a.store.assets())
        self.assertEqual([block["id"] for block in bundle["scriptBlocks"]], [paragraph["id"]])
        self.assertEqual(result["shot"]["action"], paragraph["text"])
        self.assertEqual(self.project()["script"], before)

    def test_director_cast_updates_only_matching_identity_and_other_workspace_stays_clean(self):
        from direction import scaffold, save_plan
        self.asset('old', role='voice_identity', vendorVoiceId='old-voice', voiceId='actor')
        self.asset('new', role='voice_identity', vendorVoiceId='new-voice', voiceId='actor', reviewStatus='candidate')
        self.asset('other', role='voice_identity', vendorVoiceId='other-voice', voiceId='other')
        studio.update_shot('S001', {'bindings':[{'assetId':'old','role':'voice_identity','usage':'voice_identity','voiceId':'actor'},
                                               {'assetId':'other','role':'voice_identity','usage':'voice_identity','voiceId':'other'}]})
        plan=scaffold(self.project());plan['characters']=[{'id':'actor','name':'维修员','candidateAssetIds':['old','new']}]
        plan=save_plan(self.a.data/'direction.json',plan,self.project())
        studio.select_cast({'revision':plan['revision'],'projectRevision':self.project()['revision'],'voiceId':'actor','assetId':'new'})
        bindings=self.project()['shots'][0]['bindings']
        self.assertEqual([b['assetId'] for b in bindings],['new','other'])
        self.assertFalse((self.b.data/'direction.json').exists())
        self.assertEqual(self.project()['shots'][0]['review']['status'],'unreviewed')

    def test_director_cast_rejects_other_character_candidate(self):
        from direction import scaffold, save_plan
        self.asset('wrong', role='voice_identity', vendorVoiceId='id', voiceId='other')
        plan=scaffold(self.project());plan['characters']=[{'id':'actor','name':'维修员','candidateAssetIds':['wrong']}]
        plan=save_plan(self.a.data/'direction.json',plan,self.project())
        with self.assertRaises(studio.StudioError):
            studio.select_cast({'revision':plan['revision'],'projectRevision':self.project()['revision'],'voiceId':'actor','assetId':'wrong'})

    def test_performance_markers_do_not_replace_canonical_dialogue(self):
        self.asset('voice', role='voice_identity',vendorVoiceId='actor-id',voiceId='actor')
        job,fresh=studio.prepare_audio_job(self.speech_body(text='来。走吧。',performance=[{'after':'来。','pause':.2}]),'speech')
        self.assertEqual(job['prompt'],'来。走吧。')
        self.assertEqual(job['payload']['text'],'来。<#0.2#>走吧。')

    def test_direction_gap_blocks_direct_video_before_submission(self):
        from direction import scaffold, save_plan
        save_plan(self.a.data/'direction.json',scaffold(self.project()),self.project())
        with self.assertRaises(studio.StudioError) as ctx:
            studio.prepare_job({'confirmed':True,'requestId':'blocked-dir-video','shotId':'S001','kind':'video','modelId':VIDEO['id'],'prompt':'scene','params':{'duration':'5'}})
        self.assertEqual(ctx.exception.code,'REFERENCE_COVERAGE')
        self.assertEqual(self.gw.submissions,[])

    def test_generation_preflight_is_non_mutating(self):
        before=studio.read_json(studio.JOBS,[])
        result,fresh=studio.prepare_job({'confirmed':True,'shotId':'S001','kind':'video','modelId':VIDEO['id'],'prompt':'scene','params':{'duration':'5'}},validate_only=True)
        self.assertTrue(result['valid']);self.assertFalse(result['willGenerate']);self.assertFalse(fresh)
        self.assertEqual(before,studio.read_json(studio.JOBS,[]));self.assertEqual(self.gw.submissions,[])

    def test_plan_approval_does_not_approve_a_future_completed_video(self):
        studio.update_shot("S001", {"status": "approved"}, review_only=True)
        shot = self.project()["shots"][0]
        self.assertEqual(shot["review"]["scope"], "plan")
        self.assertFalse(shot_readiness(shot, {"status": "complete", "jobId": "future-result"}, [], {})["deliveryReady"])

    def test_new_video_result_resets_old_media_approval(self):
        project = self.project()
        project["shots"][0].update(mediaUrl="/media/old.mp4", lastJobId="old-job", review={"status": "approved", "scope": "media", "jobId": "old-job"})
        write(self.a.project, project)
        job = {"id": "new-video", "kind": "video", "shotId": "S001", "status": "succeeded", "mediaUrl": "/media/new.mp4"}
        write(self.a.jobs, [job])
        studio.attach_job_result(job)
        shot = self.project()["shots"][0]
        self.assertEqual(shot["review"]["status"], "unreviewed")
        self.assertEqual(shot["lastJobId"], "new-video")

    def test_invalid_last_batch_patch_preserves_both_documents(self):
        before = self.a.project.read_bytes(), self.a.store.workflow_path.read_bytes()
        with self.assertRaises(studio.StudioError):
            studio.update_shots_batch({"revision": self.project()["revision"], "patches": [
                {"shotId": "S001", "title": "Should not persist"}, {"shotId": "missing", "title": "invalid"}]})
        self.assertEqual((self.a.project.read_bytes(), self.a.store.workflow_path.read_bytes()), before)

    def test_blocked_voice_candidate_cannot_launch_a_paid_performance(self):
        self.asset("voice", role="voice_identity", vendorVoiceId="ttv-actual", reviewStatus="blocked")
        with self.assertRaises(studio.StudioError):
            studio.prepare_audio_job(self.speech_body(), "speech")
        self.assertEqual(studio.read_json(studio.JOBS), [])

    def test_speech_completion_adds_candidate_but_does_not_overwrite_existing_video(self):
        self.asset("voice", role="voice_identity", vendorVoiceId="ttv-actual")
        project = self.project()
        project["shots"][0].update(mediaUrl="/media/final-video.mp4", thumbnailUrl="/media/poster.jpg", lastJobId="video-job", mediaKind="video",
                                  audioPlan={"lines": [{"text": "Actual dialogue"}], "dialogueCueIds": ["cue-1"]})
        write(self.a.project, project)
        job, _ = studio.prepare_audio_job(self.speech_body(), "speech")
        studio.update_job(job["id"], status="processing", taskId="speech-task")
        output = self.asset("generated-line")
        self.gw.query_result = {"status": "succeeded", "result": {"path": output["path"]}}
        media = {"mediaPath": output["path"], "mediaUrl": "/media/new-line.wav", "duration": 3}
        with mock.patch.object(studio, "collect_media", return_value=media), mock.patch.object(self.a.store, "import_asset", side_effect=self.fake_import):
            completed = studio.refresh_job(job["id"])
        self.assertTrue(completed["projectAttached"])
        shot = self.project()["shots"][0]
        self.assertEqual((shot["mediaUrl"], shot["thumbnailUrl"], shot["lastJobId"], shot["mediaKind"]),
                         ("/media/final-video.mp4", "/media/poster.jpg", "video-job", "video"))
        asset_map = {a["id"]: a for a in self.a.store.assets()["assets"]}
        candidate = asset_map[completed["assetId"]]
        self.assertEqual(candidate["role"], "dialogue_performance")
        self.assertEqual(candidate["reviewStatus"], "candidate")
        self.assertEqual(candidate["cueIds"], ["cue-1"])
        self.assertFalse(shot_readiness(shot, {"status": "complete"}, shot["bindings"], asset_map)["audioReady"])

    def test_paid_voice_design_download_can_recover_without_designing_again(self):
        job, _ = studio.prepare_audio_job({"confirmed": True, "requestId": "design-recovery", "description": "A voice",
                                          "previewText": "Preview", "characterId": "new-role"}, "voice_design")
        output = self.asset("voice-result")
        response = {"voice_id": "ttv-existing-paid-voice", "trial_audio_url": output["path"]}
        media = {"mediaPath": output["path"], "mediaUrl": "/media/voice-result.wav", "duration": 3}
        with mock.patch.object(studio, "design_voice", return_value=response) as paid, \
             mock.patch.object(studio, "collect_media", side_effect=[studio.StudioError("try existing output", "OUTPUT_PENDING"), media]), \
             mock.patch.object(self.a.store, "import_asset", side_effect=self.fake_import):
            studio.submit_audio_job(job["id"])
            failed_download = studio.find_job(job["id"])
            self.assertEqual(failed_download["vendorVoiceId"], response["voice_id"])
            completed = studio.refresh_job(job["id"])
            self.assertEqual(completed["status"], "succeeded")
            self.assertTrue(completed["projectAttached"])
            self.assertEqual(paid.call_count, 1)

    def test_scheduled_audio_result_stays_in_captured_workspace_after_activation(self):
        self.asset("voice", role="voice_identity", vendorVoiceId="ttv-actual")
        job, _ = studio.prepare_audio_job(self.speech_body(), "speech")
        studio.schedule(job["id"], lambda jid: studio.update_job(jid, status="captured-workspace-marker"))
        runner = self.pool.submit.call_args.args[0]
        self.registry.activate(self.b.id)
        with scope(self.b):
            runner()
            self.assertEqual(studio.read_json(studio.JOBS), [])
        self.assertEqual(studio.find_job(job["id"])["status"], "captured-workspace-marker")
        self.assertEqual(studio.INFLIGHT, set())

    def test_verified_multimodal_paths_reach_gateway_as_json_without_blind_retry(self):
        image = self.asset("image-ref", "image")
        audio = self.asset("audio-ref", "audio")
        video = self.asset("motion-ref", "video")
        body = {"confirmed": True, "requestId": "multimodal-submit", "kind": "video", "shotId": "S001", "modelId": VIDEO["id"],
                "prompt": "Use image and sound", "params": {"duration": "5"}, "imagePaths": [image["path"]],
                "audioPaths": [audio["path"]], "videoPaths": [video["path"]]}
        with mock.patch.object(studio, "probe_media", return_value={"duration": 3, "fps": 30}):
            job, _ = studio.prepare_job(body)
        self.gw.submit_error = GatewayError("SUBMISSION_STATUS_UNKNOWN", "lost response", submission_uncertain=True)
        studio.submit_job(job["id"])
        studio.submit_job(job["id"])
        self.assertEqual(len(self.gw.submissions), 1)
        args = self.gw.submissions[0]
        self.assertEqual(args[4], [image["path"]])
        self.assertEqual(json.loads(args[3]["reference_audios"]), [audio["path"]])
        self.assertEqual(json.loads(args[3]["reference_videos"]), [video["path"]])
        self.assertEqual(studio.find_job(job["id"])["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
