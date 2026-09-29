"""Real local media metadata and waveform extraction, without cloud calls."""
import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


def binary(name):
    local = Path(__file__).resolve().parent / '.runtime/ffmpeg' / (name + '.exe')
    if local.is_file():
        return str(local)
    bundled = Path(os.environ.get("LOCALAPPDATA", "")) / "com.minimax.hub/current/resources/ffmpeg" / (name + ".exe")
    return str(bundled) if bundled.is_file() else shutil.which(name)


def probe(path, waveform=True):
    ffprobe = binary("ffprobe")
    if not ffprobe:
        return {"probeStatus": "unavailable", "probeError": "ffprobe未安装，不能验证实际时长"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,duration", "-of", "json", str(path)], capture_output=True, check=True, timeout=25, creationflags=flags)
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("无法得到有效的媒体时长")
        data = {"duration": duration, "durationSec": duration, "streams": info.get("streams", []), "probeStatus": "verified"}
        video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
        audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
        if video:
            data.update(width=video.get("width"), height=video.get("height"))
            numerator, denominator = video.get("r_frame_rate", "0/1").split("/")
            data["fps"] = float(numerator) / max(float(denominator), 1)
        if audio:
            data.update(sampleRate=int(audio.get("sample_rate", 0)), channels=audio.get("channels"))
        ffmpeg = binary("ffmpeg")
        if waveform and audio and ffmpeg:
            decoded = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-t", str(min(duration, 600)), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"], capture_output=True, check=True, timeout=45, creationflags=flags)
            samples = array.array("f")
            samples.frombytes(decoded.stdout)
            size = max(1, math.ceil(len(samples) / 160))
            peaks = [max((abs(s) for s in samples[i:i + size] if math.isfinite(s)), default=0) for i in range(0, len(samples), size)]
            ceiling = max(peaks, default=1) or 1
            data["waveform"] = [round(min(1, p / ceiling), 4) for p in peaks]
            data["waveformDuration"] = min(duration, 600)
        return data
    except Exception as exc:
        return {"probeStatus": "failed", "probeError": str(exc)[:500]}
