"""Local storyboard workspace and MiniMax Design task adapter (Python stdlib)."""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import copy
import contextvars
import datetime as dt
import hashlib
import json
import mimetypes
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gateway import Gateway
from workflow import WorkflowStore, WorkflowError, write as write_workflow_json
from workspaces import WorkspaceRegistry, WorkspacePath, WorkspaceStoreProxy, current as current_workspace, scope as workspace_scope, scoped_urls
from creator import script_document, review_bundle
from direction import scaffold as direction_scaffold, save_plan as save_direction, coverage_report, audition_spec, validate_plan as validate_direction, shot_digest
from multimodal import capabilities as reference_capabilities, compile_references
from media_probe import probe as probe_media, binary as local_media_binary
from audio_service import build_voice_design_body, build_speech_body, design_voice, submit_speech, render_performance, build_reference_speech_body, submit_reference_speech, clone_voice
from editor_bridge import EditorBridge
from personal_library import PersonalLibrary
from voice_lab import VoiceLab
from editing import LocalJobs, media_catalog, local_capabilities, resolve_media, empty_timeline, save_timeline, public_local_job

ROOT = Path(__file__).resolve().parent
REGISTRY = WorkspaceRegistry(ROOT)
DATA = WorkspacePath(REGISTRY, "data")
MEDIA = WorkspacePath(REGISTRY, "media")
PROJECT = WorkspacePath(REGISTRY, "project")
JOBS = WorkspacePath(REGISTRY, "jobs")
LOCK = threading.RLock()
STOP = threading.Event()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio")
INFLIGHT: set[str] = set()
GW = Gateway(os.environ.get("MINIMAX_DESIGN_GATEWAY", "http://127.0.0.1:8001"))
VERSION = "0.12.0"
WORKFLOW = WorkspaceStoreProxy(REGISTRY)
EDITOR = EditorBridge(ROOT)
LIBRARY = PersonalLibrary(ROOT)
LOCAL_JOBS = LocalJobs(ROOT)
LOCAL_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio-local")
VOICE_LAB = VoiceLab(ROOT)


class StudioError(Exception):
    def __init__(self, message, code="INVALID_REQUEST", status=400, detail=None):
        super().__init__(message)
        self.code, self.status, self.detail = code, status, detail


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def initialise():
    DATA.mkdir(exist_ok=True)
    MEDIA.mkdir(exist_ok=True)
    if not PROJECT.exists():
        shots = []
        actions = [
            ("窗边的纸船", "全景", "固定镜头", "清晨，一张木桌靠着窗户。白色纸船静静停在桌上。"),
            ("微风经过", "近景", "缓慢推进", "微风吹动薄纱窗帘，窗帘的影子掠过纸船。"),
            ("光停在船头", "特写", "固定镜头", "一束温暖的阳光照亮白色纸船的船头，画面安静停留。"),
        ]
        for i, (title, shot_type, camera, action) in enumerate(actions):
            shots.append({"id": f"shot-{i+1:03}", "number": f"S{i+1:03}", "title": title,
                "scene": "窗边 / 清晨", "shotType": shot_type, "camera": camera, "action": action,
                "prompt": f"电影分镜，{shot_type}。{action} 自然晨光，温暖木色，白色纸船，写实摄影，16:9，无文字。",
                "videoPrompt": f"{shot_type}，{camera}。{action} 写实电影摄影，自然晨光，无人物，无文字，无配乐。",
                "referencePaths": [], "kind": "video", "modelId": "wan3.0-video",
                "params": {"duration": "2", "resolution": "480P", "aspect_ratio": "16:9", "image_mode": "reference", "generate_audio": "false"},
                "position": {"x": 80 + i * 340, "y": 140}, "status": "draft"})
        atomic_json(PROJECT, {"id": "paper-boat-demo", "title": "纸船 · 接入验证示例", "revision": 1,
            "script": "【示例剧本，非用户原作】\n清晨，窗边的木桌上放着一只白色纸船。微风轻轻吹动薄纱窗帘，窗影掠过船身。一束温暖的阳光停在船头。",
            "style": "写实摄影；温暖晨光；木色、白色；16:9；无对白。",
            "shots": shots, "edges": [{"id": "e1", "source": "shot-001", "target": "shot-002"}, {"id": "e2", "source": "shot-002", "target": "shot-003"}], "updatedAt": now()})
    if not JOBS.exists():
        atomic_json(JOBS, [])


def validate_project(project):
    if not isinstance(project, dict) or not isinstance(project.get("shots"), list):
        raise StudioError("项目必须包含 shots 数组。")
    if len(project["shots"]) > 1000:
        raise StudioError("单项目最多 1000 个镜头。")
    ids = set()
    for shot in project["shots"]:
        sid = shot.get("id") if isinstance(shot, dict) else None
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid) or sid in ids:
            raise StudioError("镜头 id 必须唯一，只能包含字母、数字、下划线和短横线。")
        ids.add(sid)
        pos = shot.get("position", {"x": 0, "y": 0})
        for key in ("x", "y"):
            val = pos.get(key, 0)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not -100000 <= val <= 100000:
                raise StudioError("镜头坐标无效。")
        if not isinstance(shot.get("referencePaths", []), list):
            raise StudioError("referencePaths 必须是数组。")
    for edge in project.get("edges", []):
        if edge.get("source") not in ids or edge.get("target") not in ids:
            raise StudioError("连线指向不存在的镜头。")


def save_project(project, *, check_revision=True):
    validate_project(project)
    with LOCK:
        current = read_json(PROJECT, {})
        if check_revision and project.get("revision") != current.get("revision"):
            raise StudioError("项目已被 Codex 或其他窗口更新，请重新载入后再保存。", "REVISION_CONFLICT", 409)
        snapshots = DATA / "snapshots"
        snapshots.mkdir(exist_ok=True)
        if current:
            atomic_json(snapshots / f"project-r{current.get('revision', 0)}-{time.time_ns()}.json", current)
        project = copy.deepcopy(project)
        project["revision"] = int(current.get("revision", 0)) + 1
        project["updatedAt"] = now()
        atomic_json(PROJECT, project)
        return project


def upload_asset(body):
    name = str(body.get("name", "")).strip()
    if not name or Path(name).name != name or any(c in name for c in "/\\\x00"):
        raise StudioError("上传文件名无效。")
    try:
        content = base64.b64decode(body.get("contentBase64", ""), validate=True)
    except Exception:
        raise StudioError("上传内容不是有效的Base64。")
    if not content or len(content) > 64 * 1024 * 1024:
        raise StudioError("上传文件须在1字节至64MB之间。")
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm", ".txt", ".md"}:
        raise StudioError("此文件类型不支持。")
    destination = DATA / "uploads" / (uuid.uuid4().hex + suffix)
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(content)
    try:
        return WORKFLOW.import_asset({"path": str(destination), "name": body.get("displayName") or Path(name).stem,
            "kind": body.get("kind"), "role": body.get("role", "reference"), "provenance": {"source": "user-upload", "filename": name}})
    finally:
        destination.unlink(missing_ok=True)


def sync_shot_graph(graph, shot, patch):
    sid = shot["id"]
    assets = {a["id"]: a for a in WORKFLOW.assets()["assets"]}
    node = next((n for n in graph["nodes"] if n.get("shotId") == sid and n["type"] in {"video", "image", "reuse", "edit"}), None)
    fresh = node is None
    if fresh:
        y = max((n["position"]["y"] for n in graph["nodes"]), default=-450) + 450
        node = {"id": "video-" + sid, "type": shot.get("kind", "video"), "label": f"{sid} · {shot['title']}", "shotId": sid,
            "position": {"x": 550, "y": y}, "data": {"provider": "minimax-design", "modelId": shot.get("modelId", ""),
                "params": copy.deepcopy(shot.get("params", {})), "audioPlan": copy.deepcopy(shot.get("audioPlan", {}))}}
        graph["nodes"].append(node)
    node["label"] = f"{sid} · {shot.get('title', sid)}"
    data = node.setdefault("data", {})
    for key in ("modelId", "params", "audioPlan", "bindings", "negativePrompt"):
        if key in patch or fresh:
            if key in shot:
                data[key] = copy.deepcopy(shot[key])
    if "audioPlan" in patch and "generateAudio" in patch["audioPlan"]:
        entry = next((m for m in (offline_catalog() or {}).get("video", []) if m.get("id") == data.get("modelId")), None)
        if node["type"] == "video" and (entry is None or "generate_audio" in entry.get("params", {})):
            data.setdefault("params", {})["generate_audio"] = str(bool(patch["audioPlan"]["generateAudio"])).lower()
        shot["params"] = copy.deepcopy(data["params"])
    if node["type"] in {"image", "video"}:
        edge = next((e for e in graph["edges"] if e["target"] == node["id"] and e["targetPort"] == "prompt"), None)
        prompt_node = next((n for n in graph["nodes"] if edge and n["id"] == edge["source"]), None)
        if prompt_node is None:
            prompt_node = {"id": "prompt-" + sid, "type": "prompt", "label": sid + " · 创作提示", "shotId": sid,
                "position": {"x": node["position"]["x"] - 320, "y": node["position"]["y"]}, "data": {}}
            graph["nodes"].append(prompt_node)
            graph["edges"].append({"id": "prompt-edge-" + sid, "source": prompt_node["id"], "sourcePort": "text", "target": node["id"], "targetPort": "prompt"})
        if fresh or "videoPrompt" in patch or "prompt" in patch:
            prompt_node["data"]["text"] = shot.get("videoPrompt" if node["type"] == "video" else "prompt", "")
    if "bindings" in patch:
        ports = {"references", "firstFrame", "audioReferences", "videoReferences", "audioPlan"}
        old_edges = [e for e in graph["edges"] if e["target"] == node["id"] and e["targetPort"] in ports]
        graph["edges"] = [e for e in graph["edges"] if e not in old_edges]
        candidates = {e["source"] for e in old_edges}
        linked = {e["source"] for e in graph["edges"]} | {e["target"] for e in graph["edges"]}
        graph["nodes"] = [n for n in graph["nodes"] if not (n["id"] in candidates and n["id"] not in linked and n["type"] == "asset" and n.get("shotId") == sid)]
        data["requiredAssetIds"] = [b["assetId"] for b in shot["bindings"] if b.get("enabled", True) and b.get("usage", "model_reference") == "model_reference" and assets[b["assetId"]].get("mediaType") == "image"]
        for index, binding in enumerate(shot["bindings"]):
            if not binding.get("enabled", True):
                continue
            asset = assets[binding["assetId"]]
            media_type = asset.get("mediaType", "image")
            if media_type not in {"image", "audio", "video"}:
                continue
            # Reuse/edit shots also retain visible sound planning nodes, separately from their source video.
            if node["type"] not in {"image", "video"} and media_type != "audio":
                continue
            aid = "binding-" + sid + "-" + hashlib.sha1(binding["assetId"].encode()).hexdigest()[:12]
            graph["nodes"].append({"id": aid, "type": "asset", "assetId": binding["assetId"], "label": binding.get("alias") or asset["name"],
                "shotId": sid, "position": {"x": node["position"]["x"] - 670, "y": node["position"]["y"] + index * 300},
                "data": {"role": binding.get("role"), "usage": binding.get("usage"), "alias": binding.get("alias"), "mediaType": media_type}})
            target_port = "references" if media_type == "image" else "audioReferences" if media_type == "audio" else "videoReferences"
            if node["type"] in {"reuse", "edit"} and media_type == "audio":
                target_port = "audioPlan"
            graph["edges"].append({"id": "edge-" + aid, "source": aid, "sourcePort": media_type, "target": node["id"], "targetPort": target_port,
                "role": binding.get("role"), "usage": binding.get("usage", "model_reference")})
    return graph


def update_shot(sid, body, review_only=False, *, project=None, graph=None, persist=True):
    project = read_json(PROJECT) if project is None else project
    if "revision" in body and body["revision"] != project["revision"]:
        raise StudioError("项目已更新，请重新载入后保存。", "REVISION_CONFLICT", 409)
    shot = next((s for s in project["shots"] if s["id"] == sid), None)
    if shot is None:
        raise StudioError("镜头不存在。", "SHOT_NOT_FOUND", 404)
    graph = WORKFLOW.workflow() if graph is None else graph
    if review_only:
        if body.get("status") not in {"approved", "changes_requested", "unreviewed"}:
            raise StudioError("审核状态无效。")
        media_review = bool(shot.get("lastJobId") and shot.get("mediaUrl") and not shot.get("validationOnly"))
        shot["review"] = {"status": body["status"], "notes": str(body.get("notes", "")), "updatedAt": now(),
            "scope": "media" if media_review else "plan", "jobId": shot.get("lastJobId") if media_review else None}
    else:
        allowed = {"title", "scene", "sceneId", "shotType", "camera", "action", "dialogue", "prompt", "videoPrompt", "negativePrompt", "modelId", "params", "audioPlan", "bindings", "plannedDuration", "sourceBlockIds", "kind", "editNotes"}
        extra = set(body) - allowed - {"revision"}
        if extra:
            raise StudioError("不支持的镜头字段：" + ",".join(sorted(extra)))
        if "bindings" in body:
            if not isinstance(body["bindings"], list):
                raise StudioError("bindings需要数组。")
            assets = {a["id"]: a for a in WORKFLOW.assets()["assets"]}
            seen = set()
            for binding in body["bindings"]:
                if binding.get("assetId") not in assets or binding["assetId"] in seen:
                    raise StudioError("引用资源不存在或重复。")
                seen.add(binding["assetId"])
                if binding.get("usage", "model_reference") not in {"model_reference", "voice_identity", "post_mix", "context"}:
                    raise StudioError("引用用途无效。")
        shot.update({key: copy.deepcopy(value) for key, value in body.items() if key in allowed})
        if "dialogue" in body:
            shot.setdefault("audioPlan", {})["lines"] = copy.deepcopy(body["dialogue"]) if isinstance(body["dialogue"], list) else [{"text": str(body["dialogue"]), "kind": "dialogue", "voice_id": None}]
            body = {**body, "audioPlan": shot["audioPlan"]}
        shot["review"] = {"status": "unreviewed", "notes": "镜头设置已更新，需重新审阅。", "scope": "plan", "updatedAt": now()}
        graph = sync_shot_graph(graph, shot, body)
    from workflow import validate_structure
    validate_structure(graph)
    if not persist:
        return {"project": project, "graph": graph, "shot": shot}
    old_project = read_json(PROJECT)
    saved = save_project(project)
    try:
        if not review_only:
            graph = WORKFLOW.save(graph)
    except Exception:
        # Locks serialize web mutations; restore the previous project if graph validation/write failed.
        atomic_json(PROJECT, old_project)
        raise
    return {"shot": shot, "projectRevision": saved["revision"], "workflowRevision": graph["revision"]}


def update_shots_batch(body):
    project, graph = read_json(PROJECT), WORKFLOW.workflow()
    if body.get("revision") != project["revision"]:
        raise StudioError("项目已更新，请重新载入。", "REVISION_CONFLICT", 409)
    patches = body.get("patches", [])
    if not isinstance(patches, list) or not 1 <= len(patches) <= 1000:
        raise StudioError("批量修改需要1–1000个镜头补丁。")
    ids = set()
    for entry in patches:
        sid = entry.get("shotId")
        if sid in ids:
            raise StudioError("批量修改不能重复同一镜头。")
        ids.add(sid)
        result = update_shot(sid, {k: v for k, v in entry.items() if k != "shotId"}, project=project, graph=graph, persist=False)
        project, graph = result["project"], result["graph"]
    previous = read_json(PROJECT)
    saved = save_project(project)
    try:
        graph = WORKFLOW.save(graph)
    except Exception:
        atomic_json(PROJECT, previous)
        raise
    return {"updatedShotIds": sorted(ids), "projectRevision": saved["revision"], "workflowRevision": graph["revision"]}


def create_shot(body):
    project = read_json(PROJECT)
    if "revision" in body and body["revision"] != project["revision"]:
        raise StudioError("项目已更新，请重新载入。", "REVISION_CONFLICT", 409)
    sid = body.get("id") or "S" + str(len(project["shots"]) + 1).zfill(3)
    if any(s["id"] == sid for s in project["shots"]):
        sid = "shot-" + uuid.uuid4().hex[:8]
    block_ids = body.get("sourceBlockIds", [])
    document = script_document(project)
    selected = [b["text"] for b in document["blocks"] if b["id"] in block_ids]
    if len(selected) != len(block_ids):
        raise StudioError("来源段落已变化，请重新选择。")
    action = body.get("action") or "\n\n".join(selected)
    model_id = body.get("modelId") or project.get("defaultModelId") or ""
    entry = next((m for m in (offline_catalog() or {}).get("video", []) if m.get("id") == model_id), {})
    defaults = {k: str(v["default"]) for k, v in entry.get("params", {}).items() if "default" in v}
    if defaults.get("aspect_ratio") == "adaptive" and "16:9" in entry.get("params", {}).get("aspect_ratio", {}).get("options", []):
        defaults["aspect_ratio"] = "16:9"
    shot = {"id": sid, "number": sid, "title": body.get("title") or "新镜头", "scene": body.get("scene", "未分场"),
        "sceneId": body.get("sceneId"), "shotType": body.get("shotType", "待细化"), "camera": body.get("camera", "待设计"),
        "action": action, "prompt": body.get("prompt", action), "videoPrompt": body.get("videoPrompt", action),
        "sourceBlockIds": block_ids, "referencePaths": [], "bindings": [], "kind": "video", "modelId": model_id,
        "params": {**defaults, **body.get("params", {})}, "audioPlan": {"mode": "separate_tracks", "lines": [], "generateAudio": False},
        "position": {"x": 100 + len(project["shots"]) * 340, "y": 100}, "status": "draft", "review": {"status": "unreviewed", "notes": ""}}
    project["shots"].append(shot)
    graph = sync_shot_graph(WORKFLOW.workflow(), shot, shot)
    from workflow import validate_structure
    validate_structure(graph)
    saved = save_project(project)
    try:
        graph = WORKFLOW.save(graph)
    except Exception:
        project["shots"].pop()
        project["revision"] = saved["revision"]
        save_project(project)
        raise
    return {"shot": shot, "projectRevision": saved["revision"], "workflowRevision": graph["revision"]}


def select_cast(body):
    plan = read_json(DATA / "direction.json", {})
    project = read_json(PROJECT)
    validate_direction(plan, project)
    if body.get("revision") != plan.get("revision") or body.get("projectRevision") != project["revision"]:
        raise StudioError("项目或选角已更新，请重新打开选角卡片。", "REVISION_CONFLICT", 409)
    character = next((c for c in plan["characters"] if c["id"] == body.get("voiceId")), None)
    asset = next((a for a in WORKFLOW.assets()["assets"] if a["id"] == body.get("assetId")), None)
    if (not character or body.get("assetId") not in character.get("candidateAssetIds", []) or not asset
            or asset.get("role") != "voice_identity" or not asset.get("vendorVoiceId")
            or asset.get("voiceId") != character["id"] or asset.get("reviewStatus") in {"blocked", "missing"}):
        raise StudioError("请选择该角色真实可用的候选声线。", "VOICE_REQUIRED")
    patches = []
    for shot in project["shots"]:
        bindings = copy.deepcopy(shot.get("bindings", []))
        changed = False
        for binding in bindings:
            if binding.get("usage") == "voice_identity" and binding.get("voiceId") == character["id"]:
                binding.update(assetId=asset["id"], alias=character["name"] + " · 已选声线")
                changed = True
        if changed:
            patches.append({"shotId": shot["id"], "bindings": bindings})
    if patches:
        update_shots_batch({"revision": project["revision"], "patches": patches})
    WORKFLOW.patch_asset(asset["id"], {"reviewStatus": "ready", "reviewNotes": body.get("notes") or "用户通过选角卡片选用此音色；不代表任何正式对白通过。"})
    character.update(selectedAssetId=asset["id"], castingStatus="selected")
    saved = save_direction(DATA / "direction.json", plan, read_json(PROJECT))
    return {"directionRevision": saved["revision"], "selectedAssetId": asset["id"], "updatedShots": len(patches)}


def public_job(job):
    return {k: v for k, v in job.items() if k not in {"upstream", "requestHash", "payload", "gatewayResponse"}}


def update_job(jid, **changes):
    with LOCK:
        jobs = read_json(JOBS, [])
        for job in jobs:
            if job["id"] == jid:
                job.update(changes, updatedAt=now())
                atomic_json(JOBS, jobs)
                return copy.deepcopy(job)
    raise StudioError("找不到任务。", "NOT_FOUND", 404)


def find_job(jid):
    with LOCK:
        for job in read_json(JOBS, []):
            if job["id"] == jid:
                return copy.deepcopy(job)
    raise StudioError("找不到任务。", "NOT_FOUND", 404)


def models():
    catalog = {kind: GW.models(kind) for kind in ("image", "video")}
    try:
        catalog["speech"] = GW.models("speech")
    except Exception:
        catalog["speech"] = read_json(DATA / "model-catalog.json", {}).get("speech", [])
    for model in catalog["video"]:
        model["referenceCapabilities"] = reference_capabilities(model)
    atomic_json(DATA / "model-catalog.json", {**catalog, "cachedAt": now()})
    return catalog


def offline_catalog():
    cached = read_json(DATA / "model-catalog.json", None)
    return {kind: cached.get(kind, []) for kind in ("image", "video", "speech")} if cached else None


def prepare_audio_job(body, job_type):
    if body.get("confirmed") is not True:
        raise StudioError("音频模型调用会消耗积分，请确认具体声音任务。", "CONFIRMATION_REQUIRED")
    rid = body.get("requestId", "")
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", rid):
        raise StudioError("声音任务需要稳定的requestId。")
    digest = hashlib.sha256(json.dumps({**body, "type": job_type}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with LOCK:
        for old in read_json(JOBS, []):
            if old.get("requestId") == rid:
                if old.get("requestHash") != digest:
                    raise StudioError("同一requestId不能更换声音参数。", "IDEMPOTENCY_CONFLICT", 409)
                return old, False
        if job_type == "voice_design":
            validated = build_voice_design_body(body.get("description", ""), body.get("previewText", ""))
            payload = {"description": validated["prompt"], "previewText": validated["preview_text"], "voiceId": body.get("voiceId")}
            model_id, text = "voice-design", body["previewText"]
        elif job_type in {"reference_speech", "voice_clone"}:
            workspace = current_workspace(REGISTRY)
            if job_type == "voice_clone":
                reference = resolve_media(workspace, body.get("referenceAssetId"), {"audio"})
                if not 10 <= reference["duration"] <= 300 or Path(reference["path"]).stat().st_size > 20*1024*1024:
                    raise StudioError("音色克隆需要10秒至5分钟、20MB以内的参考录音。")
                if Path(reference['path']).suffix.lower() not in {'.mp3','.wav','.m4a'}:
                    raise StudioError('克隆参考支持 mp3/wav/m4a。')
                build_voice_design_body("clone", body.get("previewText", ""))
                payload = {"audioPath": reference["path"], "previewText": body["previewText"], "voiceId": body.get("voiceId")}
                model_id, text = "voice-clone", body["previewText"]
            else:
                ids=body.get('referenceAssetIds', [])
                if not isinstance(ids,list) or len(ids)>3 or len(ids)!=len(set(ids)):
                    raise StudioError('选择最多3条不重复的声音参考。')
                references=[resolve_media(workspace,aid,{'audio'}) for aid in ids]
                for reference in references:
                    if reference['duration']>30 or Path(reference['path']).stat().st_size>10*1024*1024 or Path(reference['path']).suffix.lower() not in {'.mp3','.wav'}:
                        raise StudioError('Seed Audio 参考使用30秒内、10MB内的 mp3/wav。')
                model=next((m for m in GW.models('speech') if m['id']==body.get('modelId')),None)
                if not model:raise StudioError('当前目录没有所选声音模型。','MODEL_NOT_FOUND')
                spoken=body.get('text','').strip()
                if not spoken:raise StudioError('请填写台词。')
                direction=body.get('direction','').strip()
                if len(direction)>1200:raise StudioError('表演说明过长。')
                reference_note='；'.join(f'@音频{i+1}为第{i+1}个声音参考' for i in range(len(references)))
                prompt='\n'.join(x for x in [reference_note,direction,'需要说出的台词：'+spoken] if x)
                seed=build_reference_speech_body(model,prompt,body.get('params',{}),[r['path'] for r in references],[], 'studio-seed-preview')
                payload={'request':seed,'text':prompt,'canonicalText':spoken,'referenceAssetIds':ids,'voiceId':body.get('voiceId')}
                model_id,text=model['id'],spoken
        else:
            project = read_json(PROJECT)
            if body.get("shotId") and not any(s["id"] == body["shotId"] for s in project["shots"]):
                raise StudioError("声音任务镜头不在当前项目。", "SHOT_NOT_FOUND", 404)
            voice = next((a for a in WORKFLOW.assets()["assets"] if a["id"] == body.get("voiceAssetId")), None)
            if not voice or not voice.get("vendorVoiceId") or voice.get("role") != "voice_identity":
                raise StudioError("请选择本项目中带真实音色ID的角色试听。", "VOICE_REQUIRED")
            if voice.get("reviewStatus") in {"blocked", "missing"}:
                raise StudioError("此声线已被隔离或缺失，请选择可用候选。", "VOICE_BLOCKED")
            entries = GW.models("speech")
            model = next((m for m in entries if m["id"] == body.get("modelId")), None)
            if model is None:
                raise StudioError("当前目录没有此语音模型。", "MODEL_NOT_FOUND")
            performed_text = render_performance(body.get("text", ""), body.get("performance"))
            build_speech_body(model, performed_text, voice["vendorVoiceId"], body.get("params", {}), "studio-speech-preview")
            payload = {"model": model, "vendorVoiceId": voice["vendorVoiceId"], "voiceId": voice.get("voiceId"), "voiceAssetId": voice["id"], "params": body.get("params", {}), "text": performed_text, "canonicalText": body["text"], "performance": body.get("performance", [])}
            model_id, text = model["id"], body["text"]
        job = {"id": uuid.uuid4().hex, "type": job_type, "kind": "audio", "workspaceId": current_workspace(REGISTRY).id,
            "name": body.get("name") or body.get("characterId") or body.get("cueId") or "声音候选", "shotId": body.get("shotId"),
            "characterId": body.get("characterId"), "cueId": body.get("cueId"), "requestId": rid, "requestHash": digest,
            "modelId": model_id, "prompt": text, "status": "queued", "createdAt": now(), "updatedAt": now(), "payload": payload}
        jobs = read_json(JOBS, [])
        jobs.append(job)
        atomic_json(JOBS, jobs)
        return job, True


def register_audio_result(job):
    role = "voice_identity" if job.get("type") in {"voice_design", "voice_clone"} else "dialogue_performance"
    aid = "audio-" + job["id"]
    asset = WORKFLOW.import_asset({"id": aid, "path": job["mediaPath"], "name": job.get("name", "声音候选"), "kind": "audio", "role": role,
        "reviewStatus": "candidate", "vendorVoiceId": job.get("vendorVoiceId") or job.get("payload", {}).get("vendorVoiceId"),
        "voiceId": job.get("payload", {}).get("voiceId"),
        "characterId": job.get("characterId"), "spokenText": job.get("prompt"), "cueIds": [job["cueId"]] if job.get("cueId") else [],
        "provenance": {"source": "minimax-design", "modelId": job["modelId"], "jobId": job["id"], "taskId": job.get("taskId"), "type": job.get("type"), "performance": job.get("payload", {}).get("performance", []), "submittedText": job.get("payload", {}).get("text"), "voiceDesignPrompt": job.get("payload", {}).get("description")}})
    if job.get("shotId") and role == "dialogue_performance":
        with LOCK:
            project = read_json(PROJECT)
            shot = next((s for s in project["shots"] if s["id"] == job["shotId"]), None)
            if shot and not any(b.get("assetId") == aid for b in shot.get("bindings", [])):
                bindings = copy.deepcopy(shot.get("bindings", [])) + [{"assetId": aid, "alias": job.get("cueId") or "本镜台词", "role": role, "usage": "post_mix", "enabled": True, "cueId": job.get("cueId")}]
                plan = copy.deepcopy(shot.get("audioPlan", {}))
                plan.setdefault("performances", []).append({"assetId": aid, "cueId": job.get("cueId"), "text": job.get("prompt"), "status": "candidate"})
                update_shot(shot["id"], {"revision": project["revision"], "bindings": bindings, "audioPlan": plan})
    return update_job(job["id"], assetId=aid, mediaUrl=asset["mediaUrl"], duration=asset.get("duration"), waveform=asset.get("waveform"), projectAttached=True)


def execute_local_job(workspace,jid):
    LOCAL_JOBS.execute(workspace,jid)
    job=next((j for j in LOCAL_JOBS.get(workspace) if j['id']==jid),None)
    if not job or job.get('status')!='succeeded' or job.get('operation')!='tts':return
    payload=job.get('payload',{})
    if not payload.get('shotId'):return
    try:
        with workspace_scope(workspace),LOCK:
            project=read_json(PROJECT)
            shot=next((s for s in project['shots'] if s['id']==payload['shotId']),None)
            if not shot:raise StudioError('镜头已删除，结果仍保留在资源库。')
            if payload.get('cueId'):
                cue=next((c for c in shot.get('audioPlan',{}).get('cues',[]) if (c.get('cueId') or c.get('id'))==payload['cueId']),None)
                if not cue or cue.get('text','').strip()!=payload['text']:raise StudioError('生成期间台词已修改，请手动复核候选引用。')
            bindings=copy.deepcopy(shot.get('bindings',[]));plan=copy.deepcopy(shot.get('audioPlan',{}))
            for asset in job.get('assets',[]):
                if not any(b.get('assetId')==asset['id'] for b in bindings):
                    bindings.append({'assetId':asset['id'],'alias':payload.get('cueId') or '本地配音候选','role':'dialogue_performance','usage':'post_mix','enabled':True,'cueId':payload.get('cueId')})
                    plan.setdefault('performances',[]).append({'assetId':asset['id'],'cueId':payload.get('cueId'),'text':payload['text'],'status':'candidate'})
            update_shot(shot['id'],{'revision':project['revision'],'bindings':bindings,'audioPlan':plan})
            LOCAL_JOBS.update(workspace,jid,projectAttached=True)
    except Exception as exc:LOCAL_JOBS.update(workspace,jid,attachmentWarning=str(exc))


def submit_audio_job(jid):
    with LOCK:
        job = find_job(jid)
        if job["status"] != "queued":
            return
        job = update_job(jid, status="submitting")
    try:
        payload = job["payload"]
        if job["type"] in {"voice_design", "voice_clone"}:
            result = (design_voice(GW, payload["description"], payload["previewText"]) if job["type"]=="voice_design"
                else clone_voice(GW,payload["audioPath"],payload["previewText"]))
            if result.get('demo_audio') and not result.get('trial_audio_url'):result['trial_audio_url']=result['demo_audio']
            if not result.get("voice_id") or not result.get("trial_audio_url"):
                return update_job(jid, status="unknown", error="音色设计未返回完整试听信息，请核对Design记录，不自动重发。", upstream=result)
            update_job(jid, status="downloading", vendorVoiceId=result["voice_id"], upstream=result)
            media = collect_media(job, {"result": {"path": result["trial_audio_url"]}})
            completed = update_job(jid, status="succeeded", vendorVoiceId=result["voice_id"], projectAttached=False, **media)
            register_audio_result(completed)
        elif job['type']=='reference_speech':
            result=submit_reference_speech(GW,{**payload['request'],'filename':'seed-'+jid})
            if not result.get('task_id'):
                return update_job(jid,status='unknown',error='声音接口未返回任务ID，先核对Design，不重新提交。',upstream=result)
            update_job(jid,status='processing',taskId=str(result['task_id']),upstream=result)
        else:
            result = submit_speech(GW, payload["model"], payload["text"], payload["vendorVoiceId"], payload["params"], "speech-" + jid)
            if not result.get("task_id"):
                return update_job(jid, status="unknown", error="语音提交没有返回task_id，请核对任务，不自动重发。", upstream=result)
            update_job(jid, status="processing", taskId=str(result["task_id"]), upstream=result)
    except Exception as exc:
        current_job = find_job(jid)
        local_failure = current_job.get("vendorVoiceId") and current_job.get("upstream", {}).get("trial_audio_url")
        update_job(jid, status="download_failed" if local_failure else "unknown" if getattr(exc, "submission_uncertain", False) else "failed", error=str(exc), errorCode=str(getattr(exc, "code", "ERROR")))


def workflow_compile(body=None, *, live=False):
    body = body or {}
    catalog = models() if live else offline_catalog()
    with LOCK:
        jobs = read_json(JOBS, [])
    compiled = WORKFLOW.compile(body.get("nodeIds"), catalogs=catalog, jobs=jobs)
    direction = read_json(DATA / "direction.json", None)
    if direction:
        report = coverage_report(direction, read_json(PROJECT), WORKFLOW.workflow(), WORKFLOW.assets())
        by_shot = {s["shotId"]: s for s in report["shots"]}
        compiled["referenceCoverage"] = report["summary"]
        for task in compiled.get("tasks", []):
            review = by_shot.get(task.get("shotId"))
            if task.get("kind") == "video" and review and not review["referenceReady"]:
                task.update(ready=False, status="blocked")
                compiled["errors"].append({"nodeId": task["nodeId"], "code": "REFERENCE_COVERAGE", "message": "；".join(review["errors"])})
    return compiled


def run_workflow(body):
    if body.get("confirmed") is not True:
        raise StudioError("执行所选模型节点会消耗积分，请确认。", "CONFIRMATION_REQUIRED")
    rid = body.get("requestId", "")
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", rid):
        raise StudioError("工作流执行需要稳定的 requestId。")
    selection = body.get("nodeIds")
    if not isinstance(selection, list) or not 1 <= len(selection) <= 32:
        raise StudioError("明确选择1–32个生成节点；不会自动执行全图。")
    run_path = DATA / "workflow-runs.json"
    with LOCK:
        runs = read_json(run_path, [])
        previous = next((r for r in runs if r["requestId"] == rid), None)
        if previous:
            if previous["nodeIds"] != selection:
                raise StudioError("同一执行标识不能用于不同节点。", "IDEMPOTENCY_CONFLICT", 409)
            return {**previous, "jobs": [public_job(find_job(j)) for j in previous.get("jobIds", [])]}
        compiled = workflow_compile(body, live=True)
        runnable = [t for t in compiled["tasks"] if t["ready"]]
        if compiled["errors"] or not runnable or any(t["external"] for t in compiled["tasks"]):
            raise StudioError("所选节点尚不能执行；请先处理依赖或交给 Codex 补图。", "WORKFLOW_NOT_READY", 400,
                {"errors": compiled["errors"], "tasks": [{"nodeId": t["nodeId"], "status": t["status"]} for t in compiled["tasks"]]})
        run = {"requestId": rid, "nodeIds": selection, "revision": compiled["revision"], "jobIds": [], "createdAt": now(), "status": "preparing"}
        runs.append(run)
        atomic_json(run_path, runs)
        created = []
        try:
            for task in runnable:
                if not task.get("shotId"):
                    raise StudioError("生成节点需要绑定镜头编号，以保留结果归属。")
                payload = {"shotId": task["shotId"], "kind": task["kind"], "modelId": task["modelId"],
                    "params": task["params"], "prompt": task["prompt"], "imagePaths": task["imagePaths"],
                    "audioPaths": task.get("audioPaths", []), "videoPaths": task.get("videoPaths", []),
                    "audioPlan": task.get("audioPlan", {}),
                    "requestId": rid + "-" + hashlib.sha256(task["nodeId"].encode()).hexdigest()[:12], "confirmed": True}
                if task.get("trimSeconds"):
                    payload["trimSeconds"] = task["trimSeconds"]
                if task.get("trimStartSeconds"):
                    payload["trimStartSeconds"] = task["trimStartSeconds"]
                job, fresh = prepare_job(payload)
                job = update_job(job["id"], workflowNodeId=task["nodeId"], workflowSignature=task["signature"], workflowRevision=compiled["revision"])
                created.append((job, fresh))
                run["jobIds"].append(job["id"])
                atomic_json(run_path, runs)
        except Exception as exc:
            for job, fresh in created:
                if fresh:
                    update_job(job["id"], status="failed", error="整批预检未通过，未发送生成请求：" + str(exc))
            run.update(status="failed_preflight", error=str(exc))
            atomic_json(run_path, runs)
            raise
        run["status"] = "queued"
        atomic_json(run_path, runs)
        for job, fresh in created:
            if fresh:
                schedule(job["id"], submit_job)
        return {**run, "jobs": [public_job(j) for j, _ in created]}


def prepare_job(body, *, validate_only=False):
    if not isinstance(body, dict) or body.get("confirmed") is not True:
        raise StudioError("提交生成会消耗 Design 积分；请明确确认本次任务。", "CONFIRMATION_REQUIRED", 400)
    kind = body.get("kind")
    if kind not in ("image", "video"):
        raise StudioError("kind 必须为 image 或 video。")
    request_id = 'preflight-'+uuid.uuid4().hex if validate_only else body.get("requestId")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", request_id):
        raise StudioError("请提供稳定的 requestId（8–100 个字母、数字、短横线或下划线）。")
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with LOCK:
        for previous in read_json(JOBS, []):
            if previous.get("requestId") == request_id:
                if previous["requestHash"] != digest:
                    raise StudioError("相同 requestId 的参数不同；请检查已有任务。", "IDEMPOTENCY_CONFLICT", 409)
                return previous, False
        project = read_json(PROJECT)
        if not any(s["id"] == body.get("shotId") for s in project["shots"]):
            raise StudioError("提交前请先保存镜头。", "SHOT_NOT_FOUND", 404)
        direction = read_json(DATA / "direction.json", None)
        if kind == "video" and direction:
            review = next(s for s in coverage_report(direction, project, WORKFLOW.workflow(), WORKFLOW.assets())["shots"] if s["shotId"] == body["shotId"])
            if not review["referenceReady"]:
                raise StudioError("本镜参考或导演分析需更新：" + "；".join(review["errors"]), "REFERENCE_COVERAGE", 400)
    entries = GW.models(kind)
    model = next((m for m in entries if m["id"] == body.get("modelId")), None)
    if not model:
        raise StudioError("模型不在当前 Design 目录中。", "MODEL_NOT_FOUND")
    prompt = body.get("prompt", "").strip()
    if not prompt or len(prompt) > model.get("promptMaxLength", 20000):
        raise StudioError("提示词为空或超过模型长度限制。")
    provided = body.get("params", {})
    if not isinstance(provided, dict):
        raise StudioError("params 必须是对象。")
    specs = model.get("params", {})
    params = {k: str(v["default"]) for k, v in specs.items() if "default" in v}
    for key, val in provided.items():
        if key not in specs:
            raise StudioError(f"此模型不支持参数：{key}")
        spec = specs[key]
        value = str(val).lower() if isinstance(val, bool) else str(val)
        if spec.get("options") and value not in [str(x) for x in spec["options"]]:
            raise StudioError(f"{key}={value} 不在模型允许范围：{', '.join(map(str, spec['options']))}。", "UNSUPPORTED_PARAMETER")
        if spec.get("type") == "slider":
            try:
                number = float(value)
                if not spec.get("min", float('-inf')) <= number <= spec.get("max", float('inf')):
                    raise ValueError()
            except ValueError:
                raise StudioError(f"{key} 超出允许范围。")
        params[key] = value
    for constraint in model.get("paramConstraints", []):
        cond, disabled = constraint.get("if", {}), constraint.get("disable", {})
        if params.get(cond.get("param")) == cond.get("eq") and params.get(disabled.get("param")) in disabled.get("options", []):
            raise StudioError(f"当前生成方式不支持 {disabled.get('param')}={params.get(disabled.get('param'))}。")
    paths = body.get("imagePaths", [])
    if not isinstance(paths, list) or len(paths) > model.get("max_refs", 16):
        raise StudioError("参考图数量超过模型限制。")
    for item in paths:
        if not isinstance(item, str):
            raise StudioError("参考图路径无效。")
        path = Path(item)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise StudioError("参考图须为存在的本地 PNG、JPG 或 WebP 绝对路径。")
    if model.get("referenceImageRequired") and not paths:
        raise StudioError("此模型要求参考图片。")
    media_paths = {"image": paths, "audio": body.get("audioPaths", []), "video": body.get("videoPaths", [])}
    if kind == "image" and (media_paths["audio"] or media_paths["video"]):
        raise StudioError("图片生成不接受音频/视频条件。")
    for media_type in ("audio", "video"):
        if not isinstance(media_paths[media_type], list) or len(media_paths[media_type]) > 30:
            raise StudioError("媒体参考路径数组无效。")
        allowed = {".mp3", ".wav"} if media_type == "audio" else {".mp4", ".mov", ".webm"}
        for item in media_paths[media_type]:
            if not isinstance(item, str) or not Path(item).is_absolute() or not Path(item).is_file() or Path(item).suffix.lower() not in allowed:
                raise StudioError("音视频引用需为本地支持格式的真实文件。")
    internal_params = {}
    if kind == "video" and params.get("image_mode", "reference") == "reference" and (reference_capabilities(model)["adapterSupported"] or media_paths["audio"] or media_paths["video"]):
        registered = {str(Path(a["path"]).resolve()): a for a in WORKFLOW.assets()["assets"] if a.get("path")}
        refs, ref_assets = [], {}
        for media_type, entries in media_paths.items():
            for index, item in enumerate(entries):
                aid = f"input-{media_type}-{index+1}"
                prior = registered.get(str(Path(item).resolve()), {})
                ref_assets[aid] = {"id": aid, "mediaType": media_type, "path": str(Path(item).resolve()), "reviewStatus": "ready", "role": prior.get("role", "reference")}
                if media_type in {"audio", "video"}:
                    ref_assets[aid].update(probe_media(item, waveform=False))
                refs.append({"assetId": aid, "usage": "model_reference", "role": prior.get("role", "reference"), "enabled": True})
        compiled_refs = compile_references(model, prompt, refs, ref_assets, params=params)
        if compiled_refs["errors"]:
            raise StudioError("引用验证失败：" + "；".join(compiled_refs["errors"]), "REFERENCE_VALIDATION_FAILED")
        internal_params = compiled_refs["paramsPatch"]
    elif media_paths["audio"] or media_paths["video"]:
        raise StudioError("此模型/生成模式的音视频引用尚未适配。", "REFERENCE_UNSUPPORTED")
    trim = body.get("trimSeconds")
    if trim is not None and (kind != "video" or isinstance(trim, bool) or not isinstance(trim, (int, float)) or not math.isfinite(trim) or trim <= 0 or trim > float(params.get("duration", 0))):
        raise StudioError("裁剪时长须大于 0，且不超过生成时长。")
    trim_start = body.get("trimStartSeconds", 0)
    if isinstance(trim_start, bool) or not isinstance(trim_start, (int, float)) or not math.isfinite(trim_start) or trim_start < 0 or (trim_start and not trim) or (trim and trim_start + trim > float(params.get("duration", 0))):
        raise StudioError("裁切起点与长度超过生成源时长。")
    job = {"id": uuid.uuid4().hex, "requestId": request_id, "requestHash": digest,
        "shotId": body["shotId"], "kind": kind, "modelId": model["id"], "modelName": model.get("name"),
        "status": "queued", "createdAt": now(), "updatedAt": now(), "params": params,
        "trimSeconds": trim, "trimStartSeconds": trim_start, "prompt": prompt, "payload": {"model": model, "imagePaths": paths, "paramsPatch": internal_params},
        "audioPaths": media_paths["audio"], "videoPaths": media_paths["video"], "audioPlan": copy.deepcopy(body.get("audioPlan", {})), "workspaceId": current_workspace(REGISTRY).id,
        "gateway": getattr(GW, "base_url", "http://127.0.0.1:8001")}
    if validate_only:
        Gateway.build_body(kind, model, prompt, {**params, **internal_params}, paths, "preflight.png" if kind=='image' else "preflight.mp4")
        return {"valid":True,"referenceCount":len(paths),"willGenerate":False},False
    with LOCK:
        jobs = read_json(JOBS, [])
        for previous in jobs:
            if previous.get("requestId") == request_id:
                if previous["requestHash"] != digest:
                    raise StudioError("相同 requestId 的参数不同。", "IDEMPOTENCY_CONFLICT", 409)
                return previous, False
        jobs.append(job)
        atomic_json(JOBS, jobs)
    return job, True


def schedule(jid, function):
    context = contextvars.copy_context()
    key = (current_workspace(REGISTRY).id, jid)
    with LOCK:
        if key in INFLIGHT:
            return
        INFLIGHT.add(key)
    def runner():
        try:
            context.run(function, jid)
        finally:
            with LOCK:
                INFLIGHT.discard(key)
    POOL.submit(runner)


def submit_job(jid):
    with LOCK:
        job = find_job(jid)
        if job["status"] != "queued":
            return
        job = update_job(jid, status="submitting")
    try:
        result = GW.submit(job["kind"], job["payload"]["model"], job["prompt"], {**job["params"], **job["payload"].get("paramsPatch", {})}, job["payload"]["imagePaths"], f"studio-{jid}.{'mp4' if job['kind']=='video' else 'png'}")
        task_id = result.get("task_id") or result.get("taskId")
        if not task_id and isinstance(result.get("data"), dict):
            task_id = result["data"].get("task_id")
        if not task_id:
            update_job(jid, status="unknown", error="提交没有返回 task_id。已停止自动重试，请先核对 Design 任务与扣费。", upstream=result)
            return
        update_job(jid, status="processing", taskId=str(task_id), upstream=result)
    except Exception as exc:
        # Never automatically retry a billable POST, even on a lost response.
        code = getattr(exc, "code", "")
        status = getattr(exc, "status", getattr(exc, "status_code", 0))
        uncertain = getattr(exc, "submission_uncertain", False) or isinstance(exc, (TimeoutError, urllib.error.URLError)) or (isinstance(status, int) and status >= 500) or "TIMEOUT" in str(code)
        update_job(jid, status="unknown" if uncertain else "failed", error=str(exc)[:2000], errorCode=str(code))


def ffmpeg_binary(name):
    local = local_media_binary(name)
    if local:
        return local
    bundled = Path(os.environ.get("LOCALAPPDATA", "")) / "com.minimax.hub/current/resources/ffmpeg" / (name + ".exe")
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    raise StudioError(f"未找到 {name}。", "MEDIA_TOOL_MISSING", 503)


def find_media_candidates(data):
    found = []
    path_keys = {"path", "local_path", "localPath", "file_path", "filePath", "output_path", "outputPath", "url", "video_url", "image_url", "file_url", "download_url"}
    containers = {"data", "result", "results", "output", "outputs", "files", "paths", "artifacts", "images", "videos", "asset", "assets", "media", "video", "image"}
    def walk(value, key=""):
        if isinstance(value, str) and (key in path_keys or key in containers):
            suffix = Path(urllib.parse.urlparse(value).path).suffix.lower()
            if suffix in (".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".m4a"):
                found.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, dict):
            for k, item in value.items():
                if k in containers or k in path_keys:
                    walk(item, k)
    walk(data)
    return list(dict.fromkeys(found))


def collect_media(job, response):
    directory = MEDIA / job["id"]
    directory.mkdir(exist_ok=True)
    candidates = find_media_candidates(response)
    if not candidates:
        raise StudioError("上游任务完成，但尚未找到可保存的媒体路径；可刷新已有任务，无需重新生成。", "OUTPUT_PENDING", 503)
    candidates.sort(key=lambda v: 1 if v.startswith(("http://", "https://")) else 0)
    saved, last_error = [], None
    for candidate in candidates:
        suffix = Path(urllib.parse.urlparse(candidate).path).suffix.lower()
        if job["kind"] == "video" and suffix not in (".mp4", ".mov", ".webm"):
            continue
        target = directory / (f"source{len(saved)+1}" + suffix)
        try:
            if candidate.startswith(("http://", "https://")):
                # URLs originate in the successful, authenticated generation response.
                with urllib.request.urlopen(candidate, timeout=90) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            elif Path(candidate).is_absolute() and Path(candidate).is_file():
                shutil.copy2(candidate, target)
            else:
                workspace = Path(GW.workspace()["dir"]).resolve()
                local = (workspace / candidate).resolve()
                if not local.is_relative_to(workspace) or not local.is_file():
                    raise ValueError("Design 输出路径不在工作区内，或文件尚未落盘")
                shutil.copy2(local, target)
            if target.stat().st_size < 100:
                raise ValueError("输出文件过小")
            saved.append(target)
            break
        except Exception as exc:
            last_error = str(exc)
            if target.exists():
                target.unlink()
    if not saved:
        raise StudioError("生成已完成，但媒体下载失败：" + str(last_error), "DOWNLOAD_FAILED", 502)
    source = saved[0]
    media = source
    meta = {"sourcePath": str(source), "sourceMediaUrl": "/media/" + source.relative_to(MEDIA).as_posix()}
    if job["kind"] == "video":
        ffmpeg = ffmpeg_binary("ffmpeg")
        if job.get("trimSeconds"):
            media = directory / "preview-1s.mp4" if job["trimSeconds"] == 1 else directory / "trimmed.mp4"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-ss", str(job.get("trimStartSeconds", 0)), "-t", str(job["trimSeconds"]), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(media)], check=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        thumb = directory / "poster.jpg"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-frames:v", "1", "-vf", "scale=640:-2", str(thumb)], check=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        meta["thumbnailUrl"] = "/media/" + thumb.relative_to(MEDIA).as_posix()
        try:
            info = subprocess.check_output([ffmpeg_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(media)], timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            meta["mediaInfo"] = json.loads(info)
            meta["hasAudio"] = any(s.get("codec_type") == "audio" for s in meta["mediaInfo"].get("streams", []))
        except Exception as exc:
            meta["probeWarning"] = str(exc)
    elif job["kind"] == "audio":
        meta.update(probe_media(source))
    meta.update(mediaPath=str(media), mediaUrl="/media/" + media.relative_to(MEDIA).as_posix())
    return meta


def attach_job_result(job):
    if job["kind"] == "audio":
        try:
            return register_audio_result(job)
        except Exception as exc:
            return update_job(job["id"], projectAttached=False, projectAttachError=str(exc))
    try:
        output_path = job.get('sourcePath') or job.get('mediaPath')
        if output_path and Path(output_path).is_file() and not job.get('assetId'):
            asset=WORKFLOW.import_asset({'id':'generated-'+job['id'],'path':output_path,
                'name':(job.get('shotId') or '生成')+' · '+('分镜图' if job['kind']=='image' else '视频源片'),
                'kind':job['kind'],'role':'storyboard' if job['kind']=='image' else 'result','reviewStatus':'candidate',
                'provenance':{'source':'minimax-design','jobId':job['id'],'modelId':job.get('modelId'),'shotId':job.get('shotId')}})
            job=update_job(job['id'],assetId=asset['id'])
        with LOCK:
            project = read_json(PROJECT)
            shot = next((s for s in project["shots"] if s["id"] == job["shotId"]), None)
            if shot:
                shot["review"] = {"status": "unreviewed", "notes": "新生成结果待审阅。", "scope": "media", "jobId": job["id"], "updatedAt": now()}
                shot.update({k: job[k] for k in ("mediaUrl", "thumbnailUrl") if k in job})
                shot.update(status="succeeded", lastJobId=job["id"], mediaKind=job["kind"])
                save_project(project)
        return update_job(job["id"], projectAttached=True, projectAttachError=None)
    except Exception as exc:
        return update_job(job["id"], projectAttached=False, projectAttachError=str(exc))


def refresh_job(jid):
    job = find_job(jid)
    if job["status"] == "succeeded":
        return job if job.get("projectAttached") else attach_job_result(job)
    if job.get("type") in {"voice_design", "voice_clone"} and job.get("vendorVoiceId") and job.get("upstream", {}).get("trial_audio_url"):
        try:
            media = collect_media(job, {"result": {"path": job["upstream"]["trial_audio_url"]}})
            completed = update_job(jid, status="succeeded", error=None, projectAttached=False, **media)
            return register_audio_result(completed)
        except Exception as exc:
            return update_job(jid, status="download_failed", error=str(exc))
    if not job.get("taskId"):
        return job
    try:
        result = GW.query(job["taskId"])
        replacement = result.get("task_id")
        if replacement and str(replacement) != job["taskId"]:
            job = update_job(jid, taskId=str(replacement))
        state = str(result.get("status", "")).lower()
        if not state and isinstance(result.get("data"), dict):
            state = str(result["data"].get("status", "")).lower()
        if state in {"failed", "error", "cancelled", "canceled"} or result.get("ok") is False:
            return update_job(jid, status="failed", error=str(result.get("error") or result.get("message") or state), upstream=result)
        if state in {"success", "succeeded", "completed", "complete", "done", "finished"}:
            update_job(jid, status="downloading", upstream=result)
            try:
                media = collect_media(job, result)
            except Exception as exc:
                return update_job(jid, status="download_failed", error=str(exc), upstream=result)
            job = update_job(jid, status="succeeded", error=None, upstream=result, projectAttached=False, **media)
            return attach_job_result(job)
        return update_job(jid, status="processing", upstreamStatus=state or "processing", upstream=result, lastPollAt=now(), pollError=None)
    except Exception as exc:
        return update_job(jid, pollError=str(exc)[:1500], lastPollAt=now())


def poll_worker():
    while not STOP.wait(8):
        for workspace in REGISTRY.all():
            with workspace_scope(workspace):
                with LOCK:
                    jobs = read_json(JOBS, [])
                for job in jobs:
                    if job.get("taskId") and (job["status"] in ("processing", "downloading") or (job["status"] == "succeeded" and not job.get("projectAttached"))):
                        schedule(job["id"], refresh_job)


class Handler(BaseHTTPRequestHandler):
    server_version = "StoryboardStudio/" + VERSION

    def log_message(self, fmt, *args):
        # Do not log request bodies, credentials or signed download URLs.
        print(f"{now()} {self.command} {urllib.parse.urlparse(self.path).path} {args[1] if len(args)>1 else ''}", flush=True)

    def check_origin(self, write=False):
        host = self.headers.get("Host", "")
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed_hosts:
            raise StudioError("仅接受本机地址。", "LOCAL_ONLY", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed_hosts}:
            raise StudioError("跨站请求被拒绝。", "ORIGIN_REJECTED", 403)
        is_document_navigation = not write and getattr(self, "command", "") == "GET" and urllib.parse.urlparse(getattr(self, "path", "")).path == "/" and self.headers.get("Sec-Fetch-Mode") == "navigate"
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not is_document_navigation:
            raise StudioError("跨站请求被拒绝。", "ORIGIN_REJECTED", 403)
        if write and self.headers.get("X-Studio-Request") != "1":
            raise StudioError("缺少 X-Studio-Request: 1。", "REQUEST_HEADER_REQUIRED", 403)

    def json_response(self, data, status=200):
        if getattr(self, "workspace", None) and not self.path.startswith("/api/editor"):
            data = scoped_urls(data, self.workspace.id)
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def read_body(self):
        size = int(self.headers.get("Content-Length", "0"))
        limit = 96 * 1024 * 1024 if urllib.parse.urlparse(self.path).path in ("/api/assets/upload", "/api/library/assets") else 16 * 1024 * 1024
        if size < 0 or size > limit:
            raise StudioError("请求体过大。", "BODY_TOO_LARGE", 413)
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise StudioError("需要 application/json 请求。", "CONTENT_TYPE_REQUIRED", 415)
        return json.loads(self.rfile.read(size).decode("utf-8-sig"))

    def guarded(self, method):
        try:
            self.check_origin(method != "GET")
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            media_route = re.fullmatch(r"/w/([A-Za-z0-9_-]+)/media/(.+)", path)
            wid = self.headers.get("X-Studio-Workspace") or urllib.parse.parse_qs(parsed.query).get("workspace", [None])[0]
            if media_route:
                if wid and wid != media_route[1]:
                    raise StudioError("媒体工作区与请求工作区不一致。", "WORKSPACE_MISMATCH", 409)
                wid, path = media_route[1], "/media/" + media_route[2]
            self.workspace = REGISTRY.get(wid)
            with workspace_scope(self.workspace):
                if method == "GET":
                    return self.get(path)
                body = self.read_body()
                return self.write_request(method, path, body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            status = getattr(exc, "status", getattr(exc, "status_code", 400 if isinstance(exc, (ValueError, StudioError)) else 502))
            if not isinstance(status, int) or not 400 <= status <= 599:
                status = 502
            self.json_response({"error": str(exc)[:2000], "code": str(getattr(exc, "code", "ERROR")), "detail": getattr(exc, "detail", None)}, status)

    def write_request(self, method, path, body):
        with LOCK:
            w=current_workspace(REGISTRY)
            if method=='PUT' and path=='/api/voice-lab/draft':return self.json_response(VOICE_LAB.save_draft(w,body))
            if method=='POST' and path=='/api/voice-lab/preview':
                take,fresh=VOICE_LAB.prepare(w,body)
                if fresh:LOCAL_POOL.submit(VOICE_LAB.execute,w,take['id'])
                return self.json_response(take,202 if fresh else 200)
            if method=='POST' and path=='/api/voice-lab/adopt':return self.json_response(VOICE_LAB.adopt(w,body,LIBRARY))
            if method=='POST' and path=='/api/voice-lab/cancel':return self.json_response(VOICE_LAB.cancel(w,body))
            if method=='POST' and path=='/api/library/people':return self.json_response(LIBRARY.person(body),201)
            if method=='POST' and path=='/api/library/assets':return self.json_response(LIBRARY.add(body),201)
            if method=='PATCH' and path.startswith('/api/library/assets/'):
                return self.json_response(LIBRARY.edit_asset(path.rsplit('/',1)[1],body))
            if method=='POST' and path=='/api/library/use':return self.json_response(LIBRARY.use(w,body),201)
            if method=='POST' and path=='/api/editor/open':return self.json_response(EDITOR.open(w))
            if method=='POST' and path=='/api/editor/import-assets':return self.json_response(EDITOR.import_assets(w,body.get('assetIds',[])))
            if method=='PUT' and path=='/api/editor/project':return self.json_response(EDITOR.save(w,body))
            if method=='POST' and path=='/api/editor/proxy':return self.json_response(EDITOR.prepare_proxy(w,body.get('assetId')))
            if method=='POST' and path=='/api/editor/recover':return self.json_response(scoped_urls(EDITOR.recover(w,body.get('name')),w.id))
            if method=='POST' and path=='/api/editor/process':
                spec,context=EDITOR.prepare_clip_source(w,body)
                job,fresh=LOCAL_JOBS.prepare(w,spec)
                if fresh:
                    job=LOCAL_JOBS.update(w,job['id'],editorClip=context)
                    LOCAL_POOL.submit(execute_local_job,w,job['id'])
                return self.json_response(scoped_urls(public_local_job(job),w.id),202 if fresh else 200)
            if method=='POST' and path=='/api/editor/captions/apply':return self.json_response(EDITOR.apply_captions(w,body,LOCAL_JOBS.get(w)))
            if method=='POST' and path=='/api/editor/captions':
                source=EDITOR.media_path(w,body.get('src'))
                asset=w.store.import_asset({'path':str(source),'name':'剪辑字幕识别音轨','role':'context'})
                job,fresh=LOCAL_JOBS.prepare(w,{'operation':'transcribe','assetId':asset['id'],'language':body.get('language','zh'),'device':'cpu','requestId':body.get('requestId')})
                if fresh:LOCAL_POOL.submit(execute_local_job,w,job['id'])
                return self.json_response(public_local_job(job),202 if fresh else 200)
            if method == "POST" and path == "/api/workspaces":
                return self.json_response(REGISTRY.create(body).summary(), 201)
            workspace_edit = re.fullmatch(r"/api/workspaces/([A-Za-z0-9_-]+)", path)
            if method == "PATCH" and workspace_edit:
                return self.json_response(REGISTRY.update(workspace_edit[1], body).summary())
            activation = re.fullmatch(r"/api/workspaces/([A-Za-z0-9_-]+)/activate", path)
            if method == "POST" and activation:
                return self.json_response(REGISTRY.activate(activation[1]).summary())
            if method == "POST" and path == "/api/script/import":
                project = read_json(PROJECT)
                if "revision" in body and body["revision"] != project["revision"]:
                    raise StudioError("项目已更新，请重新载入。", "REVISION_CONFLICT", 409)
                if not isinstance(body.get("text"), str):
                    raise StudioError("需要剧本原文text。")
                project["script"] = body["text"]
                project["scriptFilename"] = body.get("filename")
                return self.json_response(script_document(save_project(project)))
            if method == "POST" and path == "/api/shots":
                return self.json_response(create_shot(body), 201)
            if method == "POST" and path == "/api/shots/batch":
                return self.json_response(update_shots_batch(body))
            shot_route = re.fullmatch(r"/api/shots/([A-Za-z0-9_-]+)(/review)?", path)
            if shot_route and method in {"PATCH", "PUT"}:
                return self.json_response(update_shot(shot_route[1], body, review_only=bool(shot_route[2])))
            if method == "PUT" and path == "/api/project":
                return self.json_response(save_project(body))
            if method == "PUT" and path == "/api/workflow":
                return self.json_response(WORKFLOW.save(body))
            if method == "PUT" and path == "/api/direction":
                with LOCK:
                    return self.json_response(save_direction(DATA / "direction.json", body, read_json(PROJECT)))
            if method == "POST" and path == "/api/direction/audition":
                validate_direction(read_json(DATA / "direction.json", {}), read_json(PROJECT))
                return self.json_response({"spec": audition_spec(read_json(DATA / "direction.json", {}),
                    body.get("voiceId"), body.get("caseId"), body.get("mode", "design"), body.get("assetId")),
                    "willSubmitGeneration": False})
            if method == "POST" and path == "/api/direction/cast":
                with LOCK:
                    return self.json_response(select_cast(body))
            if method == 'POST' and path == '/api/direction/recheck':
                with LOCK:
                    project=read_json(PROJECT)
                    if body.get('reviewed') is not True or body.get('projectRevision')!=project['revision']:
                        raise StudioError('请先复核当前镜头；项目版本须一致。','REVISION_CONFLICT',409)
                    plan=read_json(DATA/'direction.json',None)
                    if not plan:return self.json_response({'updated':False,'reason':'当前项目尚未建立导演分析。'})
                    validate_direction(plan,project)
                    shot=next((s for s in project['shots'] if s['id']==body.get('shotId')),None)
                    row=next((r for r in plan['shots'] if r['shotId']==body.get('shotId')),None)
                    if not shot or not row:raise StudioError('该镜头缺少导演分析。')
                    row.update(sourceHash=shot_digest(shot),manualRecheck={'at':now(),'projectRevision':project['revision']})
                    report=coverage_report(plan,project,WORKFLOW.workflow(),WORKFLOW.assets())
                    checked=next(r for r in report['shots'] if r['shotId']==shot['id'])
                    if not checked['referenceReady']:raise StudioError('仍有参考缺口：'+'；'.join(checked['errors']))
                    return self.json_response(save_direction(DATA/'direction.json',plan,project))
            if method == 'POST' and path == '/api/generation/preflight':
                result,_=prepare_job({**body,'confirmed':True},validate_only=True)
                return self.json_response(result)
            if method == "POST" and path == "/api/assets/import":
                return self.json_response(WORKFLOW.import_asset(body), 201)
            if method == "POST" and path == "/api/assets/upload":
                return self.json_response(upload_asset(body), 201)
            if method == "POST" and path in {"/api/audio/voice-design", "/api/audio/speech", "/api/audio/reference-speech", "/api/audio/voice-clone"}:
                job, fresh = prepare_audio_job(body, {"voice-design":"voice_design","speech":"speech","reference-speech":"reference_speech","voice-clone":"voice_clone"}[path.rsplit('/',1)[1]])
                if fresh:
                    schedule(job["id"], submit_audio_job)
                return self.json_response(public_job(job), 202 if fresh else 200)
            if method == 'PUT' and path == '/api/timeline':
                with LOCK:return self.json_response(save_timeline(current_workspace(REGISTRY),body))
            if method == 'POST' and path == '/api/local-jobs':
                workspace=current_workspace(REGISTRY)
                job,fresh=LOCAL_JOBS.prepare(workspace,body)
                if fresh:LOCAL_POOL.submit(execute_local_job,workspace,job['id'])
                return self.json_response({k:v for k,v in job.items() if k not in {'payload','requestHash'}},202 if fresh else 200)
            if method == 'POST' and path == '/api/models/refresh':
                return self.json_response(models())
            if method == 'POST' and path == '/api/services/design/start':
                executable=Path(os.environ.get('LOCALAPPDATA',''))/'com.minimax.hub/current/MiniMax Design.exe'
                if not executable.is_file():raise StudioError('未找到本机MiniMax Design，请先打开已安装的客户端。','DESIGN_NOT_INSTALLED',409)
                subprocess.Popen([str(executable)],cwd=executable.parent,creationflags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
                return self.json_response({'launched':True,'message':'已打开MiniMax Design，请完成正常登录。启动客户端本身不提交生成。'})
            if method == 'POST' and path == '/api/timeline/import-subtitles':
                workspace=current_workspace(REGISTRY)
                with LOCK:
                    from editing import import_transcript
                    job=next((j for j in LOCAL_JOBS.get(workspace) if j['id']==body.get('jobId')),None)
                    return self.json_response(import_transcript(workspace,job,body.get('revision'),body.get('offset',0)))
            asset_match = re.fullmatch(r"/api/assets/([A-Za-z0-9_.:-]+)", path)
            if method == "PATCH" and asset_match:
                return self.json_response(WORKFLOW.patch_asset(asset_match[1], body))
            if method == "POST" and path in ("/api/workflow/validate", "/api/workflow/compile"):
                compiled = workflow_compile(body)
                return self.json_response(compiled)
            if method == "POST" and path == "/api/workflow/run":
                return self.json_response(run_workflow(body), 202)
            if method == "POST" and path == "/api/jobs":
                job, fresh = prepare_job(body)
                if fresh:
                    schedule(job["id"], submit_job)
                return self.json_response(public_job(job), 202 if fresh else 200)
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/refresh", path)
            if method == "POST" and match:
                job = find_job(match[1])
                schedule(job["id"], refresh_job)
                return self.json_response(public_job(job))
            raise StudioError("接口不存在。", "NOT_FOUND", 404)

    def do_GET(self):
        self.guarded("GET")

    def do_POST(self):
        self.guarded("POST")

    def do_PUT(self):
        self.guarded("PUT")

    def do_PATCH(self):
        self.guarded("PATCH")

    def get(self, path):
        w=current_workspace(REGISTRY)
        if path=='/api/voice-lab':return self.json_response(VOICE_LAB.listing(w))
        if path=='/api/library':return self.json_response(LIBRARY.listing())
        if path.startswith('/library/media/'):
            target=(LIBRARY.root/'media'/urllib.parse.unquote(path[len('/library/media/'):])).resolve()
            if not target.is_relative_to((LIBRARY.root/'media').resolve()):raise StudioError('媒体路径无效')
            return self.send_file(LIBRARY.root/'media',path[len('/library/media/'):])
        if path=='/api/editor/project':return self.json_response(EDITOR.project(w))
        if path=='/api/editor/proxies':return self.json_response({'jobs':[j for j in EDITOR.proxy_jobs.values() if j['workspaceId']==w.id]})

        if path == '/api/health':
            return self.json_response({'ok':True,'version':VERSION,'pid':os.getpid(),
                'appRoot':str(ROOT),'projectPath':str(PROJECT),'workspaceId':current_workspace(REGISTRY).id})
        if path == '/api/media':
            return self.json_response({'assets':media_catalog(current_workspace(REGISTRY))})
        if path == '/api/local-capabilities':
            return self.json_response(local_capabilities(ROOT))
        if path == '/api/timeline':
            workspace=current_workspace(REGISTRY)
            return self.json_response(read_json(workspace.data/'timeline.json',empty_timeline(workspace)))
        if path == '/api/local-jobs':
            return self.json_response({'jobs':[public_local_job(j) for j in LOCAL_JOBS.get(current_workspace(REGISTRY))]})
        if path == "/api/direction":
            return self.json_response(read_json(DATA / "direction.json", direction_scaffold(read_json(PROJECT))))
        if path == "/api/direction/report":
            return self.json_response(coverage_report(read_json(DATA / "direction.json", None), read_json(PROJECT), WORKFLOW.workflow(), WORKFLOW.assets()))
        if path == "/api/workspaces":
            return self.json_response(REGISTRY.listing())
        if path == "/api/script":
            return self.json_response(script_document(read_json(PROJECT)))
        review_route = re.fullmatch(r"/api/shots/([A-Za-z0-9_-]+)/review", path)
        if review_route:
            try:
                value = review_bundle(read_json(PROJECT), review_route[1], WORKFLOW.workflow(), WORKFLOW.assets(), workflow_compile())
                direction_report = coverage_report(read_json(DATA / "direction.json", None), read_json(PROJECT), WORKFLOW.workflow(), WORKFLOW.assets())
                value["direction"] = next((s for s in direction_report["shots"] if s["shotId"] == review_route[1]), None)
            except KeyError:
                raise StudioError("镜头不存在。", "SHOT_NOT_FOUND", 404)
            return self.json_response(value)
        if path == "/api/status":
            try:
                health = GW.health()
                online = health.get("status") == "ok"
                error = None
            except Exception as exc:
                online, error = False, str(exc)
            workspace = current_workspace(REGISTRY)
            return self.json_response({"online": online, "error": error, "gateway": getattr(GW, "base_url", "http://127.0.0.1:8001"), "version": VERSION, "projectPath": str(PROJECT), "workspaceId": workspace.id, "workspacePath": str(workspace.root), "appRoot": str(ROOT), "pid": os.getpid()})
        if path == "/api/models":
            catalog = offline_catalog()
            if catalog is None:
                shared=read_json(ROOT/'data/model-catalog.json',{})
                catalog={kind:shared.get(kind,[]) for kind in ('image','video','speech')}
            catalog['_meta']={'cached':True,'message':'本地模型目录；提交时校验在线模型。可手动刷新云端目录。'}
            return self.json_response(catalog)
        if path == "/api/workflow":
            return self.json_response(WORKFLOW.workflow())
        if path == "/api/assets":
            return self.json_response(WORKFLOW.assets())
        if path == "/api/production":
            with LOCK:
                project, jobs = read_json(PROJECT), read_json(JOBS, [])
            return self.json_response(WORKFLOW.production(project, catalogs=offline_catalog(), jobs=jobs))
        if path == "/api/project":
            with LOCK:
                return self.json_response(read_json(PROJECT))
        if path == "/api/jobs":
            with LOCK:
                return self.json_response({"jobs": [public_job(j) for j in read_json(JOBS, [])]})
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if match:
            return self.json_response(public_job(find_job(match[1])))
        if path.startswith("/media/"):
            return self.send_file(MEDIA, path[len("/media/"):])
        if path == "/":
            return self.send_file(ROOT / "web", "index.html")
        if path.startswith("/branding/"):
            return self.send_file(ROOT / "web" / "branding", path[len("/branding/"):])
        if path == "/favicon.ico":
            return self.send_file(ROOT / "web" / "branding", "dingzhen.ico")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/app.js", "/style.css", "/styles.css", "/graph.js", "/graph.css", "/creator.js", "/creator.css", "/direction-ui.js", "/direction.css", "/production.js", "/production.css", "/studio-v6.css", "/studio-v7.css", "/pro-editor.js", "/editor-tools.js", "/voice-lab.js", "/voice-lab.css", "/library.js", "/service-status.js", "/edit-preview.js", "/shots.html", "/favicon.svg"):
            return self.send_file(ROOT / "web", path[1:])
        raise StudioError("文件或接口不存在。", "NOT_FOUND", 404)

    def send_file(self, base, relative):
        target = (base / urllib.parse.unquote(relative)).resolve()
        if not target.is_relative_to(base.resolve()) or not target.is_file():
            raise StudioError("文件不存在。", "NOT_FOUND", 404)
        length = target.stat().st_size
        start, end, partial = 0, length - 1, False
        byte_range = self.headers.get("Range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
            if not match:
                raise StudioError("不支持的字节范围。", "RANGE_NOT_SATISFIABLE", 416)
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, end)
            if start > end:
                raise StudioError("字节范围超过文件大小。", "RANGE_NOT_SATISFIABLE", 416)
            partial = True
        self.send_response(206 if partial else 200)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{length}")
        self.end_headers()
        with target.open("rb") as stream:
            stream.seek(start)
            left = end - start + 1
            while left:
                chunk = stream.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def main():
    parser = argparse.ArgumentParser(description="MiniMax 本地分镜工作台")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    REGISTRY.persist()
    initialise()
    # A lost submission response is never resent on restart.
    for workspace in REGISTRY.all():
        with workspace_scope(workspace), LOCK:
            jobs = read_json(JOBS, [])
            for job in jobs:
                if job["status"] in ("queued", "submitting"):
                    job.update(status="unknown", error="服务重启时提交未完成。请核对原任务；未自动重新提交。")
            atomic_json(JOBS, jobs)
            VOICE_LAB.recover_interrupted(workspace)
            local_jobs=LOCAL_JOBS.get(workspace)
            for job in local_jobs:
                if job.get('status') in {'queued','running'}:
                    job.update(status='failed',error='服务在本地处理期间重启。原素材保留，可重新发起本地任务。',updatedAt=now())
            if local_jobs:atomic_json(workspace.data/'local-jobs.json',local_jobs)
    threading.Thread(target=poll_worker, daemon=True, name="poller").start()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Studio ready: http://127.0.0.1:{args.port} | {PROJECT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        httpd.server_close()
        POOL.shutdown(wait=False)


if __name__ == "__main__":
    main()
