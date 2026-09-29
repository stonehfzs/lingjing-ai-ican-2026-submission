"""Script blocks, per-shot review and honest picture/sound readiness."""
import copy
import hashlib
import re


def script_document(project):
    text = project.get("script", "")
    shots = project.get("shots", [])
    shot_map = {s["id"]: s for s in shots}
    known = sorted(shot_map, key=len, reverse=True)
    shot_pattern = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(s) for s in known) + r")(?![A-Za-z0-9_-])") if known else None
    blocks, occurrences, current_shot = [], {}, None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        start = i
        paragraph = [lines[i]]
        i += 1
        if not paragraph[0].lstrip().startswith("#"):
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith("#"):
                paragraph.append(lines[i])
                i += 1
        value = "\n".join(paragraph)
        heading = value.lstrip().startswith("#")
        if heading:
            match = shot_pattern.search(value) if shot_pattern else None
            current_shot = match.group(1) if match else None
        digest = hashlib.sha1(value.encode()).hexdigest()[:12]
        ordinal = occurrences.get(digest, 0)
        occurrences[digest] = ordinal + 1
        bid = f"block-{digest}-{ordinal}"
        associated = [current_shot] if current_shot else []
        for shot in shots:
            if bid in shot.get("sourceBlockIds", []) and shot["id"] not in associated:
                associated.append(shot["id"])
        kind = "heading" if heading else "audio" if value.startswith("**声音") else "dialogue" if re.match(r"\*\*.+(?:旁白|画外|对白|）).*?\*\*", value) else "paragraph"
        blocks.append({"id": bid, "type": kind, "text": value, "startLine": start + 1, "endLine": i,
            "shotIds": associated, "sceneId": shot_map.get(current_shot, {}).get("sourceData", {}).get("scene_id")})
    scenes = {}
    for shot in shots:
        sid = shot.get("sceneId") or shot.get("sourceData", {}).get("scene_id") or shot.get("scene", "未分场")
        scenes.setdefault(sid, {"id": sid, "name": shot.get("scene", sid), "shotIds": []})["shotIds"].append(shot["id"])
    return {"text": text, "blocks": blocks, "scenes": list(scenes.values()), "revision": project.get("revision", 0), "projectId": project.get("id")}


def shot_audio_plan(shot, node=None):
    data = (node or {}).get("data", {})
    plan = copy.deepcopy(data.get("audioPlan") or shot.get("audioPlan") or {})
    source = shot.get("sourceData", {})
    plan.setdefault("mode", "reuse_audio" if source.get("audio_reuse_from") else "separate_tracks")
    plan.setdefault("lines", source.get("lines", []))
    plan.setdefault("soundEffects", source.get("sound_effects", ""))
    plan.setdefault("generateAudio", False)
    return plan


def shot_readiness(shot, compiled_state, bindings, assets, node=None):
    plan = shot_audio_plan(shot, node)
    mode = plan.get("mode", "separate_tracks")
    lines = plan.get("lines", [])
    cues = plan.get("cues", [])
    performance_roles = {"dialogue_performance", "lip_sync_driver", "narration", "dialogue"}
    performances = [b for b in bindings if b.get("enabled", True) and b.get("role") in performance_roles and b.get("usage") in {"post_mix", "model_reference"}
        and assets.get(b.get("assetId"), {}).get("mediaType") == "audio"
        and assets.get(b.get("assetId"), {}).get("role") in performance_roles
        and assets.get(b.get("assetId"), {}).get("reviewStatus") == "ready"]
    identity = [b for b in bindings if b.get("enabled", True) and b.get("usage") == "voice_identity"]
    native = mode == "native_audio" and plan.get("generateAudio") is True
    review = shot.get("review", {})
    native_reviewed = native and compiled_state.get("status") == "complete" and compiled_state.get("hasAudio") is True and review.get("status") == "approved" and review.get("scope") == "media" and review.get("jobId") == compiled_state.get("jobId")
    declared_ids = plan.get("dialogueCueIds", [])
    expected = {cid: {"id": cid} for cid in declared_ids}
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            line = {"text": str(line)}
        cid = line.get("cueId") or line.get("id") or (declared_ids[index] if index < len(declared_ids) else f"anonymous-line-{index}")
        expected.setdefault(cid, {"id": cid}).update(line)
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict) or cue.get("type") == "voice_identity":
            continue
        cid = cue.get("cueId") or cue.get("id")
        if cid:
            expected.setdefault(cid, {"id": cid}).update(cue)
    needs_speech = bool(expected)
    normalize = lambda value: re.sub(r"\W+", "", str(value or ""))
    matched = set()
    anonymous_used = set()
    for cid, cue in expected.items():
        for binding in performances:
            aid = binding["assetId"]
            asset = assets[aid]
            declared = cid in asset.get("cueIds", []) or binding.get("cueId") == cid
            if not declared and not cid.startswith("anonymous-line-"):
                continue
            wanted_voice = cue.get("voiceId") or cue.get("voice_id") or cue.get("characterId")
            actual_voice = asset.get("voiceId") or asset.get("characterId") or binding.get("voiceId")
            if wanted_voice and actual_voice and wanted_voice != actual_voice:
                continue
            text, spoken = normalize(cue.get("text")), normalize(asset.get("spokenText"))
            if text and spoken and (text not in spoken if len(asset.get("cueIds", [])) > 1 else text != spoken):
                continue
            if cid.startswith("anonymous-line-") and (aid in anonymous_used or not text or text != spoken):
                continue
            matched.add(cid)
            if cid.startswith("anonymous-line-"):
                anonymous_used.add(aid)
            break
    performance_complete = len(matched) == len(expected) or native_reviewed
    audio_ready = (not needs_speech or performance_complete) and not plan.get("missing", [])
    visual_ready = compiled_state.get("status") in {"ready", "complete"}
    missing = list(plan.get("missing", []))
    if needs_speech and not performance_complete:
        missing.append("原生对白待生成与审听" if native else "缺少本镜逐句表演音频；角色试听不等于正式对白")
    for binding in bindings:
        if not binding.get("enabled", True):
            continue
        asset = assets.get(binding.get("assetId"), {})
        if asset.get("mediaType") == "audio" and binding.get("usage") in {"post_mix", "model_reference", "voice_identity"} and asset.get("reviewStatus") != "ready" and not (native_reviewed and binding.get("usage") == "voice_identity"):
            missing.append(("角色音色待选定：" if binding.get("usage") == "voice_identity" else "音轨待审阅：") + str(binding.get("alias") or asset.get("name", binding.get("assetId"))))
    if plan.get("soundEffects") and not native_reviewed and not any(b.get("enabled", True) and b.get("role") in {"ambience", "sfx"} and b.get("usage") == "post_mix"
        and assets.get(b.get("assetId"), {}).get("mediaType") == "audio" and assets.get(b.get("assetId"), {}).get("reviewStatus") == "ready" for b in bindings):
        missing.append("环境声/拟声尚未制作或绑定")
    review = shot.get("review", {})
    return {"visualReady": visual_ready, "audioReady": audio_ready and not missing,
        "deliveryReady": compiled_state.get("status") == "complete" and audio_ready and not missing and review.get("status") == "approved" and review.get("scope") == "media" and review.get("jobId") == compiled_state.get("jobId"),
        "audioMode": mode, "audioMissing": list(dict.fromkeys(missing)), "voiceIdentityCount": len(identity),
        "performanceCount": len(performances), "label": "画面可生成 · 声音待准备" if visual_ready and missing else "画面可生成" if visual_ready else "画面依赖待准备"}


def review_bundle(project, sid, graph, asset_document, compiled=None):
    shot = next((s for s in project.get("shots", []) if s["id"] == sid), None)
    if not shot:
        raise KeyError(sid)
    node = next((n for n in graph.get("nodes", []) if n.get("shotId") == sid and n["type"] in {"video", "reuse", "edit"}), None)
    asset_map = {a["id"]: a for a in asset_document.get("assets", [])}
    bindings = copy.deepcopy((node or {}).get("data", {}).get("bindings", shot.get("bindings", [])))
    if not bindings and node:
        by_id = {n["id"]: n for n in graph["nodes"]}
        for edge in graph["edges"]:
            if edge["target"] == node["id"] and edge["source"] in by_id:
                upstream = by_id[edge["source"]]
                aid = upstream.get("assetId")
                if aid in asset_map:
                    a = asset_map[aid]
                    bindings.append({"assetId": aid, "alias": a.get("name", aid), "role": a.get("role", "reference"), "usage": "model_reference", "enabled": True})
    states = {s["id"]: s for s in (compiled or {}).get("nodes", [])}
    state = states.get((node or {}).get("id"), {})
    document = script_document(project)
    return {"shot": shot, "projectRevision": project.get("revision"), "workflowRevision": graph.get("revision"),
        "scriptBlocks": [b for b in document["blocks"] if sid in b["shotIds"]], "bindings": bindings,
        "assets": [asset_map[b["assetId"]] for b in bindings if b.get("assetId") in asset_map],
        "audioPlan": shot_audio_plan(shot, node), "readiness": shot_readiness(shot, state, bindings, asset_map, node),
        "review": shot.get("review", {"status": "unreviewed", "notes": ""}), "node": node}
