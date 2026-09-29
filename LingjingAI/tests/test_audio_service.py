"""Pure builders and fake-transport tests. Never generate real voices or speech."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_service import (SPEECH_ROLE, VOICE_DESIGN_ROLE, build_speech_body,
                           build_voice_design_body, design_voice, submit_speech)
from gateway import GatewayError


MODEL = {"id": "speech-2.8-hd", "backend": "minimax_tts", "promptMaxLength": 10000,
         "params": {"voice_id": {"options": ["Friendly_Person"], "default": "Friendly_Person"},
                    "speed": {"type": "slider", "min": 0.5, "max": 2, "default": "1"}}}


class FakeGateway:
    def __init__(self, response=None, *, session_id=None):
        self.session_id = session_id
        self.workspace_headers = {"x-workspace-id": "captured-workspace"}
        self.calls = []
        self.scope_calls = 0
        self.response = response if response is not None else {"task_id": "existing-upstream-task"}
        self.post_error = self.wallet_error = None

    def _billing_headers(self):
        self.scope_calls += 1
        headers = {"x-group-id": "normal-billing-group"}
        if self.session_id:
            headers.update({"x-session-id": self.session_id, "x-chat-turn-id": "normal-turn"})
        return headers

    def _request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(kwargs)))
        if method == "GET":
            if self.wallet_error:
                raise self.wallet_error
            return {"balance": 100}
        if self.post_error:
            raise self.post_error
        return copy.deepcopy(self.response)


class AudioServiceTest(unittest.TestCase):
    def test_seed_reference_transport_does_not_emulate_voice_id(self):
        from audio_service import build_reference_speech_body,submit_reference_speech
        model={'id':'seed-audio-1.0','backend':'seedaudio'}
        body=build_reference_speech_body(model,'@音频1 为音色，台词：走吧。',{'speed':.95},['C:/project/voice.wav'],[],'seed-test')
        self.assertEqual(body['audio_paths'],['C:/project/voice.wav'])
        self.assertNotIn('voice_id',body['params'])
        gw=FakeGateway();submit_reference_speech(gw,body)
        posts=[c for c in gw.calls if c[0]=='POST'];self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][1],'/api/generate/speech/submit')
    def test_seed_rejects_mixed_references_and_foreign_params(self):
        from audio_service import build_reference_speech_body
        for params,audio,images in [({},['a'],['b']),({'voice_id':'bad'},[],[]),({},['a','b','c','d'],[])]:
            with self.assertRaises(GatewayError):build_reference_speech_body({'id':'seed-audio-1.0','backend':'seedaudio'},'台词',params,audio,images,'seed-test')
    def test_clone_uses_normal_billing_and_exact_local_contract_once(self):
        from audio_service import clone_voice
        gw=FakeGateway({'voice_id':'real-id','demo_audio':'preview.mp3'})
        clone_voice(gw,'C:/project/reference.wav','试听台词')
        posts=[c for c in gw.calls if c[0]=='POST'];self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][1],'/api/speech/voice_clone')
        self.assertEqual(posts[0][2]['audio_path'],'C:/project/reference.wav')
        self.assertNotIn('voice_id',posts[0][2])
    def body(self, **changes):
        args = {"model_catalog_entry": MODEL, "text": "这句台词必须保持原文。",
                "vendor_voice_id": "ttv-generated-character-voice", "params": {}, "filename": "dialogue-001"}
        args.update(changes)
        return build_speech_body(**args)

    def test_official_body_uses_spoken_prompt_and_voice_id_inside_params(self):
        text = "  第一句。\n第二句！  "
        body = self.body(text=text, params={"speed": 0.75, "emotion": "sad", "language_boost": "Chinese"})
        self.assertEqual(body["backend"], "minimax_tts")
        self.assertEqual(body["model_id"], "speech-2.8-hd")
        self.assertEqual(body["prompt"], text)
        self.assertEqual(body["params"], {"model_name": "speech-2.8-hd", "voice_id": "ttv-generated-character-voice",
                                          "speed": "0.75", "emotion": "sad", "language_boost": "Chinese"})
        self.assertNotIn("voice_id", body)
        self.assertNotIn("voice_setting", body)
        self.assertEqual(body["source_tool"], "codex_studio:generate_audio_speech")

    def test_custom_voice_id_is_not_replaced_by_catalog_default(self):
        body = self.body(vendor_voice_id="hub_some-new-character")
        self.assertEqual(body["params"]["voice_id"], "hub_some-new-character")
        self.assertEqual(body["params"]["speed"], "1")
        self.assertEqual(body["params"]["language_boost"], "auto")

    def test_different_speech_contracts_and_unknown_model_are_rejected(self):
        for mid, backend in (("seed-audio-1.0", "seedaudio"), ("MiniMax-H3 Audio", "minimax_v3"),
                             ("future-speech", "minimax_tts"), ("speech-2.8-hd", "other")):
            with self.subTest(mid=mid), self.assertRaises(GatewayError):
                self.body(model_catalog_entry={"id": mid, "backend": backend})

    def test_documented_turbo_is_pinned_if_caller_provides_live_entry(self):
        body = self.body(model_catalog_entry={"id": "speech-2.8-turbo", "backend": "minimax_tts"})
        self.assertEqual(body["model_id"], "speech-2.8-turbo")
        self.assertEqual(body["params"]["model_name"], "speech-2.8-turbo")

    def test_empty_or_oversized_dialogue_is_not_a_valid_job(self):
        for text in (None, "", "   ", ["line one", "line two"], "A" * 10001, "bad\x00text"):
            with self.subTest(text=repr(text)[:35]), self.assertRaises(GatewayError):
                self.body(text=text)
        with self.assertRaises(GatewayError):
            self.body(text="four", model_catalog_entry={**MODEL, "promptMaxLength": 3})

    def test_voice_id_cannot_be_missing_or_an_audio_asset_path(self):
        for voice in (None, "", " Friendly_Person", "C:/preview/voice.mp3", "https://host/voice", {"id": "voice-asset"}):
            with self.subTest(voice=voice), self.assertRaises(GatewayError):
                self.body(vendor_voice_id=voice)

    def test_no_identity_override_raw_audio_or_billing_fields_in_params(self):
        for key in ("voice_id", "model_name", "audio_path", "reference_audios", "image_paths",
                    "x-hilo-source", "billing", "role", "voice_id_source", "volume"):
            with self.subTest(key=key), self.assertRaises(GatewayError):
                self.body(params={key: "unexpected"})

    def test_speed_volume_and_pitch_bounds_are_validated_locally(self):
        for params in ({"speed": 0.49}, {"speed": 2.01}, {"speed": True}, {"speed": "NaN"},
                       {"speed": float("inf")}, {"vol": 0}, {"vol": 10.01},
                       {"pitch": 12.1}, {"pitch": 1.5}, {"pitch": -13}, {"pitch": None}):
            with self.subTest(params=params), self.assertRaises(GatewayError):
                self.body(params=params)
        self.assertEqual(self.body(params={"vol": "0.1", "pitch": "-12", "speed": "2"})["params"]["pitch"], "-12")

    def test_optional_empty_emotion_is_omitted_and_unknown_emotion_language_rejected(self):
        self.assertNotIn("emotion", self.body(params={"emotion": ""})["params"])
        for params in ({"emotion": "cinematic"}, {"emotion": True}, {"language_boost": "zh-CN"}):
            with self.subTest(params=params), self.assertRaises(GatewayError):
                self.body(params=params)

    def test_verified_nested_parameters_are_json_strings_on_wire(self):
        params = {"pronunciation_dict": {"tone": ["处理/(chu3)(li3)"]},
                  "voice_modify": {"pitch": -10, "intensity": 5, "timbre": 10, "sound_effects": "lofi_telephone"}}
        output = self.body(params=params)["params"]
        self.assertEqual(json.loads(output["pronunciation_dict"]), params["pronunciation_dict"])
        self.assertEqual(json.loads(output["voice_modify"]), params["voice_modify"])
        from_strings = self.body(params={key: json.dumps(value) for key, value in params.items()})["params"]
        self.assertEqual(output, from_strings)

    def test_malformed_nested_parameters_cannot_be_silently_dropped_by_gateway(self):
        for params in ({"pronunciation_dict": "{broken"}, {"pronunciation_dict": []},
                       {"pronunciation_dict": {"tone": "not an array"}},
                       {"pronunciation_dict": {"tone": ["no replacement"]}},
                       {"voice_modify": {"pitch": 101}}, {"voice_modify": {"timbre": float("nan")}},
                       {"voice_modify": {"unknown": 1}}, {"voice_modify": {"sound_effects": "invented"}}):
            with self.subTest(params=params), self.assertRaises(GatewayError):
                self.body(params=params)

    def test_filenames_are_controlled_basenames_with_real_extension_left_to_gateway(self):
        self.assertEqual(self.body(filename="dialogue-001.wav")["filename"], "dialogue-001")
        for filename in ("../line", "C:/line", "line\n1", "line.exe", "CON", "a" * 61, ".hidden", ""):
            with self.subTest(filename=filename), self.assertRaises(GatewayError):
                self.body(filename=filename)

    def test_design_body_marks_preview_separate_from_actual_dialogue(self):
        self.assertEqual(VOICE_DESIGN_ROLE, "voice_identity")
        self.assertEqual(SPEECH_ROLE, "dialogue_performance")
        self.assertEqual(build_voice_design_body("Older gentle storyteller", "试听原文。"),
                         {"prompt": "Older gentle storyteller", "preview_text": "试听原文。"})
        for description, preview in (("", "preview"), ("voice", ""), ("voice", "A" * 501)):
            with self.subTest(description=description, preview=preview[:10]), self.assertRaises(GatewayError):
                build_voice_design_body(description, preview)

    def test_builders_preserve_inputs_and_are_deterministic(self):
        entry = copy.deepcopy(MODEL)
        params = {"voice_modify": {"pitch": 2}, "pronunciation_dict": {"tone": ["A/B"]}}
        snapshot = copy.deepcopy((entry, params))
        one = self.body(model_catalog_entry=entry, params=params)
        two = self.body(model_catalog_entry=entry, params=params)
        self.assertEqual(one, two)
        self.assertEqual((entry, params), snapshot)

    def test_design_normal_scope_wallet_and_single_post_preserve_raw_local_path(self):
        response = {"voice_id": "ttv-designed", "trial_audio_url": "audio/voice-design.mp3", "extra": "keep"}
        gw = FakeGateway(response, session_id="verified-session")
        result = design_voice(gw, "A clear narrator", "Preview line")
        self.assertEqual(result, response)
        self.assertEqual(gw.scope_calls, 1)
        self.assertEqual([(method, path) for method, path, _, _ in gw.calls],
                         [("GET", "/api/v1/credit/wallet"), ("POST", "/api/speech/voice_design")])
        for _, _, _, kwargs in gw.calls:
            self.assertEqual(kwargs["headers"], {"x-group-id": "normal-billing-group",
                                                 "x-session-id": "verified-session", "x-chat-turn-id": "normal-turn"})
            self.assertNotIn("x-hilo-source", kwargs["headers"])
        self.assertTrue(gw.calls[1][3]["submission"])
        self.assertEqual(gw.workspace_headers, {"x-workspace-id": "captured-workspace"})

    def test_speech_submits_exactly_one_task_without_polling_or_resubmitting(self):
        gw = FakeGateway({"taskId": "speech-task", "status": "pending"})
        result = submit_speech(gw, MODEL, "Exact line", "ttv-designed", {"emotion": "calm"}, "line-001")
        self.assertEqual(result, {"taskId": "speech-task", "status": "pending"})
        self.assertEqual(gw.scope_calls, 1)
        self.assertEqual(len(gw.calls), 2)
        self.assertEqual(gw.calls[1][1], "/api/generate/speech/submit")
        self.assertEqual(gw.calls[1][2]["params"]["voice_id"], "ttv-designed")
        self.assertTrue(gw.calls[1][3]["submission"])

    def test_invalid_input_does_not_even_read_wallet_or_scope(self):
        gw = FakeGateway()
        with self.assertRaises(GatewayError):
            design_voice(gw, "voice", "A" * 501)
        with self.assertRaises(GatewayError):
            submit_speech(gw, MODEL, "line", "voice", {"speed": 100}, "line")
        self.assertEqual(gw.calls, [])
        self.assertEqual(gw.scope_calls, 0)

    def test_uncertain_billable_failure_is_preserved_without_retry_for_both_methods(self):
        for operation in (lambda gw: design_voice(gw, "voice", "preview"),
                          lambda gw: submit_speech(gw, MODEL, "line", "voice", {}, "line")):
            with self.subTest(operation=operation):
                gw = FakeGateway()
                error = GatewayError("SUBMISSION_STATUS_UNKNOWN", "lost response", submission_uncertain=True)
                gw.post_error = error
                with self.assertRaises(GatewayError) as caught:
                    operation(gw)
                self.assertIs(caught.exception, error)
                self.assertTrue(caught.exception.submission_uncertain)
                self.assertEqual(sum(method == "POST" for method, _, _, _ in gw.calls), 1)

    def test_failed_wallet_check_stops_before_paid_post(self):
        gw = FakeGateway()
        gw.wallet_error = GatewayError("GATEWAY_HTTP_401", "login required", status=401)
        with self.assertRaises(GatewayError):
            submit_speech(gw, MODEL, "line", "voice", {}, "line")
        self.assertEqual([call[0] for call in gw.calls], ["GET"])

    def test_non_object_post_response_is_uncertain_and_never_retried(self):
        gw = FakeGateway(["unexpected"])
        with self.assertRaises(GatewayError) as caught:
            design_voice(gw, "voice", "preview")
        self.assertTrue(caught.exception.submission_uncertain)
        self.assertEqual(sum(call[0] == "POST" for call in gw.calls), 1)


if __name__ == "__main__":
    unittest.main()
