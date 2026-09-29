"""Contract tests for reference compilation; no files, network or paid services."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multimodal import capabilities, compile_references


def model(mid="wan3.0-video", **extra):
    backend = "wan_i2v" if mid.startswith("wan") else "seedance" if mid.startswith("seedance") else "minimax_v3"
    return {"id": mid, "model_name": mid, "backend": backend, "promptMaxLength": 7000, **extra}


def asset(aid, kind="image", duration=3, **extra):
    ext = {"image": "png", "audio": "wav", "video": "mp4"}[kind]
    return {"id": aid, "mediaType": kind, "path": "C:/fixture/" + aid + "." + ext,
            "reviewStatus": "ready", "duration": duration, "fps": 30, "role": "reference", **extra}


def binding(aid, **extra):
    return {"assetId": aid, "usage": "model_reference", "enabled": True, **extra}


class MultimodalTest(unittest.TestCase):
    def compile(self, mid="wan3.0-video", prompt="Use @{face} and @{sound}", bindings=None, assets=None, params=None):
        assets = assets if assets is not None else {"face": asset("face"), "sound": asset("sound", "audio")}
        bindings = bindings if bindings is not None else [binding("face"), binding("sound")]
        return compile_references(model(mid), prompt, bindings, assets, params=params)

    def assert_no_payload(self, result):
        self.assertTrue(result["errors"])
        self.assertEqual(result["imagePaths"], [])
        self.assertEqual(result["audioPaths"], [])
        self.assertEqual(result["videoPaths"], [])
        self.assertEqual(result["paramsPatch"], {})

    def test_verified_exact_models_have_distinct_capabilities(self):
        expected = {"MiniMax-H3": (9, 3, 3), "MiniMax-H3-Max": (9, 3, 3),
                    "wan3.0-video": (10, 5, 5), "wan3.0-video-prime": (10, 5, 5),
                    "seedance2.0": (9, 3, 3), "seedance2.0-fast": (9, 3, 3),
                    "seedance2.0-mini": (9, 3, 3), "seedance2.5": (30, 10, 10)}
        for mid, counts in expected.items():
            with self.subTest(mid=mid):
                caps = capabilities(model(mid))
                self.assertTrue(caps["adapterSupported"])
                self.assertEqual(tuple(caps["mediaLimits"][kind]["maxCount"] for kind in ("image", "audio", "video")), counts)
                self.assertFalse(caps["supportsVoiceIdentity"])

    def test_unknown_models_and_backend_mismatches_are_not_guessed(self):
        for entry in (model("wan9.0"), model("MiniMax-H3-Max-Turbo"),
                      model("wan3.0-video", backend="other"),
                      {"id": "kling-v3-omni-video", "model_name": "kling-v3-omni", "backend": "kling", "max_video_refs": 1}):
            with self.subTest(entry=entry):
                caps = capabilities(entry)
                self.assertFalse(caps["adapterSupported"])
                result = compile_references(entry, "Use @{face}", [binding("face")], {"face": asset("face")})
                self.assert_no_payload(result)

    def test_live_catalog_can_narrow_but_not_expand_verified_limits(self):
        caps = capabilities(model(max_audio_refs=1, max_refs=100,
                                  referenceMediaLimits={"audio": {"maxDurationSec": 10}}))
        self.assertEqual(caps["mediaLimits"]["image"]["maxCount"], 10)
        self.assertEqual(caps["mediaLimits"]["audio"]["maxCount"], 1)
        self.assertEqual(caps["mediaLimits"]["audio"]["maxDurationSec"], 10)

    def test_exact_transported_paths_json_and_plain_native_prompt(self):
        result = self.compile()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["prompt"], "Use 图1 and 音频1")
        self.assertEqual(result["imagePaths"], ["C:/fixture/face.png"])
        self.assertEqual(result["audioPaths"], ["C:/fixture/sound.wav"])
        self.assertEqual(json.loads(result["paramsPatch"]["reference_audios"]), result["audioPaths"])
        self.assertEqual(result["paramsPatch"]["image_mode"], "reference")
        self.assertNotIn("reference_images", result["paramsPatch"])
        self.assertNotIn("audio_path", result["paramsPatch"])

    def test_seedance_pins_selected_model_in_backend_params(self):
        for mid in ("seedance2.0", "seedance2.0-fast", "seedance2.0-mini", "seedance2.5"):
            with self.subTest(mid=mid):
                result = self.compile(mid)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["paramsPatch"]["model_name"], mid)
                self.assertEqual(result["prompt"], "Use 图片1 and 音频1")

    def test_h3_transport_does_not_invent_bracket_at_or_raw_ir_tokens(self):
        for mid in ("MiniMax-H3", "MiniMax-H3-Max"):
            with self.subTest(mid=mid):
                result = self.compile(mid)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["prompt"], "Use 图片1 and 音频1")
        self.assertIsNone(capabilities(model("MiniMax-H3-Max"))["audioOutput"]["parameter"])

    def test_order_is_stable_per_media_type_and_not_first_prompt_occurrence(self):
        assets = {"second": asset("second"), "first": asset("first"),
                  "song": asset("song", "audio"), "motion": asset("motion", "video")}
        bindings = [binding("second", order=30), binding("song", order=1),
                    binding("first", order=20), binding("motion", order=10)]
        result = self.compile(prompt="@{second}, @{song}, @{first}, @{motion}, @{second}",
                              bindings=bindings, assets=assets, params={"duration": "5"})
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["prompt"], "图2, 音频1, 图1, 视频1, 图2")
        self.assertEqual(result["imagePaths"], [assets["first"]["path"], assets["second"]["path"]])
        self.assertEqual(json.loads(result["paramsPatch"]["reference_videos"]), [assets["motion"]["path"]])

    def test_unique_connected_alias_is_readable_but_unconnected_alias_is_rejected(self):
        result = self.compile(prompt="@{named-face}", bindings=[binding("face", alias="named-face")])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["prompt"], "图1")
        self.assert_no_payload(self.compile(prompt="@{unconnected-name}", bindings=[binding("face", alias="named-face")]))

    def test_ambiguous_connected_alias_cannot_select_a_random_asset(self):
        result = self.compile(prompt="@{same-name}", bindings=[binding("face", alias="same-name"), binding("other", alias="same-name")],
            assets={"face": asset("face"), "other": asset("other")})
        self.assert_no_payload(result)

    def test_disabled_missing_and_malformed_mentions_fail_closed(self):
        for text, bindings in (("@{unknown}", [binding("face")]),
                               ("@{face}", [binding("face", enabled=False)]),
                               ("@{face", [binding("face")]),
                               ("@{}", [binding("face")])):
            with self.subTest(text=text, bindings=bindings):
                self.assert_no_payload(self.compile(prompt=text, bindings=bindings))

    def test_post_mix_context_and_voice_identity_are_never_transmitted(self):
        assets = {"face": asset("face"), "voice": asset("voice", "audio"),
                  "score": asset("score", "audio"), "notes": {"mediaType": "text"}}
        bindings = [binding("face"), binding("voice", usage="voice_identity"),
                    binding("score", usage="post_mix"), binding("notes", usage="context")]
        result = self.compile(prompt="@{face} turns around", bindings=bindings, assets=assets)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["audioPaths"], [])
        self.assertNotIn("reference_audios", result["paramsPatch"])
        self.assertEqual(len(result["referenceBindings"]), 4)
        self.assertEqual([r["included"] for r in result["referenceBindings"]], [True, False, False, False])
        self.assert_no_payload(self.compile(prompt="@{voice}", bindings=bindings, assets=assets))

    def test_voice_identity_asset_cannot_be_relabelled_as_normal_audio_reference(self):
        result = self.compile(assets={"face": asset("face"), "sound": asset("sound", "audio", role="voice_identity")},
                              bindings=[binding("face"), binding("sound", role="reference")])
        self.assert_no_payload(result)

    def test_unreviewed_asset_and_mistyped_media_path_are_blocked(self):
        for changes in ({"reviewStatus": "candidate"}, {"reviewStatus": "blocked"},
                        {"path": "relative.wav"}, {"path": "https://example.com/a.wav"},
                        {"path": "C:/fixture/not-an-audio.png"}, {"path": "C:/fixture/sound.m4a"}):
            with self.subTest(changes=changes):
                assets = {"face": asset("face"), "sound": asset("sound", "audio", **changes)}
                self.assert_no_payload(self.compile(assets=assets))

    def test_audio_and_video_duration_must_be_measured_finite_in_range(self):
        for duration in (None, "3", 0, -1, True, float("nan"), float("inf"), 0.9, 16):
            with self.subTest(duration=duration):
                self.assert_no_payload(self.compile(assets={"face": asset("face"), "sound": asset("sound", "audio", duration)}))

    def test_h3_minimum_reference_duration_is_two_seconds(self):
        assets = {"face": asset("face"), "sound": asset("sound", "audio", 1.5)}
        self.assert_no_payload(self.compile("MiniMax-H3", assets=assets))
        self.assertEqual(self.compile("wan3.0-video", assets=assets)["errors"], [])

    def test_count_and_per_kind_total_limits_are_enforced_before_payloads(self):
        audios = {"sound" + str(i): asset("sound" + str(i), "audio", 4) for i in range(4)}
        assets = {"face": asset("face"), **audios}
        bindings = [binding(aid) for aid in assets]
        self.assert_no_payload(self.compile("wan3.0-video", prompt="A scene", assets=assets, bindings=bindings))
        for row in audios.values():
            row["duration"] = 2
        self.assert_no_payload(self.compile("MiniMax-H3", prompt="A scene", assets=assets, bindings=bindings))
        self.assertEqual(self.compile("seedance2.5", prompt="A scene", assets=assets, bindings=bindings)["errors"], [])

    def test_h3_video_plus_audio_share_three_slots(self):
        assets = {"face": asset("face"), "a1": asset("a1", "audio"), "a2": asset("a2", "audio"),
                  "v1": asset("v1", "video"), "v2": asset("v2", "video")}
        result = self.compile("MiniMax-H3", prompt="A scene", assets=assets,
                              bindings=[binding(aid) for aid in assets])
        self.assert_no_payload(result)
        self.assertTrue(any("合计" in error for error in result["errors"]))

    def test_audio_only_rules_are_not_assumed_uniform(self):
        assets, bindings = {"sound": asset("sound", "audio")}, [binding("sound")]
        for mid in ("MiniMax-H3-Max", "seedance2.0"):
            with self.subTest(mid=mid):
                self.assert_no_payload(self.compile(mid, "@{sound}", assets=assets, bindings=bindings))
        for mid in ("MiniMax-H3", "wan3.0-video", "seedance2.5"):
            with self.subTest(mid=mid):
                self.assertEqual(self.compile(mid, "@{sound}", assets=assets, bindings=bindings)["errors"], [])

    def test_reference_mode_cannot_silently_override_explicit_first_frame_mode(self):
        result = self.compile(params={"image_mode": "first-last-frame"})
        self.assert_no_payload(result)

    def test_wan_reference_video_plus_output_duration_budget(self):
        assets, bindings = {"motion": asset("motion", "video", 15)}, [binding("motion")]
        for params in ({}, {"duration": "NaN"}, {"duration": "16"}):
            with self.subTest(params=params):
                self.assert_no_payload(self.compile(prompt="@{motion}", assets=assets, bindings=bindings, params=params))
        self.assertEqual(self.compile(prompt="@{motion}", assets=assets, bindings=bindings,
                                      params={"duration": "15"})["errors"], [])

    def test_silent_output_is_explicit_warning_not_forced_audio_or_dropped_reference(self):
        result = self.compile(params={"generate_audio": False})
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["warnings"])
        self.assertEqual(len(result["audioPaths"]), 1)
        self.assertNotIn("generate_audio", result["paramsPatch"])

    def test_repeated_asset_path_reuses_slot_but_conflicting_duplicate_binding_errors(self):
        assets = {"face": asset("face"), "same-face": asset("face")}
        result = self.compile(prompt="@{face} @{same-face}", bindings=[binding("face"), binding("same-face")], assets=assets)
        self.assertEqual(result["prompt"], "图1 图1")
        self.assertEqual(len(result["imagePaths"]), 1)
        self.assert_no_payload(self.compile(prompt="@{face}", bindings=[binding("face"), binding("face")], assets=assets))

    def test_inputs_and_returned_capabilities_do_not_mutate_shared_objects(self):
        entry, assets = model("seedance2.5"), {"face": asset("face"), "sound": asset("sound", "audio")}
        bindings, params = [binding("face"), binding("sound")], {"duration": "5"}
        before = copy.deepcopy((entry, assets, bindings, params))
        first = compile_references(entry, "@{face} @{sound}", bindings, assets, params=params)
        second = compile_references(entry, "@{face} @{sound}", bindings, assets, params=params)
        self.assertEqual(first, second)
        first["capabilities"]["allowedAudioRoles"].clear()
        self.assertTrue(second["capabilities"]["allowedAudioRoles"])
        self.assertEqual((entry, assets, bindings, params), before)


if __name__ == "__main__":
    unittest.main()
