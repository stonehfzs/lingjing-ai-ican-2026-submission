"""Local MiniMax Design adapter, inspected against desktop 3.0.18.74.

This module never reads, copies, or sends account tokens itself. The running
Design gateway supplies its own logged-in account and performs cloud admission,
billing, model validation, and moderation. Call submit only after explicit user
authorization for that exact paid job. There are deliberately no POST retries.

With a genuine Design runtime session bound using bind_context(), the original
session credit preflight is preserved. Without a session, the *existing MCP*
plugin-absent billing-current-scope fallback is used. That mode has no Design
chat credit-confirmation card; the caller must obtain confirmation in its UI.
It does not set x-hilo-source=canvas or change any credit reminder settings.
"""
from __future__ import annotations

import json
import http.client
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GatewayError(RuntimeError):
    """A stable error code plus the original, non-secret error metadata."""

    def __init__(self, code: str, message: str, *, status: int | None = None,
                 details: Any = None, submission_uncertain: bool = False):
        super().__init__(message)
        self.code = self.error_code = code
        self.status = status
        self.details = details
        self.submission_uncertain = submission_uncertain

    def as_dict(self) -> dict:
        return {"ok": False, "error": str(self), "error_code": self.code,
                "http_status": self.status,
                "submission_uncertain": self.submission_uncertain,
                "details": self.details}


class Gateway:
    def __init__(self, base_url: str = "http://127.0.0.1:8001"):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise GatewayError("INVALID_GATEWAY_URL", "Design gateway must be an HTTP loopback URL.")
        self.base_url = base_url.rstrip("/")
        self.session_id: str | None = None
        self.chat_turn_id: str | None = None
        self.workspace_headers: dict[str, str] = {}
        # Never let an environment HTTP proxy route loopback requests elsewhere.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def bind_context(self, session_id: str | None = None,
                     chat_turn_id: str | None = None) -> None:
        """Bind only ids returned by Design; never synthesize an existing session."""
        for value in (session_id, chat_turn_id):
            if value is not None and (not isinstance(value, str) or not value.strip()
                                      or any(c in value for c in "\r\n")):
                raise GatewayError("INVALID_SESSION_CONTEXT", "Invalid Design session context.")
        self.session_id, self.chat_turn_id = session_id, chat_turn_id

    def _request(self, method: str, path: str, body: dict | None = None, *,
                 headers: dict[str, str] | None = None, timeout: float = 30,
                 submission: bool = False) -> Any:
        request_headers = {"Accept": "application/json", **self.workspace_headers}
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=request_headers, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                details = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                details = {"error": raw.decode("utf-8", errors="replace")[:1500]}
            if not isinstance(details, dict):
                details = {"error": str(details)[:1500]}
            code = str(details.get("error_code") or details.get("code") or f"GATEWAY_HTTP_{exc.code}")
            message = details.get("user_message") or details.get("error") or details.get("message") or str(exc)
            if exc.code in (401, 403):
                message = f"MiniMax Design login or account permission is required: {message}"
            raise GatewayError(code, str(message), status=exc.code, details=details,
                               submission_uncertain=submission and exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
            code = "SUBMISSION_STATUS_UNKNOWN" if submission else ("GATEWAY_TIMEOUT" if timed_out else "GATEWAY_UNAVAILABLE")
            message = ("No definitive submission response. Do not resubmit automatically; inspect Design's task history first."
                       if submission else "Cannot reach the local MiniMax Design gateway. Open Design and the intended project.")
            raise GatewayError(code, message, submission_uncertain=submission) from None
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            raise GatewayError("INVALID_GATEWAY_RESPONSE", "Design returned a non-JSON response.",
                               submission_uncertain=submission) from None

    def health(self) -> dict:
        return self._request("GET", "/api/health/live", timeout=5)

    def workspace(self) -> dict:
        return self._request("GET", "/api/workspace")

    def models(self, kind: str) -> Any:
        if kind not in {"image", "video", "speech", "music"}:
            raise GatewayError("INVALID_MEDIA_KIND", "Expected image, video, speech, or music.")
        return self._request("GET", "/api/models/" + kind)

    def billing_scope(self) -> dict:
        path = ("/api/internal/sessions/" + urllib.parse.quote(self.session_id, safe="") + "/request-group"
                if self.session_id else "/api/internal/sessions/billing-current-scope")
        scope = self._request("GET", path, timeout=10)
        if not isinstance(scope, dict) or scope.get("mode") not in {"canonical", "legacy"}:
            raise GatewayError("BILLING_SCOPE_UNAVAILABLE", "Design did not return a valid billing scope.")
        if scope["mode"] == "canonical" and not isinstance(scope.get("group_id"), str):
            raise GatewayError("BILLING_SCOPE_UNAVAILABLE", "Design did not return the selected billing group.")
        return scope

    def _billing_headers(self) -> dict[str, str]:
        scope = self.billing_scope()
        headers = {}
        if scope["mode"] == "canonical":
            headers["x-group-id"] = scope["group_id"]
        if self.session_id:
            headers["x-session-id"] = self.session_id
            turn = scope.get("chat_turn_id") or self.chat_turn_id
            if turn:
                headers["x-chat-turn-id"] = turn
        return headers

    def wallet(self) -> dict:
        return self._request("GET", "/api/v1/credit/wallet", headers=self._billing_headers())

    def pricing(self) -> dict:
        return self._request("GET", "/api/v1/billing/pricing", headers=self._billing_headers())

    def estimate(self, kind: str, model: dict, params: dict,
                 image_paths: list[str] | None = None) -> dict:
        """Do not mistake promotion/pricing tables for an authoritative quote.

        The inspected local CreditController exposes wallet/migrate/transfer,
        but NOT calculate-cost. That primitive is cloud-internal, called by
        Design's session preflight. No unsupported direct-cloud request is made.
        """
        return {"available": False, "authoritative": False, "estimated_credits": None,
                "error_code": "EXACT_QUOTE_NOT_EXPOSED",
                "model": model.get("model_name") or model.get("id"),
                "reason": "This Design version has no exposed local exact-cost endpoint. Pricing tables are not a confirmed per-task quote; cloud submission is authoritative."}

    @staticmethod
    def _filename(filename: str, kind: str) -> str:
        if (not isinstance(filename, str) or len(filename) > 160
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename)
                or ".." in filename):
            raise GatewayError("INVALID_FILENAME", "Use a controlled ASCII filename, without a directory.")
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension not in ({"mp4"} if kind == "video" else {"png", "jpg", "jpeg", "webp"}):
            raise GatewayError("INVALID_FILENAME", "Filename extension does not match media kind.")
        return filename

    @staticmethod
    def build_body(kind: str, model_catalog_entry: dict, prompt: str, params: dict,
                   image_paths: list[str] | None, filename: str) -> dict:
        if kind not in {"image", "video"}:
            raise GatewayError("INVALID_MEDIA_KIND", "Only image and video submissions are supported.")
        if not isinstance(model_catalog_entry, dict):
            raise GatewayError("INVALID_MODEL", "A live Design model catalog entry is required.")
        backend = model_catalog_entry.get("backend")
        model_id = model_catalog_entry.get("model_name") or model_catalog_entry.get("id")
        if not all(isinstance(v, str) and v for v in (backend, model_id)):
            raise GatewayError("INVALID_MODEL", "Model catalog entry must contain id and backend.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise GatewayError("INVALID_PROMPT", "An explicit nonempty prompt is required.")
        if not isinstance(params, dict):
            raise GatewayError("INVALID_PARAMS", "params must be an object.")
        normalized = {}
        for key, value in params.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise GatewayError("INVALID_PARAMS", "Invalid parameter name.")
            if value is None:
                continue
            if isinstance(value, bool):
                normalized[key] = str(value).lower()
            elif isinstance(value, (str, int, float)):
                normalized[key] = str(value)
            else:
                raise GatewayError("INVALID_PARAMS", f"Parameter {key} must be a scalar; reference arrays use JSON strings.")
        images = image_paths or []
        if not isinstance(images, list) or any(not isinstance(p, str) or not p for p in images):
            raise GatewayError("INVALID_REFERENCES", "image_paths must contain nonempty paths.")
        if kind == "image":
            # These backends read params.model_name, not just top-level model_id.
            # Omitting it silently selects a provider default in Design 3.0.18.74.
            if backend in {"openai", "nano_banana", "seedream"}:
                normalized["model_name"] = model_id
            if backend in {"openai", "seedream"} and "resolution" in normalized:
                normalized["resolution"] = normalized["resolution"].lower()
            if backend == "midjourney":
                resolution = normalized.pop("resolution", normalized.get("clarity", "1k"))
                normalized["clarity"] = resolution.lower()
                if normalized["clarity"] == "2k" and not re.search(r"(?:^|\s)--hd(?:\s|$)", prompt):
                    prompt += " --hd"
        if kind == "video":
            if backend in {"seedance", "kling"}:
                normalized["model_name"] = model_id
            try:
                duration = int(normalized["duration"])
            except (KeyError, ValueError):
                raise GatewayError("INVALID_DURATION", "Video duration must be explicitly chosen in whole seconds.") from None
            if str(duration) != normalized["duration"]:
                raise GatewayError("INVALID_DURATION", "Video duration must be a whole number of seconds.")
            actual_model = model_catalog_entry.get("model_name") or model_id
            if actual_model in {"wan3.0-video", "wan3.0-video-prime"}:
                if not 2 <= duration <= 30:
                    raise GatewayError("INVALID_DURATION", "Wan 3.0 supports 2–30 seconds.")
            if actual_model == "MiniMax-H3":
                if not 4 <= duration <= 15:
                    raise GatewayError("INVALID_DURATION", "MiniMax-H3 supports 4–15 seconds.")
                normalized.setdefault("image_mode", "reference")
                if normalized["image_mode"] != "first-last-frame" and normalized.get("aspect_ratio") in {None, "", "adaptive"}:
                    raise GatewayError("INVALID_ASPECT_RATIO", "MiniMax-H3 reference/text mode requires an explicit aspect ratio such as 16:9.")
        return {"backend": backend, "model_id": model_id, "prompt": prompt,
                "params": normalized, "image_paths": list(images),
                "filename": Gateway._filename(filename, kind),
                "source_tool": "codex_studio:generate_" + kind}

    def submit(self, kind: str, model_catalog_entry: dict, prompt: str, params: dict,
               image_paths: list[str] | None, filename: str) -> dict:
        """Submit one user-authorized job. Never retries or disables gateway checks."""
        body = self.build_body(kind, model_catalog_entry, prompt, params, image_paths, filename)
        headers = self._billing_headers()  # Same normal scope fallback as bundled MCP.
        # Read-only login check using the same selected billing scope; do not extract a token.
        self._request("GET", "/api/v1/credit/wallet", headers=headers, timeout=30)
        return self._request("POST", f"/api/generate/{kind}/submit", body,
                             headers=headers, timeout=960 if self.session_id else 180,
                             submission=True)

    def query(self, task_id: str) -> dict:
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 300:
            raise GatewayError("INVALID_TASK_ID", "A task id from Design submission is required.")
        return self._request("GET", "/api/generate/tasks/" + urllib.parse.quote(task_id, safe="") + "/query",
                             headers=self._billing_headers(), timeout=60)

    @staticmethod
    def result_paths(response: dict) -> list[str]:
        """Returned local paths are relative to Design's workspace, not this app.

        succeeded result: {ok:true,path,paths?,width?,height?,duration?,node_id?}
        A compact asset.path is the recovery fallback. Original CDN URLs are not
        guaranteed by the local API; the gateway normally materializes them.
        """
        result = response.get("result", {})
        asset = response.get("asset", {})
        paths = result.get("paths", []) if isinstance(result, dict) else []
        candidates = [*paths] if isinstance(paths, list) else []
        for item in (result, asset):
            if isinstance(item, dict) and item.get("path"):
                candidates.append(item["path"])
        return list(dict.fromkeys(p for p in candidates if isinstance(p, str) and p))
