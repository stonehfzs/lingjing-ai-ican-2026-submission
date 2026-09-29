"""Typed film workflow graph, asset registry and deterministic task compiler."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
from media_probe import probe as probe_media
from multimodal import compile_references, capabilities as reference_capabilities
from creator import shot_readiness
from gateway import Gateway


class WorkflowError(ValueError):
    def __init__(self, message, code="INVALID_WORKFLOW", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else copy.deepcopy(default)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


OUTPUTS = {"asset": {"image": "image", "audio": "audio", "video": "video", "text": "text"},
    "prompt": {"text": "text"}, "script": {"text": "text"}, "image": {"image": "image"},
    "video": {"video": "video"}, "review": {"out": "media"}, "reuse": {"video": "video"}, "edit": {"video": "video"}}
INPUTS = {"asset": {}, "prompt": {"context": {"text"}}, "script": {},
    "image": {"prompt": {"text"}, "references": {"image"}},
    "video": {"prompt": {"text"}, "references": {"image"}, "firstFrame": {"image"}, "audioReferences": {"audio"}, "videoReferences": {"video"}, "audioPlan": {"audio"}},
    "review": {"media": {"image", "video", "audio", "media"}},
    "reuse": {"source": {"video", "media"}, "audioPlan": {"audio"}}, "edit": {"source": {"video", "media"}, "text": {"text"}, "audioPlan": {"audio"}}}
ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")


def validate_structure(graph):
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise WorkflowError("工作流需要 nodes 和 edges 数组。")
    if len(graph["nodes"]) > 3000 or len(graph["edges"]) > 12000:
        raise WorkflowError("工作流规模超过当前版本限制。")
    nodes, edge_ids, incoming = {}, set(), {}
    for node in graph["nodes"]:
        nid = node.get("id")
        if not isinstance(nid, str) or not ID_PATTERN.fullmatch(nid) or nid in nodes:
            raise WorkflowError("节点 ID 无效或重复。")
        if node.get("type") not in OUTPUTS:
            raise WorkflowError(f"节点 {nid} 的类型不支持。")
        pos = node.get("position", {})
        if any(isinstance(pos.get(k), bool) or not isinstance(pos.get(k), (int, float)) or not -1000000 <= pos[k] <= 1000000 for k in ("x", "y")):
            raise WorkflowError(f"节点 {nid} 坐标无效。")
        if not isinstance(node.get("data", {}), dict):
            raise WorkflowError(f"节点 {nid} 的 data 必须为对象。")
        nodes[nid] = node
        incoming[nid] = []
    linked = set()
    counts = {}
    for edge in graph["edges"]:
        eid = edge.get("id")
        if not isinstance(eid, str) or not ID_PATTERN.fullmatch(eid) or eid in edge_ids:
            raise WorkflowError("连接 ID 无效或重复。")
        edge_ids.add(eid)
        src, dst = edge.get("source"), edge.get("target")
        if src not in nodes or dst not in nodes or src == dst:
            raise WorkflowError("连接必须指向存在的两个不同节点。")
        sp, tp = edge.get("sourcePort"), edge.get("targetPort")
        out_type = OUTPUTS[nodes[src]["type"]].get(sp)
        allowed = INPUTS[nodes[dst]["type"]].get(tp, set())
        if not out_type or out_type not in allowed:
            raise WorkflowError(f"端口不兼容：{src}.{sp} → {dst}.{tp}。", "PORT_TYPE_MISMATCH")
        key = (src, sp, dst, tp)
        if key in linked:
            raise WorkflowError("不能重复连接同一对端口。")
        linked.add(key)
        count_key = (dst, tp)
        counts[count_key] = counts.get(count_key, 0) + 1
        if tp in {"prompt", "firstFrame", "media", "source"} and nodes[dst]["type"] != "edit" and counts[count_key] > 1:
            raise WorkflowError(f"{dst}.{tp} 只接受一个输入。")
        incoming[dst].append(src)
    visited, visiting = set(), set()
    def visit(nid):
        if nid in visiting:
            raise WorkflowError("工作流不能包含循环依赖。", "CYCLE_DETECTED")
        if nid in visited:
            return
        visiting.add(nid)
        for upstream in incoming[nid]:
            visit(upstream)
        visiting.remove(nid)
        visited.add(nid)
    for nid in nodes:
        visit(nid)
    return nodes


class WorkflowStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = self.root / "data"
        self.media = self.root / "media"
        self.workflow_path = self.data / "workflow.json"
        self.assets_path = self.data / "assets.json"
        self.lock = threading.RLock()

    def workflow(self):
        return load(self.workflow_path, {"version": 1, "revision": 0, "nodes": [], "edges": []})

    def save(self, graph):
        validate_structure(graph)
        with self.lock:
            current = self.workflow()
            if graph.get("revision") != current["revision"]:
                raise WorkflowError("工作流已更新，请重新载入后合并。", "REVISION_CONFLICT", 409)
            if current["nodes"]:
                write(self.data / "workflow-snapshots" / f"r{current['revision']}-{uuid.uuid4().hex[:8]}.json", current)
            result = copy.deepcopy(graph)
            result.update(version=1, revision=current["revision"] + 1, updatedAt=timestamp())
            write(self.workflow_path, result)
            return result

    def assets(self):
        return load(self.assets_path, {"revision": 0, "assets": []})

    def import_asset(self, spec):
        raw = spec.get("path")
        if not isinstance(raw, str) or not raw or raw.startswith(("http:", "https:")):
            raise WorkflowError("请提供本地资产的绝对路径。")
        path = Path(raw).resolve()
        if not Path(raw).is_absolute() or not path.is_file():
            raise WorkflowError("资产文件不存在。", "ASSET_NOT_FOUND", 404)
        suffix = path.suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".m4a", ".txt", ".md"}
        if suffix not in allowed:
            raise WorkflowError("当前支持图片、视频、音频和纯文本资产。")
        if path.stat().st_size > 1024 ** 3:
            raise WorkflowError("单资产超过1GB，请使用外部素材管理。")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        aid = spec.get("id") or "asset-" + digest[:16]
        if not isinstance(aid, str) or not ID_PATTERN.fullmatch(aid):
            raise WorkflowError("资产 ID 无效。")
        destination = self.media / "assets" / (digest[:20] + suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        media_type = "image" if suffix in {".png", ".jpg", ".jpeg", ".webp"} else "video" if suffix in {".mp4", ".mov", ".webm"} else "text" if suffix in {".txt", ".md"} else "audio"
        review = spec.get("reviewStatus", "candidate")
        if review not in {"ready", "candidate", "blocked", "missing"}:
            raise WorkflowError("reviewStatus 无效。")
        asset = {"id": aid, "name": spec.get("name") or path.stem, "kind": spec.get("kind") or media_type,
            "mediaType": media_type, "role": spec.get("role", "reference"), "path": str(destination),
            "mediaUrl": "/media/" + destination.relative_to(self.media).as_posix(),
            "thumbnailUrl": "/media/" + destination.relative_to(self.media).as_posix() if media_type == "image" else None,
            "status": "available", "reviewStatus": review, "sha256": digest, "size": destination.stat().st_size,
            "notes": spec.get("notes", ""), "provenance": spec.get("provenance") or {"source": "local-import", "originalPath": str(path)},
            "createdAt": timestamp(), "updatedAt": timestamp()}
        for key in ("characterId", "appearanceId", "environmentId", "sourceAssetId", "logicalRoles", "reviewNotes", "vendorVoiceId", "voiceId", "spokenText", "cueIds", "language", "description"):
            if key in spec:
                asset[key] = spec[key]
        if media_type in {"audio", "video"}:
            asset.update(probe_media(destination, waveform=media_type == "audio"))
        with self.lock:
            document = self.assets()
            old = next((a for a in document["assets"] if a["id"] == aid), None)
            if old:
                if old.get("status") == "missing" and not old.get("path"):
                    old.update(asset)
                    document["revision"] += 1
                    write(self.assets_path, document)
                    return old
                if old.get("sha256") != digest:
                    raise WorkflowError("同一资产ID已对应另一文件；请使用新版本ID。", "ASSET_ID_CONFLICT", 409)
                return old
            document["assets"].append(asset)
            document["revision"] += 1
            write(self.assets_path, document)
        return asset

    def patch_asset(self, aid, patch):
        allowed = {"name", "role", "reviewStatus", "notes", "reviewNotes", "logicalRoles", "characterId", "appearanceId", "environmentId", "voiceId", "vendorVoiceId", "spokenText", "cueIds", "description"}
        if set(patch) - allowed:
            raise WorkflowError("资产更新包含不可修改字段。")
        if "reviewStatus" in patch and patch["reviewStatus"] not in {"ready", "candidate", "blocked", "missing"}:
            raise WorkflowError("reviewStatus 无效。")
        with self.lock:
            document = self.assets()
            row = next((a for a in document["assets"] if a["id"] == aid), None)
            if row is None:
                raise WorkflowError("未找到资产。", "ASSET_NOT_FOUND", 404)
            row.update(copy.deepcopy(patch), updatedAt=timestamp())
            document["revision"] += 1
            write(self.assets_path, document)
            return row

    def compile(self, node_ids=None, catalogs=None, jobs=None):
        graph = self.workflow()
        nodes = validate_structure(graph)
        assets = {a["id"]: a for a in self.assets()["assets"]}
        incoming = {nid: [] for nid in nodes}
        for edge in graph["edges"]:
            incoming[edge["target"]].append(edge)
        if node_ids is not None and (not isinstance(node_ids, list) or any(n not in nodes for n in node_ids)):
            raise WorkflowError("选择包含不存在的节点。")
        selected = node_ids if node_ids is not None else [n for n, node in nodes.items() if node["type"] in {"image", "video", "reuse", "edit"}]
        memo, task_map = {}, {}
        all_jobs = jobs or []

        def resolve(nid):
            if nid in memo:
                return memo[nid]
            node = nodes[nid]
            data = node.get("data", {})
            kind = node["type"]
            status = {"id": nid, "status": "ready", "missing": [], "warnings": [], "outputs": []}
            upstream = [(e, resolve(e["source"])) for e in incoming[nid]]
            if kind == "asset":
                aid = node.get("assetId") or data.get("assetId")
                asset = assets.get(aid)
                if not asset or not asset.get("path") or not Path(asset["path"]).is_file():
                    status["missing"].append("资产文件尚未准备：" + str(aid))
                elif asset.get("reviewStatus") != "ready":
                    status["missing"].append("资产尚未通过用途检查：" + asset.get("name", aid))
                else:
                    status["outputs"] = [{"type": asset.get("mediaType", "image"), "assetId": aid, "path": asset["path"], "role": asset.get("role"), "logicalRoles": asset.get("logicalRoles", []), "mediaUrl": asset.get("mediaUrl")}]
                    if asset.get("mediaType") == "text":
                        if Path(asset["path"]).stat().st_size > 65536:
                            status["missing"].append("文字资产超过64KB，请拆分为明确规格")
                        else:
                            status["text"] = Path(asset["path"]).read_text(encoding="utf-8-sig")
                            status["outputs"][0]["text"] = status["text"]
            elif kind in {"prompt", "script"}:
                text = data.get("text") or data.get("prompt", "")
                if not isinstance(text, str) or not text.strip():
                    status["missing"].append("提示词为空")
                else:
                    contexts = []
                    for edge, state in upstream:
                        if edge["targetPort"] == "context":
                            if state["status"] not in {"ready", "complete"} or not state.get("text"):
                                status["missing"].append("上游文字规格不可用：" + edge["source"])
                            else:
                                contexts.append(state["text"])
                    status["text"] = text + ("\n\n连接的制作规格（仅用于本镜可见物件与连续性）：\n" + "\n\n".join(contexts) if contexts else "")
            elif kind in {"video", "image"}:
                prompt_nodes = [s for e, s in upstream if e["targetPort"] == "prompt"]
                prompt = prompt_nodes[0].get("text", "") if prompt_nodes else data.get("prompt", "")
                if prompt_nodes and prompt_nodes[0]["status"] not in {"ready", "complete"}:
                    status["missing"].append("上游提示词或文字规格依赖尚未就绪")
                if not prompt.strip():
                    status["missing"].append("缺少提示词连接或节点提示词")
                refs, ref_ids, roles, first_frames = [], [], set(), []
                reference_assets = dict(assets)
                bindings = copy.deepcopy(data.get("bindings", []))
                known_bindings = {b.get("assetId"): b for b in bindings}
                for edge, state in upstream:
                    if edge["targetPort"] in {"references", "firstFrame", "audioReferences", "videoReferences", "audioPlan"}:
                        source_node = nodes[edge["source"]]
                        aid = source_node.get("assetId") or source_node.get("data", {}).get("assetId")
                        if not aid and state.get("outputs"):
                            output = state["outputs"][0]
                            aid = "node:" + edge["source"]
                            reference_assets[aid] = {"id": aid, "name": source_node.get("label", aid), "mediaType": output["type"], "path": output["path"], "role": "reference", "reviewStatus": "ready"}
                        if aid and aid not in known_bindings:
                            asset = reference_assets.get(aid, {})
                            role = edge.get("role") or source_node.get("data", {}).get("role") or asset.get("role", "reference")
                            usage = edge.get("usage") or source_node.get("data", {}).get("usage") or ("voice_identity" if role == "voice_identity" else "post_mix" if role in {"music", "ambience", "sfx"} else "model_reference")
                            binding = {"assetId": aid, "alias": source_node.get("data", {}).get("alias") or asset.get("name", aid), "role": role, "usage": usage, "enabled": True}
                            bindings.append(binding)
                            known_bindings[aid] = binding
                    if edge["targetPort"] in {"references", "firstFrame"}:
                        if state["status"] not in {"ready", "complete"} or not state.get("outputs"):
                            status["missing"].append("上游参考尚未完成：" + nodes[edge["source"]].get("label", edge["source"]))
                        for output in state["outputs"]:
                            if output["type"] != "image":
                                status["missing"].append("参考端口需要图片")
                                continue
                            if output["path"] not in refs:
                                refs.append(output["path"])
                            ref_ids.append(output.get("assetId"))
                            roles.update(output.get("logicalRoles", []))
                            if edge["targetPort"] == "firstFrame":
                                first_frames.append(output)
                # Explicit v0.3 bindings are editable creator choices. Legacy fixed requirements remain for old graphs.
                for aid in (data.get("requiredAssetIds", []) if "bindings" not in data else []):
                    if aid not in ref_ids:
                        status["missing"].append("缺少必需资产连接：" + aid)
                for role in data.get("requiredRoles", []):
                    if role not in roles:
                        status["missing"].append("缺少参考用途：" + role)
                params = copy.deepcopy(data.get("params", {}))
                if first_frames:
                    if len(first_frames) != 1 or len(refs) != 1:
                        status["missing"].append("首帧模式目前要求单一首帧输入，不能同时混用身份参考")
                    if first_frames[0].get("role") in {"identity", "costume"}:
                        status["missing"].append("人物身份图不能直接当作分镜首帧")
                    params["image_mode"] = "first-last-frame"
                provider = data.get("provider", "minimax-design")
                model_id = data.get("modelId")
                if provider == "minimax-design":
                    if not model_id:
                        status["missing"].append("未选择模型")
                    entries = (catalogs or {}).get(kind, [])
                    model = next((m for m in entries if m.get("id") == model_id), None)
                    if catalogs is not None and model is None:
                        status["missing"].append("当前模型目录没有此模型：" + str(model_id))
                    if model:
                        params = {**{k: str(v["default"]) for k, v in model.get("params", {}).items() if "default" in v}, **params}
                        if len(refs) > model.get("max_refs", 0):
                            status["missing"].append("参考图超过模型容量")
                        for key, value in params.items():
                            spec = model.get("params", {}).get(key)
                            if spec is None:
                                status["missing"].append("模型不支持参数：" + key)
                            elif spec.get("options") and str(value) not in [str(v) for v in spec["options"]]:
                                status["missing"].append(f"模型不支持 {key}={value}")
                            elif spec.get("type") == "slider":
                                try:
                                    number = float(value)
                                    if not spec.get("min", float('-inf')) <= number <= spec.get("max", float('inf')):
                                        raise ValueError()
                                except (ValueError, TypeError):
                                    status["missing"].append(f"参数 {key} 超出范围")
                        for constraint in model.get("paramConstraints", []):
                            condition, disabled = constraint.get("if", {}), constraint.get("disable", {})
                            if params.get(condition.get("param")) == condition.get("eq") and params.get(disabled.get("param")) in disabled.get("options", []):
                                status["missing"].append("生成方式不支持参数组合：" + str(disabled.get("param")))
                        if model.get("referenceImageRequired") and not refs:
                            status["missing"].append("模型需要参考图片")
                        if len(prompt) > model.get("promptMaxLength", 20000):
                            status["missing"].append("提示词超过模型长度限制")
                    elif catalogs is None:
                        status["warnings"].append("离线编译：运行前必须验证实时模型参数")
                elif provider == "imagegen" and kind == "image":
                    status["status"] = "external"
                    status["warnings"].append("交给 Codex 调用 imagegen，生成后注册图片资产")
                else:
                    status["missing"].append("不支持的生成服务")
                negative = data.get("negativePrompt", "")
                if negative:
                    prompt += "\n避免：" + negative
                authored_prompt = prompt
                multimodal = {"audioPaths": [], "videoPaths": [], "paramsPatch": {}, "referenceBindings": bindings, "errors": [], "warnings": []}
                multimodal_needed = any(reference_assets.get(b.get("assetId"), {}).get("mediaType") in {"audio", "video"} and b.get("usage", "model_reference") == "model_reference" for b in bindings)
                verified_adapter = bool(provider == "minimax-design" and model and reference_capabilities(model)["adapterSupported"])
                if kind == "video" and provider == "minimax-design" and model and not first_frames and (verified_adapter or multimodal_needed or "@{" in prompt):
                    multimodal = compile_references(model, prompt, bindings, reference_assets, params=params)
                    status["missing"].extend(multimodal["errors"])
                    status["warnings"].extend(multimodal["warnings"])
                    refs = multimodal["imagePaths"]
                    ref_ids = [b["assetId"] for b in multimodal["referenceBindings"] if b.get("included") and b.get("mediaType") == "image"]
                    prompt = multimodal["prompt"]
                    for binding in multimodal["referenceBindings"]:
                        if binding.get("included") and not Path(binding["path"]).is_file():
                            status["missing"].append("引用文件已不存在：" + binding["assetId"])
                elif "@{" in prompt:
                    # Image services and first-frame prompts use descriptive numbered images, not hidden provider tokens.
                    tokens = {aid: f"参考图{i+1}" for i, aid in enumerate(ref_ids) if aid}
                    def substitute(match):
                        aid = match.group(1)
                        if aid not in tokens:
                            status["missing"].append("未连接或不可发送的提示词引用：" + aid)
                            return match.group(0)
                        return tokens[aid]
                    prompt = re.sub(r"@\{([^{}\r\n]+)\}", substitute, prompt)
                if first_frames and multimodal_needed:
                    status["missing"].append("首帧模式不可同时使用音频/视频多模态参考")
                if provider == "minimax-design" and model and len(prompt) > model.get("promptMaxLength", 20000):
                    status["missing"].append("完整提示词（含避免项）超过模型长度限制")
                if provider == "minimax-design" and model and model.get("backend") and not status["missing"]:
                    try:
                        Gateway.build_body(kind, model, prompt, {**params, **multimodal["paramsPatch"]}, refs, "preflight.mp4" if kind == "video" else "preflight.png")
                    except Exception as exc:
                        status["missing"].append("生成合同未通过：" + str(exc))
                task = {"nodeId": nid, "shotId": node.get("shotId"), "kind": kind, "provider": provider,
                    "modelId": model_id, "params": params, "prompt": prompt, "imagePaths": refs,
                    "authoredPrompt": authored_prompt, "bindings": bindings, "referenceBindings": multimodal["referenceBindings"],
                    "audioPaths": multimodal["audioPaths"], "videoPaths": multimodal["videoPaths"], "paramsPatch": multimodal["paramsPatch"],
                    "audioPlan": data.get("audioPlan", {}),
                    "assetIds": ref_ids, "issues": status["missing"], "warnings": status["warnings"],
                    "trimSeconds": data.get("trimSeconds"), "trimStartSeconds": data.get("trimStartSeconds", 0), "external": provider == "imagegen"}
                # Completion is tied to the exact current compiled inputs, not merely shot id.
                signature = hashlib.sha256(json.dumps({k: task[k] for k in ("nodeId", "kind", "provider", "modelId", "params", "prompt", "imagePaths", "audioPaths", "videoPaths", "paramsPatch", "trimSeconds", "trimStartSeconds")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                task["signature"] = signature
                matching = [j for j in all_jobs if j.get("workflowSignature") == signature and j.get("status") == "succeeded"]
                if matching:
                    latest = matching[-1]
                    path = latest.get("mediaPath")
                    if path and Path(path).is_file():
                        status["status"] = "complete"
                        status["outputs"] = [{"type": kind, "path": path, "sourcePath": latest.get("sourcePath", path), "mediaUrl": latest.get("mediaUrl"), "thumbnailUrl": latest.get("thumbnailUrl")}]
                        status["jobId"] = latest["id"]
                        status["hasAudio"] = latest.get("hasAudio", False)
                task_map[nid] = task
            elif kind in {"reuse", "edit", "review"}:
                sources = [s for e, s in upstream if e["targetPort"] in {"source", "media"}]
                media = [o for s in sources for o in s.get("outputs", [])]
                if not sources:
                    status["missing"].append("未连接来源素材")
                elif not media:
                    status["status"] = "waiting"
                    status["warnings"].append("等待上游视频完成；不重复付费生成")
                else:
                    if kind == "review" and not data.get("approved"):
                        status["status"] = "waiting"
                        status["warnings"].append("已有媒体待内容审核，审核节点尚未放行")
                    elif kind in {"reuse", "edit"} and ("sourceStartSeconds" in data or "sourceDurationSeconds" in data):
                        status["status"] = "ready"
                        status["postprocessPlan"] = {"sourcePath": media[0].get("sourcePath", media[0]["path"]),
                            "sourceStartSeconds": data.get("sourceStartSeconds", 0), "sourceDurationSeconds": data.get("sourceDurationSeconds"),
                            "plannedDuration": data.get("plannedDuration"), "instructions": data.get("text", ""), "executed": False}
                        status["warnings"].append("原始源片已可用；此节点的裁切/后期计划尚未执行")
                    else:
                        status["outputs"] = media
                        status["status"] = "complete" if kind == "review" else "ready"
                if kind == "edit":
                    status["warnings"].append("后期节点保存剪辑说明；文字/剪辑由后期工具执行")
            if status["missing"]:
                status["status"] = "blocked"
            status["missing"] = list(dict.fromkeys(status["missing"]))
            memo[nid] = status
            return status

        for nid in nodes:
            resolve(nid)
        statuses = [memo[nid] for nid in nodes]
        tasks = []
        errors = []
        warnings = []
        for nid in selected:
            row = memo[nid]
            errors.extend(f"{nid}: {m}" for m in row["missing"])
            warnings.extend(f"{nid}: {w}" for w in row["warnings"])
            if nid in task_map:
                task = task_map[nid]
                task["ready"] = row["status"] == "ready" and not task["external"]
                task["status"] = row["status"]
                tasks.append(task)
        return {"ready": bool(selected) and not errors and all(memo[nid]["status"] in {"ready", "complete"} for nid in selected) and all(t["ready"] or t["status"] == "complete" for t in tasks),
            "valid": not errors, "errors": errors, "warnings": warnings, "tasks": tasks, "nodes": statuses,
            "revision": graph["revision"], "postprocessPlans": [{"nodeId": s["id"], **s["postprocessPlan"]} for s in statuses if s.get("postprocessPlan")], "summary": {"nodes": len(nodes), "edges": len(graph["edges"]),
                "ready": sum(s["status"] == "ready" for s in statuses), "blocked": sum(s["status"] == "blocked" for s in statuses),
                "waiting": sum(s["status"] == "waiting" for s in statuses), "external": sum(s["status"] == "external" for s in statuses)}}

    def production(self, project, catalogs=None, jobs=None):
        compiled = self.compile(catalogs=catalogs, jobs=jobs)
        states = {n["id"]: n for n in compiled["nodes"]}
        graph = self.workflow()
        tasks = {t["nodeId"]: t for t in compiled["tasks"]}
        asset_map = {a["id"]: a for a in self.assets()["assets"]}
        shots = []
        for shot in project.get("shots", []):
            candidates = [n for n in graph["nodes"] if n.get("shotId") == shot["id"] and n["type"] in {"video", "reuse", "edit"}]
            node = candidates[0] if candidates else None
            state = states.get(node["id"], {}) if node else {}
            task = tasks.get(node["id"], {}) if node else {}
            readiness = shot_readiness(shot, state, (node or {}).get("data", {}).get("bindings", shot.get("bindings", [])), asset_map, node)
            shots.append({"shotId": shot["id"], "title": shot.get("title"), "scene": shot.get("scene", ""),
                "sceneId": shot.get("sourceData", {}).get("scene_id", shot.get("sceneId")),
                "plannedDuration": shot.get("plannedDuration"), "nodeId": node["id"] if node else None,
                "readiness": state.get("status", "blocked"), "status": state.get("status", "blocked"),
                "generationMode": node["type"] if node else None, "missing": state.get("missing", ["未编排工作流"]),
                "warnings": state.get("warnings", []), "assetIds": task.get("assetIds", []), **readiness})
        return {"summary": {"shots": len(shots), "assets": len(self.assets()["assets"]),
            "ready": sum(s["readiness"] == "ready" for s in shots), "visualReady": sum(s["visualReady"] for s in shots), "audioReady": sum(s["audioReady"] for s in shots), "deliveryReady": sum(s["deliveryReady"] for s in shots), "blocked": sum(s["readiness"] == "blocked" for s in shots),
            "waiting": sum(s["readiness"] == "waiting" for s in shots), "complete": sum(s["readiness"] == "complete" for s in shots)},
            "shots": shots, "workflowRevision": compiled["revision"]}
