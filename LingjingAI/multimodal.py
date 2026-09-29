"""Pure reference compiler for the inspected MiniMax Design 3.0.18.74 adapter.

No network, filesystem probing, uploads, generation, or input mutation occurs here.
The caller must check file existence and probe actual media before submission.
``params`` is the selected node's parameters, not catalog parameter definitions.

Verified contracts (installed resources/*/dist/main.js):
* mcp-tools buildMiniMaxH3Body/buildMiniMaxH3MaxBody/buildWan3Body/buildVideoBody;
* gateway compileRefTokensInPrompt, Hailuo03Service.submitVideoTask,
  SeedanceService.submitTask, WanI2VService.submitWan3Task.
* Gateway reference tokens are plain 图片N/视频N/音频N, numbered independently.
  Wan's bundled MCP manifest explicitly documents 图N/视频N/音频N.
  Raw/open-weight H3 IR uses <Picture N>/<Video N>/<Audio N>; that is a
  different prompt layer, not this desktop plain-prompt transport.
* params.reference_audios/reference_videos are JSON-encoded string arrays.
  Seedance also requires params.model_name; otherwise it selects its default.

Primary documentation cross-checks:
https://developer.volcengine.com/articles/7606009619928449070
https://docs.volcengine.com/docs/ark/seedance-2-5-prompt-guide?lang=zh
https://www.alibabacloud.com/help/zh/model-studio/wan3-video-generation-guide
https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md

Kling has separate video_list_json/avatar audio_path contracts. Its native
positional prompt syntax was not verified, so this adapter does not enable it.
Model reference capability does not imply a verified voice cloning contract.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re


_PROFILES = {
    "MiniMax-H3": ("minimax_v3", "h3"),
    "MiniMax-H3-Max": ("minimax_v3", "h3-max"),
    "wan3.0-video": ("wan_i2v", "wan3"),
    "wan3.0-video-prime": ("wan_i2v", "wan3"),
    "seedance2.0": ("seedance", "seedance2"),
    "seedance2.0-fast": ("seedance", "seedance2"),
    "seedance2.0-mini": ("seedance", "seedance2"),
    "seedance2.5": ("seedance", "seedance25"),
}
_KINDS = ("image", "audio", "video")
_MENTION = re.compile(r"@\{([^{}\r\n]+)\}")
_USAGES = {"model_reference", "voice_identity", "post_mix", "context"}
_VOICE_ROLES = {"voice_identity", "voice_sample", "voice_clone", "voice_cloning"}
_AUDIO_ROLES = ["reference", "dialogue", "dialogue_performance", "lip_sync_driver", "narration", "music", "ambience", "sfx", "rhythm"]
_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".mp3", ".wav"},  # Portable subset also accepted by H3/Seedance.
    "video": {".mp4", ".mov", ".webm"},
}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value):
    return _finite(value) and value > 0


def capabilities(model_catalog_entry):
    """Return verified *adapter* capabilities; catalog limits may narrow them.

Unsupported adapters still expose catalogCapabilities for honest inspection;
supportsImage/Audio/Video always describe what this compiler can safely emit.
Audio roles label creative intent, not provider API enum values. Voice identity
samples are deliberately a separate, non-transmitted usage in this adapter.
"""
    model = model_catalog_entry if isinstance(model_catalog_entry, dict) else {}
    mid = model.get("model_name") or model.get("id") or ""
    profile = _PROFILES.get(mid)
    supported = bool(profile and model.get("backend") == profile[0])
    result = {
        "modelId": model.get("id", mid), "modelName": mid,
        "adapterSupported": supported,
        "supportsImage": False, "supportsAudio": False, "supportsVideo": False,
        "supportsVoiceIdentity": False, "allowedAudioRoles": [],
        "mediaLimits": {}, "mentionSyntax": {}, "referenceMode": "reference",
        "parameterFields": {}, "audioOutput": {},
        "catalogCapabilities": {"maxImages": model.get("max_refs", 0),
                                "maxAudios": model.get("max_audio_refs", 0),
                                "maxVideos": model.get("max_video_refs", 0)},
        "reason": None if supported else "该模型的多模态引用适配尚未验证；不能自动发送引用。",
        "evidence": "MiniMax Design 3.0.18.74 installed gateway + MCP source; live catalog may narrow limits",
    }
    if not supported:
        return result
    family = profile[1]
    counts = (10, 5, 5) if family == "wan3" else (30, 10, 10) if family == "seedance25" else (9, 3, 3)
    low, high = (1, 15) if family == "wan3" else (2, 30) if family == "seedance25" else (2, 15)
    limits = {"image": {"maxCount": counts[0]},
              "audio": {"maxCount": counts[1], "minDurationSec": low,
                        "maxDurationSec": high, "totalMaxDurationSec": high,
                        "allowStandalone": family in {"h3", "wan3", "seedance25"}},
              "video": {"maxCount": counts[2], "minDurationSec": low,
                        "maxDurationSec": high, "totalMaxDurationSec": high}}
    if family in {"h3", "h3-max"}:
        limits["maxVideoAudioCount"] = 3
        limits["maxTotalCount"] = 12
        limits["video"].update(minFps=23.5, maxFps=60.5)
    elif family == "seedance2":
        limits["maxTotalCount"] = 12
    elif family == "wan3":
        limits["video"]["combinedWithOutputMaxDurationSec"] = 30
    for kind, field in (("image", "max_refs"), ("audio", "max_audio_refs"), ("video", "max_video_refs")):
        count = model.get(field)
        if _finite(count) and count >= 0:
            limits[kind]["maxCount"] = min(limits[kind]["maxCount"], int(count))
        extra = model.get("referenceMediaLimits", {}).get(kind, {})
        for key in ("maxDurationSec", "totalMaxDurationSec", "combinedWithOutputMaxDurationSec"):
            if _positive(extra.get(key)):
                limits[kind][key] = min(limits[kind].get(key, extra[key]), extra[key])
        if _positive(extra.get("minDurationSec")):
            limits[kind]["minDurationSec"] = max(limits[kind].get("minDurationSec", 0), extra["minDurationSec"])
        if extra.get("allowStandalone") is False:
            limits[kind]["allowStandalone"] = False
        limits[kind]["extensions"] = sorted(_EXTENSIONS[kind])
    combo = model.get("max_video_audio_refs")
    if _finite(combo) and combo >= 0:
        limits["maxVideoAudioCount"] = min(limits.get("maxVideoAudioCount", int(combo)), int(combo))
    result.update({"supportsImage": limits["image"]["maxCount"] > 0,
                   "supportsAudio": limits["audio"]["maxCount"] > 0,
                   "supportsVideo": limits["video"]["maxCount"] > 0,
                   "allowedAudioRoles": list(_AUDIO_ROLES), "mediaLimits": limits,
                   "mentionSyntax": {"image": "图{index}" if family == "wan3" else "图片{index}",
                                     "video": "视频{index}", "audio": "音频{index}"},
                   "parameterFields": {"image": "image_paths", "audio": "params.reference_audios",
                                       "video": "params.reference_videos", "arrayEncoding": "JSON string"},
                   "audioOutput": {"parameter": None if family == "h3-max" else "generate_audio",
                                   "onValue": "true", "offValue": "false",
                                   "note": "H3 Max does not expose generate_audio" if family == "h3-max" else "Controls generated output audio, not voice identity"}})
    return result


def compile_references(model, prompt, bindings, assets, *, params=None):
    """Compile connected @{assetId} mentions and exact ordered media payloads.

Bindings sort by explicit numeric order (otherwise list position), stably. Each
media kind has its own 1-based numbering. Repeated mentions reuse one slot;
different asset IDs for the same path also share a slot. Aliases are display
labels only, never interpreted as IDs or automatically inserted into the prompt.
On any error all payload path arrays and paramsPatch are empty (fail closed).
referenceBindings retains a reviewable record including excluded usages.
"""
    model = model if isinstance(model, dict) else {}
    caps = capabilities(model)
    result = {"prompt": prompt if isinstance(prompt, str) else "", "imagePaths": [],
              "audioPaths": [], "videoPaths": [], "paramsPatch": {},
              "referenceBindings": [], "errors": [], "warnings": [], "capabilities": caps}
    errors, warnings = result["errors"], result["warnings"]
    if not isinstance(prompt, str):
        errors.append("prompt 必须是文字。")
        return result
    if not isinstance(bindings, list) or not isinstance(assets, dict):
        errors.append("bindings 必须是数组，assets 必须按资产 ID 索引。")
        return result
    if params is not None and not isinstance(params, dict):
        errors.append("params 必须是对象。")
        return result
    params = copy.deepcopy(params or {})
    ordered = []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            errors.append("引用绑定必须是对象。")
            continue
        order = binding.get("order", index)
        if not _finite(order):
            errors.append("引用 order 必须是有限数字。")
            continue
        ordered.append((order, index, binding))
    paths = {kind: [] for kind in _KINDS}
    durations = {"audio": [], "video": []}
    tokens, seen_ids = {}, set()
    for _, _, binding in sorted(ordered, key=lambda item: item[:2]):
        row = copy.deepcopy(binding)
        row.update(included=False, nativeToken=None, index=None)
        result["referenceBindings"].append(row)
        aid = binding.get("assetId")
        if not isinstance(aid, str) or not aid:
            errors.append("引用缺少 assetId。")
            continue
        if binding.get("enabled", True) is not True:
            row["excludedReason"] = "disabled"
            continue
        usage = binding.get("usage", "model_reference")
        row["usage"] = usage
        if usage not in _USAGES:
            errors.append(f"{aid}: 不支持的引用 usage={usage}。")
            continue
        if usage != "model_reference":
            row["excludedReason"] = usage
            if usage == "voice_identity":
                warnings.append(f"{aid}: 仅保留为音色身份资料；此适配器未将其作为模型音频引用发送。")
            elif usage == "post_mix":
                warnings.append(f"{aid}: 保留给后期混音；不会作为视频生成模型的参考音频发送。")
            continue
        if aid in seen_ids:
            errors.append(f"{aid}: 同一资产有重复模型引用绑定，请合并后再提交。")
            continue
        seen_ids.add(aid)
        asset = assets.get(aid)
        if not isinstance(asset, dict):
            errors.append(f"{aid}: 找不到已登记资产。")
            continue
        kind = asset.get("mediaType")
        row["mediaType"] = kind
        role = binding.get("role") or asset.get("role") or "reference"
        row["role"] = role
        if not isinstance(role, str):
            errors.append(f"{aid}: role 必须是文字。")
            continue
        if role in _VOICE_ROLES or str(asset.get("role")) in _VOICE_ROLES:
            errors.append(f"{aid}: 音色身份样本不等于当前镜头音轨；此适配器未验证 voice_identity，不能作为模型参考发送。")
            continue
        if not caps["adapterSupported"]:
            errors.append(f"{aid}: {caps['reason']}")
            continue
        if kind not in _KINDS or not caps.get("supports" + str(kind).capitalize()):
            errors.append(f"{aid}: 当前模型不支持 {kind} 引用。")
            continue
        if asset.get("reviewStatus") != "ready":
            errors.append(f"{aid}: 资产尚未审核为 ready。")
            continue
        path = asset.get("path")
        if (not isinstance(path, str) or not path or "\x00" in path
                or not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
                or path.lower().startswith(("http:", "https:", "file:"))):
            errors.append(f"{aid}: 需要本地绝对路径。")
            continue
        if PureWindowsPath(path).suffix.lower() not in _EXTENSIONS[kind]:
            errors.append(f"{aid}: {kind} 文件格式尚未适配；允许 {', '.join(sorted(_EXTENSIONS[kind]))}。")
            continue
        duration = asset.get("duration", asset.get("durationSec"))
        if kind in durations:
            if not _positive(duration):
                errors.append(f"{aid}: 缺少可验证的真实媒体时长，请先探测文件。")
                continue
            limit = caps["mediaLimits"][kind]
            if not limit["minDurationSec"] <= duration <= limit["maxDurationSec"]:
                errors.append(f"{aid}: {kind} 时长必须在 {limit['minDurationSec']}–{limit['maxDurationSec']} 秒。")
            fps = asset.get("fps")
            if kind == "video" and "minFps" in limit:
                if fps is not None and (not _positive(fps) or not limit["minFps"] <= fps <= limit["maxFps"]):
                    errors.append(f"{aid}: 参考视频帧率必须为 {limit['minFps']}–{limit['maxFps']} fps。")
                elif fps is None:
                    warnings.append(f"{aid}: 提交前需验证视频帧率 {limit['minFps']}–{limit['maxFps']} fps。")
        if path not in paths[kind]:
            paths[kind].append(path)
            if kind in durations:
                durations[kind].append(duration)
        slot = paths[kind].index(path) + 1
        token = caps["mentionSyntax"][kind].format(index=slot)
        tokens[aid] = token
        row.update(included=True, index=slot, nativeToken=token, path=path)

    alias_tokens = {}
    for row in result["referenceBindings"]:
        if row.get("included") and row.get("alias"):
            alias_tokens.setdefault(str(row["alias"]), set()).add(row["nativeToken"])

    def replace(match):
        aid = match.group(1)
        if aid not in tokens and aid in alias_tokens:
            if len(alias_tokens[aid]) == 1:
                return next(iter(alias_tokens[aid]))
            errors.append(f"@{{{aid}}}: 素材别名重复，请从菜单选择明确的资源。")
            return match.group(0)
        if aid not in tokens:
            errors.append(f"@{{{aid}}}: 提示词引用必须连接到启用且可发送的真实模型资产。")
            return match.group(0)
        return tokens[aid]

    compiled_prompt = _MENTION.sub(replace, prompt)
    if "@{" in _MENTION.sub("", prompt):
        errors.append("提示词有未闭合或格式错误的 @{assetId} 引用。")
    active_count = sum(len(items) for items in paths.values())
    if active_count:
        limits = caps["mediaLimits"]
        for kind in _KINDS:
            if len(paths[kind]) > limits[kind]["maxCount"]:
                errors.append(f"{kind} 引用超过模型上限 {limits[kind]['maxCount']}。")
        for kind, values in durations.items():
            if sum(values) > limits[kind]["totalMaxDurationSec"]:
                errors.append(f"{kind} 参考总时长超过 {limits[kind]['totalMaxDurationSec']} 秒。")
        if active_count > limits.get("maxTotalCount", float("inf")):
            errors.append(f"参考文件总数超过 {limits['maxTotalCount']}。")
        if len(paths["audio"]) + len(paths["video"]) > limits.get("maxVideoAudioCount", float("inf")):
            errors.append(f"视频与音频引用合计不能超过 {limits['maxVideoAudioCount']} 个。")
        if paths["audio"] and not paths["image"] and not paths["video"] and not limits["audio"]["allowStandalone"]:
            errors.append("当前适配不允许仅使用音频参考；请连接图片或视频。")
        mode = params.get("image_mode", "reference")
        if mode != "reference":
            errors.append("模型参考需要 image_mode=reference，不能与首尾帧或其他模式混用。")
        combined = limits["video"].get("combinedWithOutputMaxDurationSec")
        if paths["video"] and combined:
            try:
                duration = float(params.get("duration", ""))
            except (ValueError, TypeError):
                duration = float("nan")
            if not math.isfinite(duration) or duration <= 0:
                errors.append("含视频引用时必须明确设置输出 duration。")
            elif sum(durations["video"]) + duration > combined:
                errors.append(f"参考视频总时长与输出时长之和不能超过 {combined} 秒。")
        if paths["audio"] and str(params.get("generate_audio", "true")).lower() == "false":
            warnings.append("generate_audio=false：参考音频仍会发送，但输出声音被关闭；若需本镜音轨请启用声音。")

    if len(compiled_prompt) > model.get("promptMaxLength", 20000):
        errors.append("展开引用后的提示词超过模型长度限制。")
    errors[:] = list(dict.fromkeys(errors))
    warnings[:] = list(dict.fromkeys(warnings))
    if not errors:
        result["prompt"] = compiled_prompt
        for kind in _KINDS:
            result[kind + "Paths"] = paths[kind]
        patch = result["paramsPatch"]
        if active_count:
            patch["image_mode"] = "reference"
        if model.get("backend") == "seedance" and caps["adapterSupported"]:
            patch["model_name"] = model.get("model_name") or model["id"]
        for kind, field in (("audio", "reference_audios"), ("video", "reference_videos")):
            if paths[kind]:
                patch[field] = json.dumps(paths[kind], ensure_ascii=False)
    return result
