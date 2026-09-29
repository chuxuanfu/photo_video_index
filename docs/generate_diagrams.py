#!/usr/bin/env python3
"""Render the two project diagrams as editable SVG and presentation-size PNG."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).resolve().parent
FONT_LIGHT = "/System/Library/Fonts/STHeiti Light.ttc"
FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
COLORS = {
    "bg": "#F4F7FB",
    "ink": "#172A3F",
    "muted": "#4D647A",
    "line": "#D8E1EA",
    "white": "#FFFFFF",
    "blue": "#275DAB",
    "blue_pale": "#E7F0FE",
    "teal": "#087E83",
    "teal_pale": "#DFF5F1",
    "amber": "#AC6515",
    "amber_pale": "#FFF1DC",
    "purple": "#7147A1",
    "purple_pale": "#EFE8FA",
}


class Canvas:
    def __init__(self, width: int, height: int):
        self.width, self.height, self.scale = width, height, 2
        self.image = Image.new("RGB", (width * self.scale, height * self.scale), COLORS["bg"])
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<rect width="{width}" height="{height}" fill="{COLORS["bg"]}"/>',
        ]

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_LIGHT, size * self.scale)

    def box(self, x: int, y: int, w: int, h: int, *, fill: str = "white", stroke: str | None = None, radius: int = 20, sw: int = 2):
        color = COLORS.get(fill, fill)
        edge = COLORS.get(stroke, stroke) if stroke else None
        self.draw.rounded_rectangle((x*self.scale, y*self.scale, (x+w)*self.scale, (y+h)*self.scale),
                                    radius=radius*self.scale, fill=color, outline=edge, width=sw*self.scale)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{color}"'
                        + (f' stroke="{edge}" stroke-width="{sw}"' if edge else "") + '/>')

    def text(self, x: int, y: int, value: str, size: int = 24, *, color: str = "ink", bold: bool = False):
        fill = COLORS.get(color, color)
        self.draw.text((x*self.scale, y*self.scale), value, fill=fill, font=self._font(size, bold), anchor="lt")
        self.svg.append(f'<text x="{x}" y="{y+3}" fill="{fill}" font-size="{size}"'
                        + f' font-family="STHeiti, Heiti SC, PingFang SC, Arial, sans-serif" font-weight="{700 if bold else 400}"'
                        + f' dominant-baseline="hanging">{escape(value)}</text>')

    def lines(self, x: int, y: int, values: list[str], *, size: int = 23, gap: int = 37, color: str = "muted", bold: bool = False):
        for i, value in enumerate(values):
            self.text(x, y + i*gap, value, size, color=color, bold=bold)

    def line(self, x1: int, y1: int, x2: int, y2: int, *, color: str = "line", width: int = 3):
        fill = COLORS.get(color, color)
        self.draw.line((x1*self.scale, y1*self.scale, x2*self.scale, y2*self.scale), fill=fill, width=width*self.scale)
        self.svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{fill}" stroke-width="{width}"/>')

    def arrow(self, x1: int, y1: int, x2: int, y2: int, *, color: str = "blue", width: int = 4):
        fill = COLORS.get(color, color)
        self.line(x1, y1, x2, y2, color=fill, width=width)
        if x2 >= x1 and abs(x2-x1) >= abs(y2-y1):
            p = [(x2,y2),(x2-16,y2-9),(x2-16,y2+9)]
        elif x2 < x1 and abs(x2-x1) >= abs(y2-y1):
            p = [(x2,y2),(x2+16,y2-9),(x2+16,y2+9)]
        elif y2 >= y1:
            p = [(x2,y2),(x2-9,y2-16),(x2+9,y2-16)]
        else:
            p = [(x2,y2),(x2-9,y2+16),(x2+9,y2+16)]
        self.draw.polygon([(a*self.scale,b*self.scale) for a,b in p], fill=fill)
        self.svg.append('<polygon points="'+' '.join(f'{a},{b}' for a,b in p)+f'" fill="{fill}"/>')

    def save(self, stem: str):
        (OUT / f"{stem}.svg").write_text("\n".join(self.svg + ["</svg>"]) + "\n", encoding="utf-8")
        self.image.save(OUT / f"{stem}.png", optimize=True)


def project_structure():
    c = Canvas(1700, 1290)
    c.text(82, 53, "项目与资料库的文件结构", 46, bold=True)
    c.text(85, 112, "项目可搬到新电脑；原始照片和视频可存于本机、外接盘或 NAS。", 24, color="muted")

    c.box(80, 190, 750, 940, fill="white", stroke="line")
    c.box(104, 214, 702, 72, fill="blue_pale", radius=13)
    c.text(130, 230, "~/photo_video_index/   项目目录", 30, color="blue", bold=True)
    rows = [
        ("media_index.py", "建索引、检索、网页预览的主程序"),
        ("config.example.json", "可用配置模板"),
        ("config.json", "安装器生成；按电脑调整参数"),
        (".setup-model-state.json", "记录当前机器的模型选择"),
        ("setup.command", "Mac 双击安装入口"),
        ("setup.sh / setup.cmd", "Linux / Windows 安装入口"),
        ("setup.ps1 / setup_support.py", "平台检测与安装辅助逻辑"),
        ("requirements-*.txt", "固定 Python 依赖版本"),
        ("试跑.command", "交互选择 Photo / Video 试跑"),
        ("README.md", "快速使用说明"),
        ("PROJECT_CONFIG.md", "配置、依赖和架构详解"),
        ("DEMO_RESULTS.md", "公开样例的实际试跑结果"),
        ("docs/", "可编辑 SVG 与 PNG 结构图"),
        ("tests/", "核心行为检查"),
        ("demo/", "可直接查看的非私人样例资料"),
        (".venv/", "安装后生成；本机 Python 环境"),
    ]
    for i, (name, detail) in enumerate(rows):
        y = 315 + i*52
        c.box(116, y+8, 10, 10, fill="blue", radius=3)
        c.text(144, y, name, 21, bold=True)
        c.text(466, y+1, detail, 19, color="muted")
        if i < len(rows)-1:
            c.line(143, y+45, 777, y+45, color="line", width=1)

    c.box(870, 190, 750, 355, fill="white", stroke="line")
    c.box(894, 214, 702, 70, fill="teal_pale", radius=13)
    c.text(920, 231, "Photo/   照片资料库", 30, color="teal", bold=True)
    c.lines(920, 308, ["2024/海边.jpg   ← 原件可在子文件夹", "index.json          ← 这一份包含所有照片的文字索引", "定位：Photo/ + relative_path"], size=23, gap=62)

    c.box(870, 570, 750, 560, fill="white", stroke="line")
    c.box(894, 594, 702, 70, fill="amber_pale", radius=13)
    c.text(920, 611, "Video/   视频资料库", 30, color="amber", bold=True)
    c.lines(920, 685, [
        "旅行.mp4                 ← 原视频",
        "index.json               ← 文字、帧时间、完整转录",
        "Resources/旅行.mp4/      ← 仅源端保留",
        "    frames/*.jpg            定时截取的关键帧",
        "    audio.wav               提取的音轨",
        "    transcript.json/.srt    独立转录文件",
        "嵌套视频按原相对路径建立 Resources 子目录",
        "只同步 Video/index.json；原件仍需另行访问",
    ], size=23, gap=49)
    c.text(88, 1175, "跨设备只复制各资料库的 index.json；Resources 和原媒体无须随索引复制。", 25, color="purple", bold=True)
    c.save("项目结构")


def data_flow():
    c = Canvas(1850, 1350)
    c.text(80, 48, "从原媒体到文字搜索：一份 JSON 走遍设备", 46, bold=True)
    c.text(82, 106, "原件留在 Photo / Video；描述、对白与视频时间码都进入各自的 index.json。", 24, color="muted")

    for x, label in [(80, "① 原始文件夹"), (650, "② 本机生成索引"), (1270, "③ 可复制的索引")]:
        c.text(x+8, 190, label, 27, color="muted", bold=True)

    c.box(80, 245, 500, 255, fill="white", stroke="line")
    c.box(102, 267, 456, 59, fill="teal_pale", radius=12)
    c.text(123, 280, "Photo/", 28, color="teal", bold=True)
    c.lines(121, 350, ["2024/海边.jpg 等原始照片", "可有多层子目录", "生成 Photo/index.json"], size=24, gap=46)

    c.box(80, 523, 500, 328, fill="white", stroke="line")
    c.box(102, 545, 456, 59, fill="amber_pale", radius=12)
    c.text(123, 558, "Video/", 28, color="amber", bold=True)
    c.lines(121, 630, [
        "旅行.mp4 等原始视频",
        "生成 Video/index.json",
        "Resources/旅行.mp4/ 保留：",
        "帧图、audio.wav、转录 JSON/SRT",
    ], size=23, gap=48)

    c.box(650, 245, 550, 606, fill="white", stroke="line")
    c.box(673, 268, 504, 63, fill="blue_pale", radius=12)
    c.text(693, 280, "media_index.py index", 28, color="blue", bold=True)
    c.lines(690, 365, [
        "照片：Pillow 校正方向与缩放",
        "视频：FFmpeg 默认每 30 秒抽一帧",
        "视频：FFmpeg 提取 16 kHz 音轨",
        "视觉：Ollama + qwen3.8:27b-mlx",
        "语音：本地 Whisper 转录",
        "合并：帧描述 + 同时间窗对白",
        "增量更新；原文件不改动",
    ], size=24, gap=60)

    c.box(1270, 245, 500, 255, fill="white", stroke="line")
    c.box(1292, 267, 456, 59, fill="teal_pale", radius=12)
    c.text(1312, 280, "Photo/index.json", 28, color="teal", bold=True)
    c.lines(1312, 351, ["library_id + relative_path", "详细 description / OCR / tags", "每个照片根目录仅这一份索引"], size=22, gap=46)

    c.box(1270, 523, 500, 328, fill="white", stroke="line")
    c.box(1292, 545, 456, 59, fill="amber_pale", radius=12)
    c.text(1312, 558, "Video/index.json", 28, color="amber", bold=True)
    c.lines(1312, 632, [
        "relative_path + duration_ms",
        "frames[]：at_ms + 画面描述",
        "transcript[]：时间段 + 对白",
        "moments[]：画面与对白结合",
    ], size=22, gap=48)

    c.arrow(583, 393, 642, 393, color="teal")
    c.arrow(583, 688, 642, 688, color="amber")
    c.arrow(1205, 394, 1262, 394, color="teal")
    c.arrow(1205, 683, 1262, 683, color="amber")

    c.box(80, 915, 650, 325, fill="white", stroke="line")
    c.box(102, 938, 606, 62, fill="purple_pale", radius=12)
    c.text(124, 950, "④ 跨设备：只复制 JSON", 28, color="purple", bold=True)
    c.lines(124, 1022, [
        "Photo/index.json + Video/index.json",
        "原件、帧图、音频、独立转录无需同步",
        "JSON 内已有可检索文字和时间码",
        "复制的索引不会凭空带来原件",
    ], size=23, gap=49)

    c.box(800, 915, 970, 325, fill="white", stroke="line")
    c.box(822, 938, 926, 62, fill="blue_pale", radius=12)
    c.text(844, 950, "⑤ 检索、定位与打开", 28, color="blue", bold=True)
    c.lines(845, 1022, [
        "search / serve 读取一份或多份 index.json，按文字匹配；可选本机模型重排",
        "原件 = index.json 所在目录 / relative_path",
        "索引副本与原件分离时，用 --media-root 指定可访问的媒体根目录",
        "照片打开原图；视频播放器跳到命中的帧或对白时间码",
    ], size=22, gap=49)

    c.arrow(735, 1077, 792, 1077, color="purple")
    c.text(85, 1280, "没有原媒体访问权限时，搜索仍返回文件夹、文件相对路径和时间码，但不能打开原件。", 23, color="muted")
    c.save("数据流程")


if __name__ == "__main__":
    project_structure()
    data_flow()
    print("已生成：", str(OUT / "项目结构.png"), str(OUT / "数据流程.png"), sep="\n")
