"""MiniMax Design voice-design and official per-line speech adapter.

Inspected against Design 3.0.18.74 resources/mcp-tools/dist/main.js
(`generate_audio_speech`, `VoicePrepareItemSchema`) and gateway/dist/main.js
(`MiniMaxSpeechService.buildTTSBody`, `designVoice`, `submitSpeech`).

Only the caller owns confirmation, idempotency, workspace capture and persistence.
Each network method performs a normal billing-scope lookup, wallet read, and ONE
POST. No tokens are read here, no canvas header is set, and no POST is retried.
Query a submitted speech task with the existing ``gw.query(task_id)``.

Voice design returns a reusable vendor voice_id and a PREVIEW, not a finished
dialogue line. trial_audio_url may be a workspace-relative local path or a URL.
Persist that preview with VOICE_DESIGN_ROLE. Persist a completed speech task
with SPEECH_ROLE and its exact source text and vendor voice ID. Never substitute
the design preview for a line's dialogue performance.

The live speech catalog contains speech-2.8-hd. The installed MCP also documents
speech-2.8-turbo; callers must still use an entry from their current live catalog.
SeedAudio reference synthesis and H3 audio continuation are different contracts
and intentionally not accepted by this vendor-voice-ID adapter.
"""
from __future__ import annotations

import json
import math
import re

from gateway import GatewayError


VOICE_DESIGN_ROLE = "voice_identity"
SPEECH_ROLE = "dialogue_performance"
SUPPORTED_SPEECH_MODELS = frozenset({"speech-2.8-hd", "speech-2.8-turbo"})
_EMOTIONS = frozenset({"happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent"})
_LANGUAGES = frozenset({
    "Chinese", "Chinese,Yue", "English", "Arabic", "Russian", "Spanish", "French",
    "Portuguese", "German", "Turkish", "Dutch", "Ukrainian", "Vietnamese", "Indonesian",
    "Japanese", "Italian", "Korean", "Thai", "Polish", "Romanian", "Greek", "Czech",
    "Finnish", "Hindi", "Bulgarian", "Danish", "Hebrew", "Malay", "Persian", "Slovak",
    "Swedish", "Croatian", "Filipino", "Hungarian", "Norwegian", "Slovenian", "Catalan",
    "Nynorsk", "Tamil", "Afrikaans", "auto",
})
_PARAMS = frozenset({"speed", "emotion", "vol", "pitch", "language_boost", "pronunciation_dict", "voice_modify"})
_EFFECTS = frozenset({"spacious_echo", "auditorium_echo", "lofi_telephone", "robotic"})


def render_performance(text, marks=None):
    """Insert supported pause/breath controls without changing spoken words.

Separate canonical dialogue from TTS markup. Empty marks preserve old requests.
Explicit matching prevents a direction from being inserted at the wrong phrase.
"""
    _text(text, "text")
    if not marks:
        return text
    if not isinstance(marks, list) or len(marks) > 8:
        _error("INVALID_PERFORMANCE", "Use at most eight explicit performance marks.")
    inserts = {}
    for mark in marks:
        if not isinstance(mark, dict) or set(mark) - {"after", "pause", "tag"}:
            _error("INVALID_PERFORMANCE", "Only after, pause and tag are supported.")
        anchor = mark.get("after")
        if not isinstance(anchor, str) or not anchor or text.count(anchor) != 1:
            _error("INVALID_PERFORMANCE", "Each after phrase must occur exactly once in the canonical line.")
        position = text.index(anchor) + len(anchor)
        if position == len(text) or position in inserts:
            _error("INVALID_PERFORMANCE", "Place one mark between phrases, not at the end or at a duplicate position.")
        if ("pause" in mark) == ("tag" in mark):
            _error("INVALID_PERFORMANCE", "Choose either pause or tag for a mark.")
        if "pause" in mark:
            value = _number(mark["pause"], "pause", 0.01, 2)
            inserts[position] = "<#" + value + "#>"
        else:
            if mark["tag"] not in {"breath", "inhale", "exhale", "sighs", "chuckle"}:
                _error("INVALID_PERFORMANCE", "Unsupported acting tag; do not include spoken instructions.")
            inserts[position] = "(" + mark["tag"] + ")"
    for position in sorted(inserts, reverse=True):
        text = text[:position] + inserts[position] + text[position:]
    return text


def _error(code, message):
    raise GatewayError(code, message)


def _text(value, field, max_length=None):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _error("INVALID_AUDIO_TEXT", f"{field} must be explicit, nonempty text.")
    if max_length is not None and len(value) > max_length:
        _error("INVALID_AUDIO_TEXT", f"{field} exceeds {max_length} characters.")
    return value  # Preserve the exact line; this module does not rewrite dialogue.


def _number(value, field, low, high, *, low_exclusive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        _error("INVALID_SPEECH_PARAMETER", f"{field} must be a number.")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        _error("INVALID_SPEECH_PARAMETER", f"{field} must be a finite number.")
    if (not math.isfinite(number) or number > high or number < low
            or (low_exclusive and number == low) or (integer and not number.is_integer())):
        _error("INVALID_SPEECH_PARAMETER", f"{field} is outside its supported range.")
    return str(int(number)) if number.is_integer() else str(number)


def _json_object(value, field):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            _error("INVALID_SPEECH_PARAMETER", f"{field} must contain a JSON object.")
    if not isinstance(value, dict):
        _error("INVALID_SPEECH_PARAMETER", f"{field} must be an object.")
    return value


def _pronunciation(value):
    value = _json_object(value, "pronunciation_dict")
    if set(value) != {"tone"} or not isinstance(value["tone"], list):
        _error("INVALID_SPEECH_PARAMETER", "pronunciation_dict must contain only a tone array.")
    if any(not isinstance(item, str) or not item.strip() or "/" not in item for item in value["tone"]):
        _error("INVALID_SPEECH_PARAMETER", "Each pronunciation rule must be source/replacement text.")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _voice_modify(value):
    value = _json_object(value, "voice_modify")
    if set(value) - {"pitch", "intensity", "timbre", "sound_effects"}:
        _error("INVALID_SPEECH_PARAMETER", "Unsupported voice_modify field.")
    normalized = {}
    for key, item in value.items():
        if key == "sound_effects":
            if not isinstance(item, str) or item not in _EFFECTS:
                _error("INVALID_SPEECH_PARAMETER", "Unsupported voice_modify sound effect.")
            normalized[key] = item
        else:
            normalized[key] = int(_number(item, "voice_modify." + key, -100, 100, integer=True))
    return json.dumps(normalized, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _filename(filename):
    if (not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename)
            or ".." in filename):
        _error("INVALID_AUDIO_FILENAME", "Use a controlled filename without a directory.")
    # Design appends the actual downloaded extension. Strip an optional audio
    # suffix so a caller's .wav does not produce misleading .wav.mp3 filenames.
    match = re.search(r"\.(mp3|wav|m4a|aac|flac|ogg|opus|pcm)$", filename, re.IGNORECASE)
    stem = filename[:match.start()] if match else filename
    if "." in stem or not 1 <= len(stem) <= 60:
        _error("INVALID_AUDIO_FILENAME", "Use a 1–60 character extensionless audio filename.")
    if stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        _error("INVALID_AUDIO_FILENAME", "This filename is reserved by Windows.")
    return stem


def build_voice_design_body(description, preview_text):
    """Pure preflight. A design preview is voice_identity, never a dialogue job."""
    return {"prompt": _text(description, "description"),
            "preview_text": _text(preview_text, "preview_text", 500)}


def build_speech_body(model_catalog_entry, text, vendor_voice_id, params, filename):
    """Pure, deterministic build for ONE official spoken line.

Custom IDs returned by voice design/clone are valid even when absent from the
catalog's short preset list. Their provenance and character assignment must be
recorded by the caller. The explicit argument is authoritative; params cannot
override voice_id/model_name, smuggle asset paths, or change billing fields.
"""
    if not isinstance(model_catalog_entry, dict):
        _error("INVALID_SPEECH_MODEL", "A live speech model catalog entry is required.")
    model = model_catalog_entry
    mid = model.get("model_name") or model.get("id")
    if model.get("backend") != "minimax_tts" or not isinstance(mid, str) or mid not in SUPPORTED_SPEECH_MODELS:
        _error("UNSUPPORTED_SPEECH_MODEL", "This adapter requires official speech-2.8-hd/turbo with a vendor voice_id; SeedAudio and H3 audio continuation use different contracts.")
    limit = model.get("promptMaxLength", 10000)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        _error("INVALID_SPEECH_MODEL", "The model text limit is invalid.")
    line = _text(text, "text", min(limit, 10000))
    if (not isinstance(vendor_voice_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", vendor_voice_id)):
        _error("INVALID_VOICE_ID", "Use the exact vendor voice_id returned by voice design/clone or voice catalog; an audio path or asset object is not a voice ID.")
    if not isinstance(params, dict) or set(params) - _PARAMS:
        _error("INVALID_SPEECH_PARAMETER", "Unsupported speech params; voice_id/model_name come from explicit arguments, not params.")
    normalized = {"model_name": mid, "voice_id": vendor_voice_id,
                  "speed": _number(params.get("speed", 1), "speed", 0.5, 2),
                  "language_boost": params.get("language_boost", "auto")}
    if not isinstance(normalized["language_boost"], str) or normalized["language_boost"] not in _LANGUAGES:
        _error("INVALID_SPEECH_PARAMETER", "Unsupported language_boost value.")
    emotion = params.get("emotion")
    if emotion not in (None, ""):
        if not isinstance(emotion, str) or emotion not in _EMOTIONS:
            _error("INVALID_SPEECH_PARAMETER", "Unsupported speech emotion.")
        normalized["emotion"] = emotion
    if "vol" in params:
        normalized["vol"] = _number(params["vol"], "vol", 0, 10, low_exclusive=True)
    if "pitch" in params:
        normalized["pitch"] = _number(params["pitch"], "pitch", -12, 12, integer=True)
    if "pronunciation_dict" in params:
        normalized["pronunciation_dict"] = _pronunciation(params["pronunciation_dict"])
    if "voice_modify" in params:
        normalized["voice_modify"] = _voice_modify(params["voice_modify"])
    return {"backend": "minimax_tts", "model_id": mid, "prompt": line,
            "filename": _filename(filename), "params": normalized,
            "source_tool": "codex_studio:generate_audio_speech"}


def _post_once(gw, path, body, timeout):
    headers = gw._billing_headers()
    gw._request("GET", "/api/v1/credit/wallet", headers=headers, timeout=30)
    response = gw._request("POST", path, body, headers=headers, timeout=timeout, submission=True)
    if not isinstance(response, dict):
        raise GatewayError("INVALID_AUDIO_RESPONSE", "Design returned a non-object audio response. Do not resubmit automatically.", submission_uncertain=True)
    return response


def design_voice(gw, description, preview_text):
    """One user-authorized voice design; return raw voice_id/trial_audio_url."""
    body = build_voice_design_body(description, preview_text)
    return _post_once(gw, "/api/speech/voice_design", body,
                      960 if getattr(gw, "session_id", None) else 300)


def build_reference_speech_body(model, text, params, audio_paths, image_paths, filename):
    """Installed Design SeedAudioSpeechService contract; no voice_id emulation."""
    if model.get('id')!='seed-audio-1.0' or model.get('backend')!='seedaudio':
        _error('UNSUPPORTED_SPEECH_MODEL','参考声音合成需要 Seed Audio 1.0。')
    _text(text,'text_prompt',3000)
    if not isinstance(params,dict) or set(params)-{'speed','volume','pitch','sample_rate'}:
        _error('INVALID_SPEECH_PARAMETER','Seed Audio 参数无效。')
    if len(audio_paths)>3 or len(image_paths)>1 or (audio_paths and image_paths):
        _error('INVALID_AUDIO_REFERENCE','最多3条音频或1张图片，两类不能混用。')
    output={'model_name':'seed-audio-1.0','speed':_number(params.get('speed',1),'speed',.5,2),
        'volume':_number(params.get('volume',1),'volume',.5,2),'pitch':_number(params.get('pitch',0),'pitch',-12,12,integer=True),
        'sample_rate':str(params.get('sample_rate',24000))}
    if output['sample_rate'] not in {'8000','16000','24000','32000','44100','48000'}:
        _error('INVALID_SPEECH_PARAMETER','采样率不支持。')
    return {'backend':'seedaudio','model_id':'seed-audio-1.0','prompt':text,'filename':_filename(filename),
        'params':output,'audio_paths':audio_paths,'image_paths':image_paths,'source_tool':'codex_studio:reference_speech'}


def submit_reference_speech(gw, body):
    return _post_once(gw,'/api/generate/speech/submit',body,180)


def clone_voice(gw, audio_path, preview_text):
    return _post_once(gw,'/api/speech/voice_clone',{'audio_path':audio_path,
        'demo_text':_text(preview_text,'demo_text',1000),'demo_model':'speech-2.8-hd'},300)


def submit_speech(gw, model_catalog_entry, text, vendor_voice_id, params, filename):
    """Submit one dialogue_performance task; return raw task response, no retry."""
    body = build_speech_body(model_catalog_entry, text, vendor_voice_id, params, filename)
    return _post_once(gw, "/api/generate/speech/submit", body,
                      960 if getattr(gw, "session_id", None) else 180)
