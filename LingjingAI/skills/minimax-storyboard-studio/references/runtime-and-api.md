# 镜序 Studio v0.6 runtime and API notes

Read this reference when operating the installed v0.6 production, editing, or local audio features. The repository `API.md` remains the full endpoint contract; `使用指南.md` is the user-facing sequence.

## Start and identify the local app

As of 0.7 the desktop shortcut launches the packaged Electron exe; `start.ps1` remains a compatible fallback launcher. It starts `server.py` detached, waits for `GET /api/health`, and opens the UI only after the health payload matches this app. Health returns `{ok,version,pid,appRoot,projectPath,workspaceId}` and does not contact Design. Reopening should reuse the same service. Stop through `Stop Studio.cmd`; `stop.ps1` verifies appRoot, PID, and the Python command line before stopping anything. If port 8766 belongs to an unrecognized process, do not terminate or reuse it.

`GET /api/models` reads the local catalog cache. Refreshing the Design catalog is a separate online action. Cached cloud model metadata does not prove that a model is currently online or free. Design image/video/audio submission still goes through the signed-in Design client and may incur credits.

## Workspace-bound local processing

All `/api/local-jobs` requests are bound to the selected workspace and need a stable `requestId`. Jobs run serially and return queued/running/succeeded/failed. Results are new candidate assets; source media is preserved. Local model failures remain local failures and must not silently fall back to paid Design processing.

Operations currently wired into the sound workspace are:

- `transcribe`: audio or video with audio → timestamped transcript and SRT. Correct names and timings before import.
- `tts`: up to 600 characters plus an audio reference no longer than 30 seconds → new speech. This does not inherit the reference performance or source video mouth motion.
- `voice_convert`: source audio plus a short reference → candidate conversion intended to retain the original words and performance.
- `speech_edit`: source audio, verified source text, and a scoped edit instruction → local edit candidate.
- `lip_sync`: source video plus confirmed dialogue audio → candidate video. The source video is never overwritten.
- `extract_audio` / `separate_vocals`: FFmpeg extraction / Demucs vocal-versus-other separation. Demucs is not actor diarization.

Readiness is governed by `GET /api/local-capabilities` and `data/local-speech-deployment.json` / `data/local-voice-deployment.json`. A deployment must be marked ready and pass the real local inference check; installed files or UI entries alone do not enable it. Current deployment records: FFmpeg, Demucs, faster-whisper, Qwen3-TTS, Seed-VC, and dots.tts.edit have actual processing/inference evidence. Speech ASR/TTS have CPU offline checks; Seed-VC and dots have RTX4060 GPU checks. MuseTalk 1.5 has also passed actual GPU inference in an isolated Python 3.10 environment with Python socket connections blocked. Test input is a portrait-based still video and original synthesized dialogue; facial texture softening was observed, so output remains a candidate. Recheck runtime status before claiming availability.

## Voice roles and timeline

Keep `voice_identity` separate from `dialogue_performance`, `post_mix`, and `model_reference`. Voice design/selection establishes identity, not approved dialogue. TTS creates a new performance; conversion changes the voice of an existing performance; local speech editing makes a scoped candidate; lip sync adapts video to confirmed audio. Review each output before downstream use.

`GET/PUT /api/timeline` is the legacy sequential timeline (0.5/0.6), with its own revision per workspace. The current 0.7 UI uses `/api/editor/project`; see desktop-editing.md. Clips and audio can be trimmed, split, copied, reordered, and mixed while preserving source files. `/api/timeline/import-subtitles` imports a chosen local transcript at an offset, after which each cue can be corrected. For the legacy interface MP4 and SRT are separate outputs. The 0.7 multi-track editor renders text/caption clips into its exported MP4.

The production workspace is a full-width page with the global Generate / Audio / Edit / Jobs navigation; Guide and Local Models are utility actions. Timeline preview plays real source trims, independent audio with gain/mute, and subtitle overlays; final MP4 export is the delivery check. Ctrl+S saves the current timeline, not the node graph.

Local speech generation supports a language selector and CPU/GPU options only for validated devices. A specific TTS cue prefers a reviewed voice reference for the same role. Lip sync accepts <=15s single-person videos, rejects dialogue longer than video, and never automatically loops source footage. Reference, generation, voice conversion, editing, and lip sync stay separate operations.
