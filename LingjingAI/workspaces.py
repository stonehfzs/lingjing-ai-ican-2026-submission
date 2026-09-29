"""Workspace registry with request/job scoped state, never global path switching."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import copy
import json
import re
import threading
import uuid

from workflow import WorkflowStore, WorkflowError, load, write, timestamp

_CURRENT: ContextVar = ContextVar("studio_workspace", default=None)


@dataclass
class Workspace:
    id: str
    name: str
    root: Path
    createdAt: str = ""
    updatedAt: str = ""
    legacy: bool = False
    archived: bool = False
    _store: object = field(default=None, repr=False)

    @property
    def data(self):
        return self.root / "data"

    @property
    def media(self):
        return self.root / "media"

    @property
    def project(self):
        return self.data / "project.json"

    @property
    def jobs(self):
        return self.data / "jobs.json"

    @property
    def store(self):
        if self._store is None:
            self._store = WorkflowStore(self.root)
        return self._store

    def summary(self):
        project = load(self.project, {})
        return {"id": self.id, "name": self.name, "root": str(self.root), "workspacePath": str(self.root),
            "projectPath": str(self.project), "createdAt": self.createdAt, "updatedAt": self.updatedAt,
            "shotCount": len(project.get("shots", [])), "scriptPresent": bool(project.get("script")), "legacy": self.legacy, "archived": self.archived}


class WorkspaceRegistry:
    def __init__(self, app_root):
        self.root = Path(app_root).resolve()
        self.path = self.root / "studio-workspaces.json"
        self.lock = threading.RLock()
        self._items = None
        self._active = None

    def _read(self):
        with self.lock:
            if self._items is not None:
                return
            config = load(self.path, None)
            if config is None:
                project = load(self.root / "data/project.json", {})
                wid = re.sub(r"[^A-Za-z0-9_-]", "-", str(project.get("id") or "legacy-project"))[:70]
                item = Workspace(wid, project.get("title") or "我的项目", self.root, timestamp(), timestamp(), True)
                self._items, self._active = {wid: item}, wid
            else:
                self._items = {item["id"]: Workspace(item["id"], item["name"], Path(item["root"]).resolve(),
                    item.get("createdAt", ""), item.get("updatedAt", ""), item.get("legacy", False), item.get("archived", False)) for item in config["workspaces"]}
                self._active = config["activeWorkspaceId"]
                if self._active not in self._items:
                    raise WorkflowError("项目注册表损坏：活动项目不存在。", "WORKSPACE_REGISTRY_INVALID", 500)

    def persist(self):
        self._read()
        with self.lock:
            write(self.path, {"version": 1, "activeWorkspaceId": self._active, "workspaces": [
                {"id": w.id, "name": w.name, "root": str(w.root), "createdAt": w.createdAt, "updatedAt": w.updatedAt, "legacy": w.legacy, "archived": w.archived}
                for w in self._items.values()]})

    def get(self, wid=None):
        self._read()
        selected = wid or self._active
        if selected not in self._items:
            raise WorkflowError("项目 workspace 不存在。", "WORKSPACE_NOT_FOUND", 404)
        return self._items[selected]

    def all(self):
        self._read()
        with self.lock:
            return list(self._items.values())

    def listing(self):
        self._read()
        with self.lock:
            return {"activeWorkspaceId": self._active, "workspaces": [w.summary() for w in self._items.values() if not w.archived], "archived": [w.summary() for w in self._items.values() if w.archived]}

    def activate(self, wid):
        workspace = self.get(wid)
        if workspace.archived:
            raise WorkflowError("请先恢复已归档项目。", "WORKSPACE_ARCHIVED", 409)
        with self.lock:
            self._active = workspace.id
            self.persist()
        return workspace

    def update(self, wid, changes):
        workspace = self.get(wid)
        if set(changes) - {"name", "archived"}:
            raise WorkflowError("只能修改项目名称与归档状态。")
        with self.lock:
            if "name" in changes:
                name = str(changes["name"]).strip()
                if not name or len(name) > 120:
                    raise WorkflowError("项目名称无效。")
                workspace.name = name
            if "archived" in changes:
                if not isinstance(changes["archived"], bool):
                    raise WorkflowError("archived须为布尔值。")
                if changes["archived"] and self._active == wid:
                    others = [w for w in self._items.values() if w.id != wid and not w.archived]
                    if not others:
                        raise WorkflowError("至少保留一个未归档项目。")
                    self._active = others[0].id
                workspace.archived = changes["archived"]
            workspace.updatedAt = timestamp()
            self.persist()
        return workspace

    def create(self, spec):
        name = str(spec.get("name", "")).strip()
        if not name or len(name) > 120:
            raise WorkflowError("项目名称需为1–120个字符。")
        wid = "project-" + uuid.uuid4().hex[:12]
        requested = spec.get("workspacePath") or spec.get("rootPath")
        if requested:
            root = Path(requested).expanduser()
            if not root.is_absolute():
                raise WorkflowError("workspacePath 必须是绝对路径。")
            root = root.resolve()
            if root.exists() and any(root.iterdir()):
                raise WorkflowError("所选目录非空；请使用新目录，避免覆盖原剧本或项目。", "WORKSPACE_NOT_EMPTY", 409)
        else:
            root = self.root / "workspaces" / wid
        if any(w.root == root for w in self.all()):
            raise WorkflowError("此目录已登记为项目。", "WORKSPACE_ALREADY_REGISTERED", 409)
        script = spec.get("script", "")
        if spec.get("scriptPath"):
            source = Path(spec["scriptPath"]).resolve()
            if not source.is_file() or source.suffix.lower() not in {".md", ".txt"} or source.stat().st_size > 8 * 1024 * 1024:
                raise WorkflowError("剧本需为本地UTF-8 Markdown或TXT文件，最大8MB。")
            script = source.read_text(encoding="utf-8-sig")
        if not isinstance(script, str):
            raise WorkflowError("script 必须是原文字符串。")
        workspace = Workspace(wid, name, root, timestamp(), timestamp())
        workspace.data.mkdir(parents=True, exist_ok=True)
        workspace.media.mkdir(parents=True, exist_ok=True)
        write(workspace.project, {"id": wid, "title": name, "revision": 1, "script": script, "style": spec.get("style", ""),
            "shots": [], "edges": [], "defaultModelId": spec.get("modelId"), "createdAt": timestamp(), "updatedAt": timestamp(),
            "sourceScript": spec.get("scriptPath"), "workspaceId": wid})
        write(workspace.jobs, [])
        write(workspace.data / "assets.json", {"revision": 0, "assets": []})
        write(workspace.data / "workflow.json", {"version": 1, "revision": 0, "nodes": [], "edges": []})
        cache = self.root / "data/model-catalog.json"
        if cache.exists():
            write(workspace.data / "model-catalog.json", load(cache, {}))
        with self.lock:
            self._items[wid] = workspace
            if spec.get("activate", True):
                self._active = wid
            self.persist()
        return workspace


def current(registry):
    return _CURRENT.get() or registry.get()


@contextmanager
def scope(workspace):
    token = _CURRENT.set(workspace)
    try:
        yield workspace
    finally:
        _CURRENT.reset(token)


class WorkspacePath:
    """Path-like proxy resolves inside the current immutable request context."""
    def __init__(self, registry, attribute):
        self.registry, self.attribute = registry, attribute

    def _path(self):
        return getattr(current(self.registry), self.attribute)

    def __fspath__(self):
        return str(self._path())

    def __str__(self):
        return str(self._path())

    def __truediv__(self, value):
        return self._path() / value

    def __getattr__(self, name):
        return getattr(self._path(), name)


class WorkspaceStoreProxy:
    def __init__(self, registry):
        self.registry = registry

    def __getattr__(self, name):
        return getattr(current(self.registry).store, name)


def scoped_urls(value, wid):
    if isinstance(value, str) and value.startswith("/media/"):
        return f"/w/{wid}" + value
    if isinstance(value, list):
        return [scoped_urls(item, wid) for item in value]
    if isinstance(value, dict):
        return {key: scoped_urls(item, wid) if key not in {"script", "text", "prompt", "videoPrompt"} else item for key, item in value.items()}
    return value
