#!/usr/bin/env python3
"""Local Photo/Video indexer and search UI. Original media is never modified."""

from __future__ import annotations

import argparse
import array
import base64
import datetime as dt
import html
import importlib.util
import io
import json
import math
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any


PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
MODEL = "qwen3.8:27b-mlx"
PROMPT_VERSION = "detail-v1"
ASR_MODEL = "mlx-community/whisper-small-mlx"
FASTER_WHISPER_MODEL = "small"
OLLAMA_URL = "http://127.0.0.1:11434"
VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "spatial_relations": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "ocr_text": {"type": "string"},
        "uncertainty_note": {"type": "string"},
    },
    "required": ["summary", "description", "objects", "spatial_relations", "tags", "ocr_text", "uncertainty_note"],
    "additionalProperties": False,
}


class IndexErrorUser(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read an optional project config; command-line flags remain authoritative."""
    config_path = (path or Path(__file__).resolve().parent / "config.json").expanduser()
    if not config_path.is_file():
        if path is not None:
            raise IndexErrorUser(f"配置文件不存在：{config_path}")
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndexErrorUser(f"配置文件无法读取：{config_path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise IndexErrorUser("config.json 顶层必须是 JSON 对象")
    allowed = {
        "schema_version", "vision_model", "ollama_url", "timeout_seconds",
        "frame_interval_seconds", "max_frames", "checkpoint_every", "asr_backend",
        "mlx_asr_model", "faster_whisper_model", "silence_threshold_db",
        "search_port", "semantic_search",
    }
    unknown = set(data) - allowed
    if unknown:
        raise IndexErrorUser(f"config.json 含未知字段：{', '.join(sorted(unknown))}")
    if data.get("schema_version", 1) != 1 or isinstance(data.get("schema_version", 1), bool):
        raise IndexErrorUser("config.json 只支持 schema_version=1")
    strings = ("vision_model", "ollama_url", "mlx_asr_model", "faster_whisper_model")
    for key in strings:
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise IndexErrorUser(f"config.json 的 {key} 必须是非空字符串")
    for key in ("timeout_seconds", "max_frames", "checkpoint_every", "search_port"):
        if key in data and (type(data[key]) is not int or data[key] < (0 if key == "max_frames" else 1)):
            raise IndexErrorUser(f"config.json 的 {key} 必须是有效整数")
    for key in ("frame_interval_seconds", "silence_threshold_db"):
        if key in data and (type(data[key]) not in (int, float) or not math.isfinite(data[key])):
            raise IndexErrorUser(f"config.json 的 {key} 必须是有限数字")
    if "frame_interval_seconds" in data and data["frame_interval_seconds"] <= 0:
        raise IndexErrorUser("config.json 的 frame_interval_seconds 必须大于 0")
    if "search_port" in data and data["search_port"] > 65535:
        raise IndexErrorUser("config.json 的 search_port 必须在 1–65535 之间")
    if "asr_backend" in data and data["asr_backend"] not in {"auto", "mlx", "faster", "whispercpp", "skip"}:
        raise IndexErrorUser("config.json 的 asr_backend 只能是 auto、mlx、faster、whispercpp 或 skip")
    if "semantic_search" in data and type(data["semantic_search"]) is not bool:
        raise IndexErrorUser("config.json 的 semantic_search 必须是 true 或 false")
    return data


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_relative_path(value: str, kind: str | None = None) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        raise IndexErrorUser(f"不安全的 relative_path：{value!r}")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise IndexErrorUser(f"relative_path 不能是绝对路径：{value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise IndexErrorUser(f"relative_path 含空段、. 或 ..：{value!r}")
    if kind == "video" and parts[0].casefold() == "resources":
        raise IndexErrorUser("视频原件的 relative_path 不能指向 Resources")
    return PurePosixPath(*parts)


def resolve_media(root: Path, relative_path: str, kind: str, *, must_exist: bool = True) -> Path | None:
    rel = validate_relative_path(relative_path, kind)
    root_real = root.expanduser().resolve()
    candidate = root_real.joinpath(*rel.parts)
    try:
        real = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        return None
    if not within(real, root_real):
        raise IndexErrorUser(f"路径或符号链接越过媒体根目录：{relative_path}")
    if must_exist and not real.is_file():
        return None
    return real


def source_files(root: Path, kind: str) -> list[tuple[str, Path]]:
    root_real = root.resolve()
    extensions = PHOTO_EXT if kind == "photo" else VIDEO_EXT
    found: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if kind == "video" and rel.parts[0].casefold() == "resources":
            continue
        if any(part.startswith(".") for part in rel.parts) or not path.is_file() or path.suffix.lower() not in extensions:
            continue
        relative = rel.as_posix()
        validate_relative_path(relative, kind)
        actual = path.resolve(strict=True)
        if not within(actual, root_real):
            raise IndexErrorUser(f"原件符号链接越过媒体根目录：{path}")
        found.append((relative, path))
    return sorted(found, key=lambda pair: pair[0].casefold())


def resource_directory(root: Path, parts: tuple[str, ...]) -> Path:
    """Create Resources children one component at a time, rejecting symlink escapes."""
    root_real = root.resolve(strict=True)
    cursor = root
    for part in ("Resources", *parts):
        cursor = cursor / part
        if cursor.is_symlink():
            raise IndexErrorUser(f"Resources 中不允许符号链接：{cursor}")
        if cursor.exists():
            if not cursor.is_dir():
                raise IndexErrorUser(f"Resources 路径不是文件夹：{cursor}")
        else:
            cursor.mkdir()
        if not within(cursor.resolve(strict=True), root_real):
            raise IndexErrorUser(f"Resources 路径越过 Video 文件夹：{cursor}")
    return cursor


def safe_resource_file(path: Path, root: Path) -> None:
    if path.is_symlink() or not within(path.parent.resolve(strict=True), root.resolve(strict=True)):
        raise IndexErrorUser(f"Resources 文件路径不安全：{path}")


def read_index(path: Path, expected_kind: str | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndexErrorUser(f"无法读取 {path}：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("kind") not in {"photo", "video"}:
        raise IndexErrorUser(f"索引版本或类型不受支持：{path}")
    if expected_kind and data["kind"] != expected_kind:
        raise IndexErrorUser(f"{path} 是 {data['kind']} 索引，不能作为 {expected_kind} 使用")
    if not isinstance(data.get("library_id"), str) or not isinstance(data.get("assets"), list):
        raise IndexErrorUser(f"索引缺少 library_id 或 assets：{path}")
    for asset in data["assets"]:
        if not isinstance(asset, dict):
            raise IndexErrorUser(f"索引资产格式错误：{path}")
        validate_relative_path(asset.get("relative_path"), data["kind"])
    return data


def write_index(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".index-", suffix=".tmp", delete=False) as handle:
        temp_path = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        json.loads(temp_path.read_text(encoding="utf-8"))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def ollama_request(messages: list[dict[str, Any]], schema: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    body = {
        "model": args.model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    request = urllib.request.Request(
        args.ollama_url.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            result = json.load(response)
        return json.loads(result["message"]["content"])
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise IndexErrorUser(f"本机 Ollama 调用失败：{exc}。请先运行 doctor，并确认 Ollama 正在运行。") from exc


def image_bytes(path: Path) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise IndexErrorUser("缺少 Pillow；先运行安装脚本 setup.command") from exc
    with tempfile.TemporaryDirectory(prefix="media-image-") as tmp:
        input_path = path
        try:
            source = Image.open(input_path)
            source.load()
        except Exception:
            if path.suffix.lower() not in {".heic", ".heif"} or not shutil.which("sips"):
                raise IndexErrorUser(f"无法解码图片：{path}；HEIC 可先转成 JPEG 再试")
            input_path = Path(tmp) / "converted.jpg"
            run_checked(["sips", "-s", "format", "jpeg", str(path), "--out", str(input_path)], "HEIC 转换")
            try:
                source = Image.open(input_path)
                source.load()
            except Exception as exc:
                raise IndexErrorUser(f"HEIC 转换后的图像无法解码：{path}") from exc
        with source:
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            extrema = image.getextrema()
            if all(high <= 1 for _, high in extrema):
                raise IndexErrorUser(f"临时模型输入几乎全黑，请人工检查图片解码：{path}")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
            return output.getvalue()


def describe_image(path: Path, args: argparse.Namespace, *, video_frame: bool) -> dict[str, Any]:
    instruction = (
        "你正在为个人影像资料库建立可搜索的中文索引。只描述这张图实际可见的事实。"
        "description 要具体：主体数量、衣物或物体颜色与形状、动作、画面方位和相互位置、背景、光线、可辨认文字。"
        "照片通常写 100–250 个汉字，视频单帧通常写 80–200 个汉字；内容少可短，但不能用泛句敷衍。"
        "summary 是准确的短预览；objects、spatial_relations、tags 分开列出。ocr_text 只填确实读得清的字。"
        "看不清的内容写进 uncertainty_note。不得猜身份、地点、意图、说话人，也不得推断本帧前后发生的动作。"
        "仅输出符合 JSON Schema 的对象。"
    )
    if video_frame:
        instruction += "这是从视频按固定时间抽出的一帧；不要把它写成连续镜头。"
    encoded = base64.b64encode(image_bytes(path)).decode("ascii")
    data = ollama_request([{"role": "user", "content": instruction, "images": [encoded]}], VISION_SCHEMA, args)
    for field in ("summary", "description", "ocr_text", "uncertainty_note"):
        if not isinstance(data.get(field), str):
            raise IndexErrorUser(f"视觉模型输出缺少字段 {field}：{path}")
    for field in ("objects", "spatial_relations", "tags"):
        if not isinstance(data.get(field), list) or any(not isinstance(x, str) for x in data[field]):
            raise IndexErrorUser(f"视觉模型输出字段 {field} 格式错误：{path}")
    if len(data["description"].strip()) < 30:
        raise IndexErrorUser(f"视觉模型描述过短，请重试或检查模型输入：{path}")
    return data


def run_checked(command: list[str], label: str, *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IndexErrorUser(f"{label}失败：{exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-900:]
        raise IndexErrorUser(f"{label}失败：{tail}")
    return result


def ffmpeg_path() -> str | None:
    direct = shutil.which("ffmpeg")
    if direct:
        return direct
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def video_info(ffmpeg: str, path: Path) -> tuple[int, bool]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = run_checked([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], "视频探测", timeout=60)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        has_audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))
    else:
        result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], text=True, capture_output=True, timeout=60, check=False)
        details = result.stderr + result.stdout
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", details)
        if not match:
            raise IndexErrorUser(f"无法读取视频时长：{path}；请安装 ffprobe 或换一个可解码视频")
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
        has_audio = bool(re.search(r"Stream #.*: Audio:", details))
    if duration <= 0:
        raise IndexErrorUser(f"视频时长无效：{path}")
    return round(duration * 1000), has_audio


def extract_frame(ffmpeg: str, source: Path, at_ms: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_checked([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at_ms / 1000:.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(output)], f"视频抽帧 {at_ms / 1000:g} 秒")
    if not output.is_file() or output.stat().st_size == 0:
        raise IndexErrorUser(f"抽帧文件为空：{output}")


def extract_audio(ffmpeg: str, source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_checked([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)], "提取音频")
    if not output.is_file() or output.stat().st_size <= 44:
        raise IndexErrorUser(f"提取的音频为空：{output}")


def srt_time(milliseconds: int) -> str:
    hours, remaining = divmod(max(0, milliseconds), 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    seconds, millis = divmod(remaining, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def save_transcript(resources: Path, transcript: list[dict[str, Any]]) -> None:
    for filename in ("transcript.json", "transcript.srt"):
        if (resources / filename).is_symlink():
            raise IndexErrorUser(f"转录输出不能覆盖符号链接：{resources / filename}")
    (resources / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"{i}\n{srt_time(row['start_ms'])} --> {srt_time(row['end_ms'])}\n{row['text']}\n" for i, row in enumerate(transcript, 1)]
    (resources / "transcript.srt").write_text("\n".join(lines), encoding="utf-8")


def suppress_silent_segments(audio: Path, transcript: list[dict[str, Any]], threshold_db: float) -> list[dict[str, Any]]:
    """Discard ASR text aligned only to near-digital silence in extracted PCM audio."""
    filtered = []
    with wave.open(str(audio), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise IndexErrorUser("转录校验需要 16-bit 单声道 WAV；请检查 ffmpeg 输出")
        rate = wav.getframerate()
        total = wav.getnframes()
        for row in transcript:
            first = min(total, max(0, int(row["start_ms"] * rate / 1000)))
            last = min(total, max(first, int(row["end_ms"] * rate / 1000)))
            if last <= first:
                log(f"  丢弃无音频范围的转录：{row['text']}")
                continue
            wav.setpos(first)
            samples = array.array("h", wav.readframes(last - first))
            if sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) if samples else 0
            level_db = 20 * math.log10(max(rms, 1e-12) / 32768)
            if level_db < threshold_db:
                log(f"  丢弃静音段误转录（{level_db:.0f} dBFS）：{row['text']}")
                continue
            filtered.append(row)
    return filtered


def select_asr_backend(args: argparse.Namespace) -> str:
    requested = getattr(args, "asr", "auto")
    if requested != "auto":
        return requested
    if shutil.which("whisper-cli") and getattr(args, "whisper_model", None):
        return "whispercpp"
    if sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        if importlib.util.find_spec("mlx_whisper") is not None:
            return "mlx"
    return "faster"


def transcribe_audio(audio: Path, resources: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    backend = select_asr_backend(args)
    if backend == "skip":
        log("  语音转录已跳过；此视频索引标记为未完成")
        save_transcript(resources, [])
        return []
    if backend == "whispercpp":
        binary = shutil.which("whisper-cli")
        if not binary or not args.whisper_model or not Path(args.whisper_model).expanduser().is_file():
            raise IndexErrorUser("whisper.cpp 需要 whisper-cli 和 --whisper-model /模型文件；也可运行安装脚本后使用 --asr auto")
        output_prefix = resources / "whisper-raw"
        for suffix in (".json", ".srt"):
            if output_prefix.with_suffix(suffix).is_symlink():
                raise IndexErrorUser(f"whisper.cpp 输出不能覆盖符号链接：{output_prefix.with_suffix(suffix)}")
        run_checked([binary, "-m", str(Path(args.whisper_model).expanduser()), "-f", str(audio), "-l", "auto", "-oj", "-osrt", "-of", str(output_prefix)], "whisper.cpp 转录", timeout=7200)
        raw_path = output_prefix.with_suffix(".json")
        if not raw_path.is_file():
            raise IndexErrorUser("whisper-cli 没有生成 JSON 转录文件")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        transcript = []
        for row in raw.get("transcription", []):
            offsets = row.get("offsets", {})
            text = str(row.get("text", "")).strip()
            if text:
                transcript.append({"start_ms": int(offsets["from"]), "end_ms": int(offsets["to"]), "text": text})
    elif backend == "mlx":
        try:
            import mlx_whisper
        except ImportError as exc:
            raise IndexErrorUser(f"MLX Whisper 无法启动：{exc}。请在普通 Mac 终端试跑；也可安装 faster-whisper 后指定 --asr faster") from exc
        # mlx-whisper uses an ffmpeg subprocess even for a WAV input.
        os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        log(f"  本地语音转录：{args.asr_model}（首次使用可能下载模型）")
        raw = mlx_whisper.transcribe(str(audio), path_or_hf_repo=args.asr_model, verbose=False)
        transcript = [
            {"start_ms": round(float(row["start"]) * 1000), "end_ms": round(float(row["end"]) * 1000), "text": str(row["text"]).strip()}
            for row in raw.get("segments", []) if str(row.get("text", "")).strip()
        ]
    elif backend == "faster":
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise IndexErrorUser("faster-whisper 未安装；请先运行本系统的 setup 脚本") from exc
        model_name = getattr(args, "faster_whisper_model", FASTER_WHISPER_MODEL)
        log(f"  本地语音转录：faster-whisper {model_name}（首次使用可能下载模型）")
        try:
            model = WhisperModel(model_name, device="auto", compute_type="auto")
            segments, _ = model.transcribe(str(audio), beam_size=5, condition_on_previous_text=False)
            segments = list(segments)
        except (RuntimeError, OSError) as exc:
            log(f"  自动设备不可用，改用 CPU int8 转录：{exc}")
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(audio), beam_size=5, condition_on_previous_text=False)
        transcript = [
            {"start_ms": round(float(segment.start) * 1000), "end_ms": round(float(segment.end) * 1000), "text": str(segment.text).strip()}
            for segment in segments if str(segment.text).strip()
        ]
    else:
        raise IndexErrorUser(f"未知的转录方式：{backend}")
    for row in transcript:
        if row["start_ms"] < 0 or row["end_ms"] < row["start_ms"]:
            raise IndexErrorUser(f"转录时间码错误：{row}")
    transcript = suppress_silent_segments(audio, transcript, args.silence_threshold_db)
    save_transcript(resources, transcript)
    return transcript


def make_moments(
    frames: list[dict[str, Any]], transcript: list[dict[str, Any]], duration_ms: int,
    *, truncated_samples: bool = False, frame_interval_ms: int = 30000,
) -> list[dict[str, Any]]:
    moments = []
    for i, frame in enumerate(frames):
        at = frame["at_ms"]
        start = 0 if i == 0 else (frames[i - 1]["at_ms"] + at) // 2
        end = duration_ms if i == len(frames) - 1 else (at + frames[i + 1]["at_ms"]) // 2
        if i == len(frames) - 1 and truncated_samples:
            end = min(duration_ms, at + frame_interval_ms // 2)
        speech = [row for row in transcript if row["start_ms"] < end and row["end_ms"] >= start]
        speech_text = " ".join(row["text"] for row in speech)
        visual_text = frame["description"]
        summary = f"{at / 1000:g} 秒抽帧：{frame['summary']}"
        if speech_text:
            summary += f"；同一时间窗音轨：{speech_text}。说话人无法仅凭抽帧确认。"
        moments.append({"start_ms": start, "end_ms": end, "frame_at_ms": at, "visual_text": visual_text, "speech_text": speech_text, "summary": summary})
    return moments


def video_summary(frames: list[dict[str, Any]], transcript: list[dict[str, Any]], duration_ms: int, args: argparse.Namespace) -> str:
    samples = [{"at_ms": row["at_ms"], "description": row["description"]} for row in frames]
    evidence = json.dumps({"duration_ms": duration_ms, "frames": samples, "transcript": transcript}, ensure_ascii=False)
    if len(evidence) > 18000:
        evidence = evidence[:18000] + "…（较长视频；完整细节仍保留在 frames 和 transcript）"
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}
    prompt = "请仅依据以下定时抽帧和原话转录，写一段具体、可检索的中文视频摘要；区分画面和声音，写明关键时间，不推断两帧之间或说话人。输出 JSON。\n" + evidence
    result = ollama_request([{"role": "user", "content": prompt}], schema, args)
    summary = result.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        raise IndexErrorUser("模型生成的视频摘要过短")
    return summary.strip()


def processing_signature(kind: str, args: argparse.Namespace) -> str:
    fields = [kind, args.model, PROMPT_VERSION]
    if kind == "video":
        backend = select_asr_backend(args)
        fields += [str(args.frame_interval), str(args.max_frames), args.asr, args.asr_model, args.whisper_model or "", "silence-v1", str(args.silence_threshold_db)]
        # Preserve existing MLX indexes while recording the portable backend/model
        # when auto chooses faster-whisper on another operating system.
        if backend == "faster":
            fields += ["faster", getattr(args, "faster_whisper_model", FASTER_WHISPER_MODEL)]
    return "|".join(fields)


def index_photo(path: Path, relative: str, identifier: str, args: argparse.Namespace) -> dict[str, Any]:
    details = describe_image(path, args, video_frame=False)
    stat = path.stat()
    return {"id": identifier, "relative_path": relative, **details, "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns, "processing_signature": processing_signature("photo", args)}


def index_video(root: Path, path: Path, relative: str, identifier: str, args: argparse.Namespace) -> dict[str, Any]:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise IndexErrorUser("缺少 ffmpeg；先运行 setup.command 再试视频")
    duration_ms, has_audio = video_info(ffmpeg, path)
    resources = resource_directory(root, PurePosixPath(relative).parts)
    frames_dir = resource_directory(root, (*PurePosixPath(relative).parts, "frames"))
    samples = list(range(0, duration_ms, round(args.frame_interval * 1000))) or [0]
    partial = bool(args.max_frames and len(samples) > args.max_frames)
    if args.max_frames:
        samples = samples[:args.max_frames]
    frames = []
    for number, at_ms in enumerate(samples):
        frame_file = frames_dir / f"{number:06d}.jpg"
        safe_resource_file(frame_file, root)
        log(f"  抽帧与描述：{at_ms / 1000:g} 秒")
        extract_frame(ffmpeg, path, at_ms, frame_file)
        details = describe_image(frame_file, args, video_frame=True)
        frames.append({"at_ms": at_ms, **details})
    transcript: list[dict[str, Any]] = []
    if has_audio:
        audio = resources / "audio.wav"
        safe_resource_file(audio, root)
        extract_audio(ffmpeg, path, audio)
        transcript = transcribe_audio(audio, resources, args)
    else:
        save_transcript(resources, [])
    summary = video_summary(frames, transcript, duration_ms, args)
    stat = path.stat()
    return {
        "id": identifier, "relative_path": relative, "duration_ms": duration_ms, "summary": summary,
        "frames": frames, "transcript": transcript,
        "moments": make_moments(frames, transcript, duration_ms, truncated_samples=partial, frame_interval_ms=round(args.frame_interval * 1000)),
        "has_audio": has_audio, "index_status": "partial" if partial or (has_audio and args.asr == "skip") else "complete",
        "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
        "processing_signature": processing_signature("video", args),
    }


def index_root(root_value: str, kind: str, args: argparse.Namespace) -> Path:
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise IndexErrorUser(f"找不到 {kind} 文件夹：{root}")
    index_path = root / "index.json"
    old = read_index(index_path, kind)
    old_assets = {asset["relative_path"]: asset for asset in old["assets"]} if old else {}
    files = source_files(root, kind)
    live_paths = {relative for relative, _ in files}
    selected = files[:args.limit] if args.limit else files
    if not selected and not old:
        raise IndexErrorUser(f"{root} 中没有可识别的 {kind} 原件")
    library_id = old["library_id"] if old else f"{kind}-{uuid.uuid4().hex[:16]}"
    by_fingerprint: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for asset in old_assets.values():
        key = (asset.get("source_size"), asset.get("source_mtime_ns"))
        by_fingerprint.setdefault(key, []).append(asset)
    current = dict(old_assets)
    scan_limited = bool(args.limit and len(files) > len(selected))
    changed = (
        old is None or old.get("source_root_hint") != str(root)
        or old.get("scan_limited", False) != scan_limited
        or old.get("scan_incomplete", False) or bool(old.get("failed_files"))
    )
    generation = int(old.get("generation", 0)) if old else 0
    processed_since_checkpoint = 0

    def publish(*, incomplete: bool, failed_files: list[dict[str, str]] | None = None) -> None:
        nonlocal generation
        assets = [current[key] for key in sorted(current, key=str.casefold)]
        if len({asset["id"] for asset in assets}) != len(assets):
            raise IndexErrorUser("索引 id 重复；请检查是否有同尺寸、同修改时间的移动文件")
        generation += 1
        data = {
            "schema_version": 1, "library_id": library_id, "kind": kind,
            "generation": generation, "generated_at": utc_now(), "source_root_hint": str(root),
            "access_roots": old.get("access_roots", []) if old else [],
            "vision_model": args.model, "vision_prompt_version": PROMPT_VERSION,
            "scan_limited": scan_limited, "scan_incomplete": incomplete,
            "failed_files": failed_files or [], "assets": assets,
        }
        write_index(index_path, data)
        log(f"已写入：{index_path}（{len(assets)} 条；generation {generation}{'；待继续' if incomplete else ''}）")

    log(f"{kind.upper()}：{root}；本轮 {len(selected)}/{len(files)} 个文件")
    for number, (relative, path) in enumerate(selected, 1):
        stat = path.stat()
        prior = old_assets.get(relative)
        signature = processing_signature(kind, args)
        if prior and prior.get("source_size") == stat.st_size and prior.get("source_mtime_ns") == stat.st_mtime_ns and prior.get("processing_signature") == signature:
            current[relative] = prior
            log(f"[{number}/{len(selected)}] 跳过未变化：{relative}")
            continue
        moved = [] if args.limit else [
            candidate for candidate in by_fingerprint.get((stat.st_size, stat.st_mtime_ns), [])
            if candidate["relative_path"] not in live_paths
        ]
        identifier = prior["id"] if prior else moved[0]["id"] if len(moved) == 1 else f"{kind[0]}-{uuid.uuid4().hex[:16]}"
        log(f"[{number}/{len(selected)}] 处理：{relative}")
        try:
            asset = index_photo(path, relative, identifier, args) if kind == "photo" else index_video(root, path, relative, identifier, args)
        except Exception as exc:
            publish(incomplete=True, failed_files=[{"relative_path": relative, "error": str(exc)[:500]}])
            raise
        if not prior and len(moved) == 1:
            current.pop(moved[0]["relative_path"], None)
        current[relative] = asset
        changed = True
        processed_since_checkpoint += 1
        if processed_since_checkpoint >= args.checkpoint_every and number < len(selected):
            publish(incomplete=True)
            processed_since_checkpoint = 0
    if not args.limit or not files:
        for relative in list(current):
            if relative not in live_paths:
                del current[relative]
                changed = True
    if not changed:
        log(f"索引未变化：{index_path}")
        return index_path
    publish(incomplete=False)
    return index_path


@dataclass
class Hit:
    index_path: Path
    library_id: str
    kind: str
    relative_path: str
    source: str
    text: str
    at_ms: int | None
    score: float = 0


def searchable_text(item: dict[str, Any]) -> str:
    values = []
    for key in ("summary", "description", "visual_text", "speech_text", "text", "ocr_text", "uncertainty_note"):
        value = item.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("objects", "spatial_relations", "tags"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(part) for part in value)
    return " ".join(values)


def load_hits(index_paths: list[str]) -> tuple[list[Hit], dict[str, dict[str, Any]]]:
    paths: list[Path] = []
    for value in index_paths:
        path = Path(value).expanduser()
        if path.is_dir():
            paths.extend(path.rglob("index.json"))
        elif path.is_file():
            paths.append(path)
        else:
            raise IndexErrorUser(f"找不到索引：{path}")
    unique = sorted({path.resolve() for path in paths})
    if not unique:
        raise IndexErrorUser("没有找到 index.json")
    hits = []
    libraries: dict[str, dict[str, Any]] = {}
    for path in unique:
        data = read_index(path)
        assert data is not None
        library_id, kind = data["library_id"], data["kind"]
        if library_id in libraries and libraries[library_id]["index_path"] != path:
            raise IndexErrorUser(f"重复的 library_id：{library_id}；请只保留同一资料库的一份索引")
        libraries[library_id] = {
            "index_path": path, "kind": kind, "source_root_hint": data.get("source_root_hint", ""),
            "scan_limited": bool(data.get("scan_limited")),
            "scan_incomplete": bool(data.get("scan_incomplete")),
            "failed_files": data.get("failed_files", []),
        }
        for asset in data["assets"]:
            relative = asset["relative_path"]
            if kind == "photo":
                hits.append(Hit(path, library_id, kind, relative, "photo", searchable_text(asset), None))
            else:
                hits.append(Hit(path, library_id, kind, relative, "video", searchable_text(asset), 0))
                for frame in asset.get("frames", []):
                    hits.append(Hit(path, library_id, kind, relative, "frame", searchable_text(frame), int(frame["at_ms"])))
                for row in asset.get("transcript", []):
                    hits.append(Hit(path, library_id, kind, relative, "speech", searchable_text(row), int(row["start_ms"])))
                for moment in asset.get("moments", []):
                    hits.append(Hit(path, library_id, kind, relative, "moment", searchable_text(moment), int(moment.get("frame_at_ms", moment["start_ms"]))))
    return hits, libraries


def folded(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def grams(value: str, size: int) -> set[str]:
    compact = re.sub(r"\s+", "", folded(value))
    return {compact[i:i + size] for i in range(max(0, len(compact) - size + 1))}


def lexical_score(query: str, text: str) -> float:
    query_n, text_n = folded(query).strip(), folded(text)
    if not query_n:
        return 0
    score = 30.0 if query_n in text_n else 0.0
    terms = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+", query_n)
    score += sum(2.0 for term in terms if term in text_n)
    score += 3.0 * len(grams(query_n, 2) & grams(text_n, 2))
    score += 4.0 * len(grams(query_n, 3) & grams(text_n, 3))
    return score


def search_hits(hits: list[Hit], query: str, limit: int, args: argparse.Namespace | None = None) -> list[Hit]:
    semantic = bool(args and getattr(args, "semantic", False))
    phrases = [query]
    if semantic:
        schema = {
            "type": "object", "properties": {"search_phrases": {"type": "array", "items": {"type": "string"}}},
            "required": ["search_phrases"], "additionalProperties": False,
        }
        prompt = (
            "把用户的照片或视频搜索描述改写为最多 6 个中文或英文检索短语，包含常见同义说法和中英对应词。"
            "保留原有颜色、数量、动作和物体，不添加用户没有表达的事实；不要输出过于宽泛的单字词。仅输出 JSON。查询：" + query
        )
        try:
            expanded = ollama_request([{"role": "user", "content": prompt}], schema, args).get("search_phrases", [])
            if isinstance(expanded, list):
                phrases += [term.strip() for term in expanded[:6] if isinstance(term, str) and len(term.strip()) >= 2]
        except IndexErrorUser as exc:
            log(f"语义扩展不可用，继续按文字匹配：{exc}")
    scored = []
    source_weight = {"photo": 1.0, "video": 0.35, "frame": 1.05, "speech": 1.25, "moment": 0.9}
    for hit in hits:
        score = max(
            [lexical_score(query, hit.text)]
            + [0.75 * lexical_score(phrase, hit.text) for phrase in phrases[1:]]
        )
        if score > 0:
            hit.score = score * source_weight.get(hit.source, 1.0)
            scored.append(hit)
    specific_assets = {(hit.library_id, hit.relative_path) for hit in scored if hit.source in {"frame", "speech", "moment"}}
    scored = [hit for hit in scored if hit.source != "video" or (hit.library_id, hit.relative_path) not in specific_assets]
    scored.sort(key=lambda hit: hit.score, reverse=True)
    fallback_all = semantic and not scored and len(hits) <= 200
    if fallback_all:
        scored = hits[:]
    if semantic and scored:
        candidates = scored[:min(12, len(scored))]
        schema = {"type": "object", "properties": {"ranked_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ranked_ids"], "additionalProperties": False}
        prompt = "按用户描述的语义相关程度重排候选，只返回相关候选的整数 id；完全不相关的候选不要列出，不要创造文件或时间码。查询：" + query + "\n候选：" + json.dumps([{"id": i, "text": hit.text[:350]} for i, hit in enumerate(candidates)], ensure_ascii=False)
        try:
            order = ollama_request([{"role": "user", "content": prompt}], schema, args).get("ranked_ids", [])
            ordered = []
            for i in order:
                if isinstance(i, int) and 0 <= i < len(candidates) and candidates[i] not in ordered:
                    ordered.append(candidates[i])
            scored = ordered if fallback_all else ordered + [hit for hit in scored if hit not in ordered]
        except IndexErrorUser as exc:
            log(f"语义重排不可用，继续按文字匹配：{exc}")
            if fallback_all:
                scored = []
    output = []
    seen: set[tuple[str, str, int]] = set()
    for hit in scored:
        bucket = -1 if hit.at_ms is None else hit.at_ms // 5000
        key = (hit.library_id, hit.relative_path, bucket)
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
        if len(output) >= limit:
            break
    return output


def parse_roots(values: list[str]) -> dict[str, Path]:
    mapping = {}
    for item in values:
        if "=" not in item:
            raise IndexErrorUser("--media-root 的格式是 library_id=/可访问的/Photo或Video文件夹")
        library_id, path = item.split("=", 1)
        root = Path(path).expanduser().resolve()
        if not library_id or not root.is_dir():
            raise IndexErrorUser(f"媒体根目录不存在：{item}")
        mapping[library_id] = root
    return mapping


def locate(hit: Hit, roots: dict[str, Path]) -> Path | None:
    return resolve_media(roots.get(hit.library_id, hit.index_path.parent), hit.relative_path, hit.kind)


def time_label(ms: int | None) -> str:
    if ms is None:
        return ""
    return srt_time(ms).replace(",", ".")


def command_search(args: argparse.Namespace) -> None:
    hits, libraries = load_hits(args.index)
    roots = parse_roots(args.media_root)
    results = search_hits(hits, args.query, args.limit, args)
    if not results:
        log("没有命中。可换更具体的颜色、物体、地点、对白或时间描述。")
        return
    for i, hit in enumerate(results, 1):
        location = locate(hit, roots)
        timestamp = f" · {time_label(hit.at_ms)}" if hit.kind == "video" else ""
        log(f"{i}. [{hit.library_id}] {hit.relative_path}{timestamp} · {hit.source}")
        log(f"   {hit.text[:240]}")
        log(f"   原件：{location.as_uri() if location else '不可访问；可用 --media-root 指定原媒体根目录'}")
        if not location and libraries[hit.library_id]["source_root_hint"]:
            log(f"   源端文件夹提示：{libraries[hit.library_id]['source_root_hint']}")


def render_page(query: str, results: list[Hit], roots: dict[str, Path], libraries: dict[str, dict[str, Any]]) -> bytes:
    notices = []
    for library_id, data in libraries.items():
        if data["scan_limited"]:
            notices.append(f"{html.escape(library_id)} 只处理了试跑选中的部分文件")
        if data["scan_incomplete"]:
            notices.append(f"{html.escape(library_id)} 上次扫描中断；已保存此前进度，可重新运行脚本继续")
    notice_html = "".join(f'<p class="unavailable">{message}</p>' for message in notices)
    rows = []
    for hit in results:
        location = locate(hit, roots)
        query_args = urllib.parse.urlencode({"lib": hit.library_id, "path": hit.relative_path, "t": hit.at_ms or 0})
        title = html.escape(f"{hit.library_id} / {hit.relative_path}")
        detail = html.escape(hit.text[:450])
        when = f" · {html.escape(time_label(hit.at_ms))}" if hit.kind == "video" else ""
        if location:
            action = f'<a class="button" href="/view?{query_args}">打开原件{when}</a>'
        else:
            hint = libraries[hit.library_id].get("source_root_hint", "")
            hint_text = f"；源端文件夹提示：{html.escape(hint)}" if hint else ""
            action = f'<span class="unavailable">原件不可访问；可指定媒体根目录{hint_text}</span>'
        rows.append(f'<article><h3>{title}</h3><p>{detail}</p><div>{action}</div></article>')
    result_html = "".join(rows) if rows else "<p>输入颜色、物体、画面位置或对白，搜索这份索引。</p>"
    page = f"""<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>我的影像搜索</title>
<style>body{{font:16px -apple-system,BlinkMacSystemFont,sans-serif;background:#f5f6f7;color:#18212b;max-width:900px;margin:36px auto;padding:0 18px}}h1{{font-size:28px}}form{{display:flex;gap:8px;margin:24px 0}}input{{flex:1;padding:13px;border:1px solid #b7c0c9;border-radius:8px;font-size:17px}}button,.button{{border:0;border-radius:8px;background:#1259c3;color:#fff;padding:12px 16px;text-decoration:none;display:inline-block;cursor:pointer}}article{{background:white;padding:18px 22px;margin:12px 0;border-radius:12px;box-shadow:0 1px 6px #0001}}h3{{margin:0 0 8px;overflow-wrap:anywhere}}p{{line-height:1.65}}.unavailable{{color:#a14422}}small{{color:#596574}}</style>
<h1>我的影像搜索</h1><small>仅在本机读取 index.json；有权访问原件时才能打开。</small>{notice_html}<form action="/" method="get"><input name="q" value="{html.escape(query, quote=True)}" placeholder="例如：红色行李箱在列车门旁" autofocus><button>搜索</button></form>{result_html}</html>"""
    return page.encode("utf-8")


def serve(args: argparse.Namespace) -> None:
    hits, libraries = load_hits(args.index)
    roots = parse_roots(args.media_root)
    allowed = {library_id: set() for library_id in libraries}
    for hit in hits:
        allowed[hit.library_id].add(hit.relative_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host", "").lower() not in {
                f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"
            }:
                self.send_error(403, "This local search page only accepts localhost requests")
                return
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/":
                phrase = query.get("q", [""])[0].strip()
                results = search_hits(hits, phrase, 30, args) if phrase else []
                body = render_page(phrase, results, roots, libraries)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path in {"/view", "/media"}:
                library_id = query.get("lib", [""])[0]
                relative = query.get("path", [""])[0]
                library = libraries.get(library_id)
                if not library or relative not in allowed[library_id]:
                    self.send_error(404)
                    return
                try:
                    media = resolve_media(roots.get(library_id, library["index_path"].parent), relative, library["kind"])
                except IndexErrorUser:
                    self.send_error(400)
                    return
                if not media:
                    self.send_error(404, "Original media is not accessible")
                    return
                media_url = "/media?" + urllib.parse.urlencode({"lib": library_id, "path": relative})
                if parsed.path == "/view":
                    if library["kind"] == "photo":
                        viewer = f'<img src="{html.escape(media_url, quote=True)}" style="max-width:100%;max-height:85vh" alt="原图">'
                    else:
                        raw_time = query.get("t", ["0"])[0]
                        try:
                            seconds = max(0, int(raw_time)) / 1000
                        except ValueError:
                            seconds = 0
                        viewer = f'<video id="v" src="{html.escape(media_url, quote=True)}" controls autoplay playsinline style="max-width:100%;max-height:85vh"></video><script>document.getElementById("v").addEventListener("loadedmetadata",function(){{this.currentTime={seconds};}},{{once:true}});</script>'
                    body = f'<!doctype html><html lang="zh"><meta charset="utf-8"><title>{html.escape(media.name)}</title><body style="font:16px -apple-system,sans-serif;margin:20px"><p><a href="/">← 返回搜索</a>　{html.escape(relative)}</p>{viewer}</body></html>'.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.stream_media(media)
                return
            self.send_error(404)

        def stream_media(self, media: Path) -> None:
            size = media.stat().st_size
            start, end, status = 0, size - 1, 200
            range_header = self.headers.get("Range", "")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match or (not match[1] and not match[2]):
                    self.send_error(416)
                    return
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), size - 1) if match[2] else size - 1
                else:
                    start = max(0, size - int(match[2]))
                if start >= size or end < start:
                    self.send_error(416)
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(media.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with media.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt: str, *values: Any) -> None:
            if args.verbose:
                super().log_message(fmt, *values)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    log(f"浏览器搜索已启动：{url}（按 Control-C 结束）")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("搜索服务已关闭")
    finally:
        server.server_close()


def doctor(args: argparse.Namespace) -> None:
    log(f"配置：{getattr(args, 'config_path', None) or '内置默认值'}")
    log(f"Python：{sys.version.split()[0]}")
    try:
        from PIL import __version__ as pillow_version
        log(f"Pillow：{pillow_version} ✓")
    except ImportError:
        log("Pillow：未安装（照片需运行 setup.command）")
    log(f"ffmpeg：{ffmpeg_path() or '未安装（视频需运行 setup.command）'}")
    log(f"whisper-cli：{shutil.which('whisper-cli') or '未安装（下方 MLX Whisper 可替代）'}")
    try:
        from importlib.metadata import PackageNotFoundError, version
        mlx_version = version("mlx-whisper")
        try:
            import mlx_whisper  # noqa: F401
            log(f"mlx-whisper：{mlx_version}，可启动 ✓")
        except ImportError as exc:
            log(f"mlx-whisper：{mlx_version} 已安装，但当前会话不能启动（{exc}）")
    except PackageNotFoundError:
        log("mlx-whisper：未安装（视频语音转录需运行 setup.command）")
    try:
        faster_version = version("faster-whisper")
        import faster_whisper  # noqa: F401
        log(f"faster-whisper：{faster_version}，可启动 ✓")
    except PackageNotFoundError:
        log("faster-whisper：未安装（Linux/Windows/Intel Mac 的视频语音转录需运行 setup 脚本）")
    except ImportError as exc:
        log(f"faster-whisper：已安装，但当前会话不能启动（{exc}）")
    log(f"自动语音转录后端：{select_asr_backend(args)}")
    try:
        with urllib.request.urlopen(args.ollama_url.rstrip("/") + "/api/tags", timeout=3) as response:
            data = json.load(response)
        names = [item.get("name") for item in data.get("models", [])]
        log(f"Ollama：已连接；模型 {args.model} {'✓' if args.model in names else '未显示，请检查 ollama list'}")
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f"Ollama：无法连接（{exc}）；请启动 Ollama 应用或在终端运行 ollama serve")


def parser(config: dict[str, Any] | None = None) -> argparse.ArgumentParser:
    config = config or {}
    main = argparse.ArgumentParser(description="本地 Photo/Video 索引生成与搜索；每个文件夹只需同步 index.json")
    main.add_argument("--config", help="配置 JSON 路径；默认读取项目目录中的 config.json，可放在子命令前或后")
    sub = main.add_subparsers(dest="command", required=True)
    common_model = argparse.ArgumentParser(add_help=False)
    common_model.add_argument("--model", default=config.get("vision_model", MODEL))
    common_model.add_argument("--ollama-url", default=config.get("ollama_url", OLLAMA_URL))
    common_model.add_argument("--timeout", type=int, default=config.get("timeout_seconds", 300))
    index = sub.add_parser("index", parents=[common_model], help="分析原件并写入各自文件夹的 index.json")
    index.add_argument("--photo-root", help="Photo 文件夹路径")
    index.add_argument("--video-root", help="Video 文件夹路径")
    index.add_argument("--limit", type=int, default=0, help="每类最多处理前 N 个文件；0=全部，试跑建议 1")
    index.add_argument("--checkpoint-every", type=int, default=config.get("checkpoint_every", 25), help="每成功处理 N 个文件先保存一次索引；默认 25")
    index.add_argument("--frame-interval", type=float, default=config.get("frame_interval_seconds", 30), help="视频抽帧间隔（秒）")
    index.add_argument("--max-frames", type=int, default=config.get("max_frames", 0), help="每视频最多抽 N 帧；0=完整，试跑可用 3")
    index.add_argument("--asr", choices=["auto", "mlx", "faster", "whispercpp", "skip"], default=config.get("asr_backend", "auto"))
    index.add_argument("--asr-model", default=config.get("mlx_asr_model", ASR_MODEL), help="MLX Whisper 模型，可写本地目录或 Hugging Face 名称")
    index.add_argument("--faster-whisper-model", default=config.get("faster_whisper_model", FASTER_WHISPER_MODEL), help="faster-whisper 模型大小或本地目录；默认 small")
    index.add_argument("--whisper-model", help="whisper.cpp 的 ggml 模型文件路径")
    index.add_argument("--silence-threshold-db", type=float, default=config.get("silence_threshold_db", -55.0), help="过滤落在近乎静音音频上的误转录；默认 -55 dBFS")
    index.set_defaults(func=lambda args: command_index(args))
    search = sub.add_parser("search", parents=[common_model], help="从一份或多份 index.json 搜索文字")
    search.add_argument("--index", action="append", required=True, help="index.json 或其上级文件夹；可重复")
    search.add_argument("--query", required=True)
    search.add_argument("--media-root", action="append", default=[], help="library_id=/可访问的媒体根；可重复")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--semantic", dest="semantic", action="store_true", help="用本机 Ollama 对文字候选重排")
    search.add_argument("--no-semantic", dest="semantic", action="store_false", help="仅使用文字匹配")
    search.set_defaults(semantic=config.get("semantic_search", False))
    search.set_defaults(func=command_search)
    web = sub.add_parser("serve", parents=[common_model], help="在本机浏览器中搜索，并打开原图或跳转视频秒数")
    web.add_argument("--index", action="append", required=True)
    web.add_argument("--media-root", action="append", default=[])
    web.add_argument("--port", type=int, default=config.get("search_port", 8765))
    web.add_argument("--open-browser", action="store_true")
    web.add_argument("--semantic", dest="semantic", action="store_true", help="用本机 Ollama 扩展查询")
    web.add_argument("--no-semantic", dest="semantic", action="store_false", help="仅使用文字匹配，不调用本机模型扩展查询")
    web.add_argument("--verbose", action="store_true")
    web.set_defaults(func=serve, semantic=config.get("semantic_search", True))
    diag = sub.add_parser("doctor", parents=[common_model], help="检查本机模型与视频依赖")
    diag.set_defaults(func=doctor)
    return main


def command_index(args: argparse.Namespace) -> None:
    if not args.photo_root and not args.video_root:
        raise IndexErrorUser("至少指定 --photo-root 或 --video-root")
    if args.limit < 0 or args.max_frames < 0 or args.frame_interval <= 0 or args.checkpoint_every < 1:
        raise IndexErrorUser("--limit、--max-frames 不能为负数；抽帧间隔和保存间隔必须大于 0")
    if args.photo_root:
        index_root(args.photo_root, "photo", args)
    if args.video_root:
        index_root(args.video_root, "video", args)


def main() -> int:
    try:
        config_arg = argparse.ArgumentParser(add_help=False)
        config_arg.add_argument("--config")
        config_options, remaining = config_arg.parse_known_args()
        config_path = Path(config_options.config) if config_options.config else None
        config = load_config(config_path)
        args = parser(config).parse_args(remaining)
        default_config_path = Path(__file__).resolve().parent / "config.json"
        args.config_path = str(config_path or default_config_path) if (config_path or default_config_path.is_file()) else None
        args.func(args)
        return 0
    except IndexErrorUser as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
