#!/usr/bin/env python3
"""Small, dependency-light helpers shared by the one-click installers."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parent
CONFIG = PROJECT / "config.json"
EXAMPLE = PROJECT / "config.example.json"
AUTO_STATE = PROJECT / ".setup-model-state.json"


def read_config() -> dict:
    with CONFIG.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("config.json 顶层必须是 JSON 对象")
    return config


def write_json_atomically(path: Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=PROJECT, prefix=f".{path.stem}-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_config(default_model: str) -> None:
    if not default_model.strip():
        raise ValueError("安装器推荐的视觉模型不能为空")
    explicit_model = bool(os.environ.get("MEDIA_INDEX_MODEL"))
    if CONFIG.exists():
        config = read_config()
        current = config.get("vision_model")
        if not isinstance(current, str) or not current.strip():
            raise ValueError("config.json 缺少非空 vision_model")
        if AUTO_STATE.exists():
            try:
                with AUTO_STATE.open(encoding="utf-8") as handle:
                    state = json.load(handle)
            except (OSError, ValueError):
                state = {"mode": "manual"}
        else:
            # Upgrade an older installation only when its model already matches
            # this machine's recommendation. Otherwise, treat it as user input.
            state = {"mode": "auto" if current == default_model else "manual", "last_auto_model": current}
        auto_owned = state.get("mode") == "auto" and current == state.get("last_auto_model")
        if explicit_model:
            if current != default_model:
                config["vision_model"] = default_model
                write_json_atomically(CONFIG, config)
            write_json_atomically(AUTO_STATE, {"mode": "manual", "last_auto_model": state.get("last_auto_model")})
            print(f"已按 MEDIA_INDEX_MODEL 设置视觉模型：{default_model}")
        elif auto_owned:
            if current != default_model:
                config["vision_model"] = default_model
                write_json_atomically(CONFIG, config)
                print(f"硬件已变化，自动调整视觉模型：{current} → {default_model}")
            else:
                print(f"已有配置，自动模型保持：{current}")
            write_json_atomically(AUTO_STATE, {"mode": "auto", "last_auto_model": default_model})
        else:
            write_json_atomically(AUTO_STATE, {"mode": "manual", "last_auto_model": state.get("last_auto_model")})
            print(f"已有手动配置，保留视觉模型：{current}；请确认新机器的内存足够。")
        return
    if not EXAMPLE.is_file():
        raise FileNotFoundError(f"缺少配置模板：{EXAMPLE}")
    with EXAMPLE.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("config.example.json 顶层必须是 JSON 对象")
    config["vision_model"] = default_model
    config["asr_backend"] = "auto"
    write_json_atomically(CONFIG, config)
    write_json_atomically(AUTO_STATE, {"mode": "manual" if explicit_model else "auto", "last_auto_model": default_model})
    print(f"已创建配置：{CONFIG}")


def model_name() -> None:
    model = read_config().get("vision_model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("config.json 缺少非空 vision_model")
    print(model.strip())


def memory_gib() -> float:
    system = platform.system()
    if system == "Darwin":
        try:
            output = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=5, stderr=subprocess.DEVNULL)
            return int(output.strip()) / 1024**3
        except (OSError, subprocess.CalledProcessError):
            # Some managed macOS environments deny sysctl but expose the same
            # hardware fact through system_profiler.
            raw = subprocess.check_output(["system_profiler", "SPHardwareDataType", "-json"], timeout=20, stderr=subprocess.DEVNULL)
            entry = json.loads(raw)["SPHardwareDataType"][0]["physical_memory"]
            match = re.fullmatch(r"([\d.]+)\s*(GB|MB|TB)", entry.strip(), re.IGNORECASE)
            if not match:
                raise RuntimeError(f"无法识别 Mac 内存容量：{entry}")
            value, unit = float(match.group(1)), match.group(2).upper()
            return value * {"MB": 1 / 1024, "GB": 1, "TB": 1024}[unit]
    if system == "Linux":
        with Path("/proc/meminfo").open(encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2
        raise RuntimeError("无法读取 /proc/meminfo 中的内存容量")
    if system == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("无法读取 Windows 内存容量")
        return status.ullTotalPhys / 1024**3
    raise RuntimeError(f"暂不支持的系统：{system}")


def suggest_model(backend: str) -> None:
    ram = memory_gib()
    if ram >= 31:
        model = "qwen3.8:27b-mlx" if backend == "mlx" else "qwen3.8:27b"
        quality = "详细描述档"
    elif ram >= 15:
        model = "qwen3.5:9b-mlx" if backend == "mlx" else "qwen3.5:9b"
        quality = "中等档；细节准确率可能低于 27B"
    elif ram >= 7:
        model = "qwen3.5:4b-mlx" if backend == "mlx" else "qwen3.5:4b"
        quality = "轻量档；建议人工复核描述和 OCR"
    else:
        raise RuntimeError(f"物理内存约 {ram:.1f} GB；自动安装至少需要 8 GB，当前无法保证本地视觉模型可用")
    print(f"检测内存约 {ram:.1f} GB；选择 {model}（{quality}）", file=sys.stderr)
    print(model)


def link_ffmpeg() -> None:
    import imageio_ffmpeg

    binary = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"FFmpeg 安装包没有可用二进制文件：{binary}")
    target_dir = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    target_dir.mkdir(parents=True, exist_ok=True)
    link = target_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.name == "nt":
        if not link.is_file() or not filecmp.cmp(binary, link, shallow=False):
            shutil.copy2(binary, link)
    elif link.is_symlink():
        if link.resolve() != binary:
            link.unlink()
            link.symlink_to(binary)
    elif link.exists():
        if not filecmp.cmp(binary, link, shallow=False):
            raise FileExistsError(f"虚拟环境里已有其他 FFmpeg：{link}；请检查后重跑安装")
    else:
        link.symlink_to(binary)
    result = subprocess.run([str(link), "-version"], capture_output=True, text=True, check=True, timeout=15)
    print(f"FFmpeg 可用：{result.stdout.splitlines()[0]}")


def download_asr(backend: str) -> None:
    config = read_config()
    if backend == "mlx":
        model = config.get("mlx_asr_model")
        if not isinstance(model, str) or not model:
            raise ValueError("config.json 缺少 mlx_asr_model")
        if Path(model).expanduser().is_dir():
            print(f"本地 MLX 语音模型已存在：{model}")
            return
        from huggingface_hub import snapshot_download

        try:
            path = snapshot_download(repo_id=model, local_files_only=True)
        except Exception:
            path = snapshot_download(repo_id=model)
        print(f"MLX 语音模型已缓存：{path}")
    elif backend == "faster":
        model = config.get("faster_whisper_model")
        if not isinstance(model, str) or not model:
            raise ValueError("config.json 缺少 faster_whisper_model")
        if Path(model).expanduser().is_dir():
            print(f"本地 faster-whisper 模型已存在：{model}")
            return
        from faster_whisper import download_model

        try:
            path = download_model(model, local_files_only=True)
        except Exception:
            path = download_model(model)
        print(f"faster-whisper 语音模型已缓存：{path}")
    else:
        raise ValueError(f"未知语音后端：{backend}")


def verify(backend: str) -> None:
    from PIL import Image
    import imageio_ffmpeg

    assert Image is not None and Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
    gpu_available = True
    if backend == "mlx":
        try:
            import mlx_whisper  # noqa: F401
        except ImportError as exc:
            if "No Metal device available" not in str(exc):
                raise
            gpu_available = False
            print("提示：当前受限制的会话不能访问 Mac GPU；MLX 软件包已安装，请在普通终端或 Finder 中运行试跑。")
    elif backend == "faster":
        import faster_whisper  # noqa: F401
    else:
        raise ValueError(f"未知语音后端：{backend}")
    if gpu_available:
        print(f"Python {sys.version.split()[0]}、Pillow、FFmpeg、{backend} 语音后端均可使用")
    else:
        print(f"Python {sys.version.split()[0]}、Pillow、FFmpeg 可用；当前会话无法验证 MLX 运行")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare-config")
    prep.add_argument("--default-model", required=True)
    suggested = sub.add_parser("suggest-model")
    suggested.add_argument("--backend", required=True, choices=("mlx", "faster"))
    sub.add_parser("model-name")
    sub.add_parser("link-ffmpeg")
    for name in ("download-asr", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--backend", required=True, choices=("mlx", "faster"))
    args = parser.parse_args()
    if args.command == "prepare-config":
        prepare_config(args.default_model)
    elif args.command == "suggest-model":
        suggest_model(args.backend)
    elif args.command == "model-name":
        model_name()
    elif args.command == "link-ffmpeg":
        link_ffmpeg()
    elif args.command == "download-asr":
        download_asr(args.backend)
    else:
        verify(args.backend)


if __name__ == "__main__":
    main()
