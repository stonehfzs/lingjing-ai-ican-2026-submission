# Third-party components

## FableCut

Source: https://github.com/ronak-create/FableCut
Pinned revision: 4a5b486abc74236959eceb8e2d2d44d9694161aa (v1.7.0)
License: MIT; original notice is retained in `vendor/fablecut/LICENSE`.

This app vendors the editor runtime under `vendor/fablecut`. Local changes add workspace isolation, Chinese interface labels, private media-library integration, clip clipboard/detach operations, captions from local ASR, preview proxies, save-conflict handling and export recovery. Upstream optional stock-media and font collections were not bundled.

FableCut was chosen because its vanilla JavaScript/Node editor could integrate with the existing Python and JavaScript application without replacing the storyboard, workspace and AI task systems. OpenCut and its archived classic version were evaluated; they were not incorporated. This is an adapted open-source editor, not Adobe Premiere or CapCut code.

## Desktop runtime

Electron 44.4.5 and @electron/packager 19.0.1 build the local Windows desktop shell. Runtime LICENSE and LICENSES.chromium.html files remain beside the executable. Node.js and Python are local runtime dependencies. The distribution currently targets this computer and its configured app/runtime paths.

## Other tools

FFmpeg, Demucs, faster-whisper, Qwen3-TTS, Seed-VC, dots.tts.edit and MuseTalk keep their own software/model licenses in the corresponding installed repositories and environments. The editor's MIT license does not change those licenses or model-use terms. Deployment versions and measured results are recorded in `data/local-speech-deployment.json` and `data/local-voice-deployment.json`.
