"""Codex-friendly CLI for the local storyboard studio; always emits JSON."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("STORYBOARD_STUDIO_URL", "http://127.0.0.1:8766").rstrip("/")
WORKSPACE_ID = os.environ.get("STUDIO_WORKSPACE_ID")


def request(path, method="GET", body=None, workspace=None):
    url = urllib.parse.urlparse(BASE)
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("STORYBOARD_STUDIO_URL must be a loopback HTTP URL")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Studio-Request": "1"}
    if workspace or WORKSPACE_ID:
        headers["X-Studio-Workspace"] = workspace or WORKSPACE_ID
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=45) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except ValueError:
            payload = {"error": str(exc), "code": "HTTP_ERROR"}
        print(json.dumps(payload, ensure_ascii=False))
        raise SystemExit(1)


def main():
    global WORKSPACE_ID
    parser = argparse.ArgumentParser(description="Codex 分镜画布：剧本、镜头、模型和生成任务。输出始终是 JSON。")
    parser.add_argument("--workspace", help="明确目标workspace ID，不依赖其他窗口当前选择")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="检查画布服务和 MiniMax 网关")
    commands.add_parser("models", help="读取实时模型及参数")
    commands.add_parser("project", help="读取完整项目（含 revision）")
    commands.add_parser("script", help="读取剧本原文、段落与镜头关联")
    direction_cmd = commands.add_parser("direction", help="导演分析、参考覆盖、角色选音；不调用生成")
    direction_sub = direction_cmd.add_subparsers(dest="direction_command", required=True)
    direction_sub.add_parser("get")
    direction_report = direction_sub.add_parser("report")
    direction_report.add_argument("--all", action="store_true")
    direction_save = direction_sub.add_parser("apply")
    direction_save.add_argument("file", type=Path)
    direction_save.add_argument("--dry-run", action="store_true")
    direction_audition = direction_sub.add_parser("audition")
    direction_audition.add_argument("voice")
    direction_audition.add_argument("case")
    direction_audition.add_argument("--mode", choices=["design", "speech"], default="design")
    direction_audition.add_argument("--asset")
    review_command = commands.add_parser("review", help="读取或更新一个镜头的审阅状态")
    review_command.add_argument("id")
    review_command.add_argument("--status", choices=["approved", "changes_requested", "unreviewed"])
    review_command.add_argument("--notes", default="")
    audio_command = commands.add_parser("audio", help="按明确授权设计音色或合成逐句表演")
    audio_commands = audio_command.add_subparsers(dest="audio_command", required=True)
    for audio_action in ("design", "speech", "reference", "clone"):
        audio_parser = audio_commands.add_parser(audio_action)
        audio_parser.add_argument("spec", type=Path, help="任务JSON文件，字段见API.md")
        audio_parser.add_argument("--request-id", required=True)
        audio_parser.add_argument("--confirm", action="store_true")
        audio_parser.add_argument("--dry-run", action="store_true")
    workspace_cmd = commands.add_parser("workspaces", help="项目workspace管理")
    workspace_sub = workspace_cmd.add_subparsers(dest="workspace_command", required=True)
    workspace_sub.add_parser("list")
    workspace_create = workspace_sub.add_parser("create")
    workspace_create.add_argument("name")
    workspace_create.add_argument("--path")
    workspace_create.add_argument("--script", type=Path)
    workspace_create.add_argument("--model")
    workspace_activate = workspace_sub.add_parser("activate")
    workspace_activate.add_argument("id")
    workspace_archive = workspace_sub.add_parser("archive")
    workspace_archive.add_argument("id")
    workspace_restore = workspace_sub.add_parser("restore")
    workspace_restore.add_argument("id")
    commands.add_parser("shots", help="列出镜头摘要")
    commands.add_parser("media", help="当前项目的可用媒体和生成源片")
    commands.add_parser("local-tools", help="本机FFmpeg与Demucs状态")
    commands.add_parser("local-jobs", help="本机处理任务")
    timeline_cmd=commands.add_parser("timeline",help="非破坏性剪辑时间线")
    timeline_sub=timeline_cmd.add_subparsers(dest="timeline_command",required=True)
    timeline_sub.add_parser("get")
    timeline_apply=timeline_sub.add_parser("apply");timeline_apply.add_argument("file",type=Path)
    local_cmd=commands.add_parser("local-run",help="执行本机音轨提取、分离或剪辑渲染")
    local_cmd.add_argument("spec",type=Path);local_cmd.add_argument("--request-id",required=True)
    shot = commands.add_parser("shot", help="读取指定镜头")
    shot.add_argument("id")
    apply = commands.add_parser("apply", help="保存完整项目 JSON，要求 revision 与服务一致")
    apply.add_argument("file", type=Path)
    apply.add_argument("--dry-run", action="store_true")
    imp = commands.add_parser("import-script", help="导入剧本原文；不调用模型、不声称自动完成分镜")
    imp.add_argument("file", type=Path)
    imp.add_argument("--title")
    jobs = commands.add_parser("jobs", help="列出生成任务")
    job = commands.add_parser("job", help="读取或刷新一个已有任务，不重新生成")
    job.add_argument("id")
    job.add_argument("--refresh", action="store_true")
    gen = commands.add_parser("generate", help="生成一个镜头；付费提交需 --confirm")
    gen.add_argument("--shot", required=True)
    gen.add_argument("--kind", choices=["image", "video"])
    gen.add_argument("--model")
    gen.add_argument("--prompt-file", type=Path)
    gen.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    gen.add_argument("--trim", type=float)
    gen.add_argument("--request-id", help="同一次提交重用此ID，以免网络重试重复计费")
    gen.add_argument("--no-reference", action="store_true")
    gen.add_argument("--confirm", action="store_true")
    gen.add_argument("--dry-run", action="store_true")
    raw = commands.add_parser("request", help="只读接口查询（GET）")
    raw.add_argument("path")
    workflow = commands.add_parser("workflow", help="读取、编辑、检查和执行节点图工作流")
    workflow_commands = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_commands.add_parser("get", help="读取当前工作流")
    workflow_apply = workflow_commands.add_parser("apply", help="保存完整工作流 JSON")
    workflow_apply.add_argument("file", type=Path)
    workflow_apply.add_argument("--dry-run", action="store_true", help="只在本地检查结构，不保存")
    for action in ("validate", "compile"):
        sub = workflow_commands.add_parser(action, help="验证工作流" if action == "validate" else "离线编译选定节点")
        sub.add_argument("--node", action="append", dest="nodes", help="节点 ID；可重复指定")
    workflow_run = workflow_commands.add_parser("run", help="提交明确选择的工作流节点（会消耗积分）")
    workflow_run.add_argument("--node", action="append", dest="nodes", required=True, help="节点 ID；可重复指定")
    workflow_run.add_argument("--request-id", required=True, help="同一次执行重试时保持不变")
    workflow_run.add_argument("--confirm", action="store_true", help="确认执行当前列出的节点")
    assets = commands.add_parser("assets", help="查看和管理工作流资产")
    asset_commands = assets.add_subparsers(dest="assets_command", required=True)
    asset_commands.add_parser("list", help="列出已注册资产")
    asset_import = asset_commands.add_parser("import", help="导入本地资产并注册为候选资产")
    asset_import.add_argument("path", type=Path)
    asset_import.add_argument("--name")
    asset_import.add_argument("--role", default="reference")
    asset_import.add_argument("--kind")
    asset_review = asset_commands.add_parser("review", help="更新资产审核状态与备注")
    asset_review.add_argument("id")
    asset_review.add_argument("--status", required=True, choices=["ready", "candidate", "blocked"])
    asset_review.add_argument("--notes")
    production = commands.add_parser("production", help="查看分镜生产就绪摘要")
    production.add_argument("--all", action="store_true", help="同时输出全部逐镜状态")
    args = parser.parse_args()
    WORKSPACE_ID = args.workspace or WORKSPACE_ID
    if args.command == "workspaces":
        if args.workspace_command == "list":
            return request("/api/workspaces")
        if args.workspace_command == "create":
            return request("/api/workspaces", "POST", {"name": args.name, "workspacePath": args.path,
                "scriptPath": str(args.script.resolve()) if args.script else None, "modelId": args.model})
        if args.workspace_command == "activate":
            return request(f"/api/workspaces/{args.id}/activate", "POST", {})
        return request(f"/api/workspaces/{args.id}", "PATCH", {"archived": args.workspace_command == "archive"})
    if args.command == "doctor":
        return request("/api/status")
    if args.command == "script":
        return request("/api/script")
    if args.command in {"media","local-tools","local-jobs"}:
        return request({"media":"/api/media","local-tools":"/api/local-capabilities","local-jobs":"/api/local-jobs"}[args.command])
    if args.command == "timeline":
        return request('/api/timeline') if args.timeline_command=='get' else request('/api/timeline','PUT',json.loads(args.file.read_text(encoding='utf-8-sig')))
    if args.command == "local-run":
        spec=json.loads(args.spec.read_text(encoding='utf-8-sig'));spec['requestId']=args.request_id
        return request('/api/local-jobs','POST',spec)
    if args.command == "direction":
        if args.direction_command == "get":
            return request("/api/direction")
        if args.direction_command == "report":
            result = request("/api/direction/report")
            if not args.all:
                result.pop("shots", None)
            return result
        if args.direction_command == "audition":
            return request("/api/direction/audition", "POST", {"voiceId": args.voice, "caseId": args.case, "mode": args.mode, "assetId": args.asset})
        plan = json.loads(args.file.read_text(encoding="utf-8-sig"))
        if args.dry_run:
            from direction import validate_plan
            validate_plan(plan, request("/api/project"))
            return {"valid": True, "revisionMatches": plan.get("revision") == request("/api/direction")["revision"], "willSave": False}
        return request("/api/direction", "PUT", plan)
    if args.command == "review":
        route = "/api/shots/" + urllib.parse.quote(args.id, safe="") + "/review"
        if not args.status:
            return request(route)
        current_review = request(route)
        return request(route, "PUT", {"status": args.status, "notes": args.notes, "revision": current_review["projectRevision"]})
    if args.command == "audio":
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        spec.update(requestId=args.request_id, confirmed=args.confirm)
        if args.dry_run:
            return {"dryRun": True, "request": spec, "willSubmitGeneration": False}
        if not args.confirm:
            raise ValueError("声音模型调用需要已有授权并传--confirm；先使用--dry-run核对。")
        route = {"design":"/api/audio/voice-design","speech":"/api/audio/speech","reference":"/api/audio/reference-speech","clone":"/api/audio/voice-clone"}[args.audio_command]
        return request(route, "POST", spec)
    if args.command == "models":
        return request("/api/models")
    if args.command == "project":
        return request("/api/project")
    if args.command in ("shots", "shot"):
        project = request("/api/project")
        if args.command == "shots":
            return {"title": project["title"], "revision": project["revision"], "shots": [{k: s.get(k) for k in ("id", "number", "title", "scene", "status", "lastJobId")} for s in project["shots"]]}
        return next(s for s in project["shots"] if s["id"] == args.id)
    if args.command == "apply":
        project = json.loads(args.file.read_text(encoding="utf-8-sig"))
        if args.dry_run:
            from server import validate_project
            validate_project(project)
            current = request("/api/project")
            return {"valid": True, "revisionMatches": project.get("revision") == current["revision"], "shots": len(project["shots"]), "willSubmitGeneration": False}
        return request("/api/project", "PUT", project)
    if args.command == "import-script":
        if args.file.suffix.lower() not in (".md", ".txt"):
            raise ValueError("请先将剧本提取为 UTF-8 .md 或 .txt。")
        project = request("/api/project")
        project["script"] = args.file.read_text(encoding="utf-8-sig")
        project["sourceScript"] = str(args.file.resolve())
        if args.title:
            project["title"] = args.title
        return request("/api/project", "PUT", project)
    if args.command == "jobs":
        return request("/api/jobs")
    if args.command == "job":
        return request(f"/api/jobs/{urllib.parse.quote(args.id, safe='')}" + ("/refresh" if args.refresh else ""), "POST" if args.refresh else "GET", {} if args.refresh else None)
    if args.command == "request":
        if not args.path.startswith("/api/") or ".." in args.path:
            raise ValueError("request 只支持本地 /api/ 下的 GET。")
        return request(args.path)
    if args.command == "workflow":
        if args.workflow_command == "get":
            return request("/api/workflow")
        if args.workflow_command == "apply":
            from workflow import validate_structure
            graph = json.loads(args.file.read_text(encoding="utf-8-sig"))
            validate_structure(graph)
            if args.dry_run:
                return {"valid": True, "dryRun": True, "revision": graph.get("revision"),
                    "nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "willSave": False}
            return request("/api/workflow", "PUT", graph)
        body = {"nodeIds": args.nodes} if args.nodes else {}
        if args.workflow_command == "validate":
            return request("/api/workflow/validate", "POST", body)
        if args.workflow_command == "compile":
            return request("/api/workflow/compile", "POST", body)
        if args.workflow_command == "run":
            if not args.confirm:
                raise ValueError("工作流执行需要 --confirm；未提交任何节点。")
            return request("/api/workflow/run", "POST", {
                "nodeIds": args.nodes, "requestId": args.request_id, "confirmed": True})
    if args.command == "assets":
        if args.assets_command == "list":
            return request("/api/assets")
        if args.assets_command == "import":
            body = {"path": str(args.path.resolve()), "role": args.role}
            if args.name is not None:
                body["name"] = args.name
            if args.kind is not None:
                body["kind"] = args.kind
            return request("/api/assets/import", "POST", body)
        body = {"reviewStatus": args.status}
        if args.notes is not None:
            body["notes"] = args.notes
        return request("/api/assets/" + urllib.parse.quote(args.id, safe=""), "PATCH", body)
    if args.command == "production":
        production_data = request("/api/production")
        if args.all:
            return production_data
        return {"summary": production_data.get("summary"),
            "workflowRevision": production_data.get("workflowRevision")}
    if args.command == "generate":
        project = request("/api/project")
        shot = next(s for s in project["shots"] if s["id"] == args.shot)
        kind = args.kind or shot.get("kind", "video")
        params = dict(shot.get("params", {}))
        for pair in args.param:
            key, value = pair.split("=", 1)
            params[key] = value
        prompt = args.prompt_file.read_text(encoding="utf-8-sig") if args.prompt_file else shot.get("videoPrompt" if kind == "video" else "prompt", "")
        body = {"shotId": shot["id"], "kind": kind, "modelId": args.model or shot.get("modelId"), "prompt": prompt,
            "params": params, "imagePaths": [] if args.no_reference else shot.get("referencePaths", []),
            "requestId": args.request_id or uuid.uuid4().hex, "confirmed": args.confirm}
        if args.trim is not None:
            body["trimSeconds"] = args.trim
        if args.dry_run:
            return {"dryRun": True, "request": body, "willSubmitGeneration": False}
        if not args.confirm:
            raise ValueError("付费提交需要 --confirm；先使用 --dry-run 检查参数。")
        print("提交标识 requestId=" + body["requestId"] + "；如响应丢失，请先查询 jobs，不要换 ID 重发。", file=sys.stderr)
        return request("/api/jobs", "POST", body)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except (Exception, StopIteration) as exc:
        print(json.dumps({"error": str(exc) or "未找到指定镜头", "code": "CLI_ERROR"}, ensure_ascii=False))
        sys.exit(1)
