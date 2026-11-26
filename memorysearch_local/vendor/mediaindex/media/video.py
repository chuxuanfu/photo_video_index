# MemorySearch adapted packaging; see ../../THIRD-PARTY.md and vendor notices.
"""Video probing, frame sampling and thumbnails via FFmpeg/ffprobe (argument lists only).

Video embeddings use the model's native video input: frames sampled at a fixed rate are passed as
one sequence (vision encoder, 140 soft tokens per frame by default). The soundtrack is handled
separately by the audio encoder; passing frames does not include audio (verified in Phase 14).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .audio import SAFE_INPUT, ffmpeg_bin

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}  # .mov: iPhone and most cameras (the same ISO media container)
SUPPORTED_VIDEO_CODECS = {"h264", "hevc", "mpeg4", "av1", "vp9"}
MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_DURATION_S = 6 * 3600
MAX_FRAME_SIDE = 768


class VideoRejected(ValueError):
    pass


@dataclass
class VideoInfo:
    codec: str
    width: int  # as displayed: phones store portrait video sideways with a rotation flag, which FFmpeg applies
    height: int
    fps: float
    duration: float
    has_audio: bool
    audio_codec: str | None
    taken_at: str | None = None  # when it was recorded, local time: "2024-07-14T18:03:22"
    lat: float | None = None  # where (phones write ISO 6709 location tags)
    lon: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None


def probe_video(path: Path) -> VideoInfo:
    size = path.stat().st_size
    if size == 0:
        raise VideoRejected("empty file")
    if size > MAX_FILE_BYTES:
        raise VideoRejected(f"file too large ({size} bytes)")
    cmd = [ffmpeg_bin("ffprobe"), *SAFE_INPUT, "-v", "error", "-show_entries",
           "stream=codec_type,codec_name,width,height,avg_frame_rate,color_transfer,color_primaries,color_space:stream_side_data=rotation:stream_tags=rotate:"
           "format=duration,format_name:format_tags",
           "-of", "json", "--", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as e:
        raise VideoRejected("ffprobe timed out") from e
    if out.returncode != 0:
        raise VideoRejected(f"cannot read video: {out.stderr.decode(errors='replace').strip()[:200]}")
    data = json.loads(out.stdout or b"{}")
    fmt = data.get("format") or {}
    if "mp4" not in (fmt.get("format_name") or "").split(","):  # "mov,mp4,m4a,3gp,3g2,mj2" for .mp4 and .mov
        raise VideoRejected(f"unsupported container {fmt.get('format_name')!r}; MP4 and MOV are supported")
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise VideoRejected("no video stream")
    if v.get("codec_name") not in SUPPORTED_VIDEO_CODECS:
        raise VideoRejected(f"unsupported video codec {v.get('codec_name')!r}; supported: "
                            f"{', '.join(sorted(SUPPORTED_VIDEO_CODECS))}")
    try:
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        fps, duration = 0.0, 0.0
    if duration <= 0:
        raise VideoRejected("unknown or zero duration")
    if duration > MAX_DURATION_S:
        raise VideoRejected(f"video too long ({duration:.0f}s)")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if _rotation(v) % 180 == 90:
        w, h = h, w
    tags = {k.lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    gps = video_location(tags)
    return VideoInfo(v["codec_name"], w, h, fps, duration, a is not None, a.get("codec_name") if a else None,
                     video_date(tags), *(gps or (None, None)),v.get("color_transfer"),v.get("color_primaries"),v.get("color_space"))


def video_date(tags: dict[str, str]) -> str | None:
    """Recording time as local time. Apple's creationdate already is ("2024-07-14T18:03:22+0530"); the standard
    creation_time is UTC, so it is shown in this computer's time zone. Missing or placeholder dates give None."""
    for key, utc in (("com.apple.quicktime.creationdate", False), ("creation_time", True)):
        raw = tags.get(key, "").strip()
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.year < 1971:  # 1904/1970: the container's zero time, written by tools that don't know the date
            continue
        if utc and t.tzinfo is not None:
            t = t.astimezone()
        return t.replace(tzinfo=None, microsecond=0).isoformat()
    return None


ISO6709_RE = re.compile(r"^\s*([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)")


def video_location(tags: dict[str, str]) -> tuple[float, float] | None:
    """(latitude, longitude) from an ISO 6709 tag such as "+15.5439+073.7553+010.000/" (iPhone, Android)."""
    for key in ("com.apple.quicktime.location.iso6709", "location", "location-eng"):
        m = ISO6709_RE.match(tags.get(key, ""))
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0):
                return round(lat, 6), round(lon, 6)
    return None


def _rotation(stream: dict) -> int:
    """Display rotation in degrees (0, 90, 180, 270) from the display matrix, or the older "rotate" tag."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate") or 0) % 360
    except ValueError:
        return 0


def _scaled(info: VideoInfo, max_side: int) -> tuple[int, int]:
    w, h = info.width, info.height
    s = min(1.0, max_side / max(w, h))
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


HDR_PIPELINE_VERSION = "explicit-hlg-pq-srgb-mobius-v1"

def _hdr(info: VideoInfo) -> bool:
    return info.color_transfer == "arib-std-b67"

def _pixel_decoder(info: VideoInfo, w: int, h: int) -> tuple[str,str]:
    if not _hdr(info):
        return ffmpeg_bin("ffmpeg"), f"scale={w}:{h}"
    # Never silently assume missing HDR colorimetry or run the default swscale path.
    if info.color_primaries != "bt2020" or info.color_space not in ("bt2020nc", "bt2020c"):
        raise VideoRejected("HDR colorimetry incomplete or unsupported; review required")
    executable = os.environ.get("MEMORYSEARCH_HDR_FFMPEG", "")
    if not executable or not Path(executable).is_file():
        raise VideoRejected("HDR needs the fixed offline zscale FFmpeg; no SDR fallback")
    return executable, ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                        "tonemap=tonemap=mobius:desat=0,"
                        f"zscale=t=iec61966-2-1:m=gbr:r=full,format=rgb24,scale={w}:{h}")

def _decode_frame(path: Path, t: float, w: int, h: int, info: VideoInfo) -> np.ndarray | None:
    executable, pixel_filter = _pixel_decoder(info,w,h)
    cmd = [executable, "-nostdin", *SAFE_INPUT, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-map", "0:v:0",
           "-an", "-frames:v", "1", "-vf", pixel_filter, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as e:
        raise VideoRejected("frame decoding timed out") from e
    if out.returncode != 0:
        raise VideoRejected(f"cannot decode video frames: {out.stderr.decode(errors='replace').strip()[:200]}")
    if len(out.stdout) < w * h * 3:
        return None
    return np.frombuffer(out.stdout[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)


def sample_frames(path: Path, info: VideoInfo, start: float, duration: float, fps: float = 1.0,
                  max_side: int = MAX_FRAME_SIDE) -> tuple[np.ndarray, list[float]]:
    """Frames at `fps` within [start, start+duration), each taken at the centre of its 1/fps slot
    (start + (k + 0.5)/fps) with an accurate seek. Returns (T,H,W,3) uint8 and source timestamps."""
    w, h = _scaled(info, max_side)
    n = max(1, int(round(duration * fps)))
    frames, times = [], []
    for k in range(n):
        t = start + (k + 0.5) / fps
        if t >= info.duration:
            break
        f = _decode_frame(path, t, w, h, info)
        if f is not None:
            frames.append(f)
            times.append(round(t, 3))
    if not frames:
        raise VideoRejected("no frames decoded in window")
    return np.stack(frames), times


def frame_at(path: Path, t: float, max_side: int = 480, info: VideoInfo | None = None) -> Image.Image:
    info = info or probe_video(path)
    executable, pixel_filter = _pixel_decoder(info,*_scaled(info,max_side))
    if not _hdr(info):pixel_filter=f"scale='min({max_side},iw)':-2"
    cmd = [executable, "-nostdin", *SAFE_INPUT, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", pixel_filter, "-f", "image2pipe", "-vcodec", "png", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    if out.returncode != 0 or not out.stdout:
        raise VideoRejected("cannot extract frame")
    import io

    return Image.open(io.BytesIO(out.stdout)).convert("RGB")
