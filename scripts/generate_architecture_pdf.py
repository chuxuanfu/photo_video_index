#!/usr/bin/env python3
"""Generate a public-safe architecture guide; contains no library data."""
from pathlib import Path
import sys
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.pagesizes import A4

OUTPUT = Path(sys.argv[1])
FONT = '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
pdfmetrics.registerFont(TTFont('CJK', FONT))
W,H=A4
C=canvas.Canvas(str(OUTPUT),pagesize=A4)
C.setTitle('Photo Video Index - 项目架构介绍')
C.setAuthor('Photo Video Index')
INK=HexColor('#16303a');TEAL=HexColor('#167d8d');PALE=HexColor('#edf7f7');GRAY=HexColor('#53666d');AMBER=HexColor('#b77519')
BODY=ParagraphStyle('body',fontName='CJK',fontSize=10.2,leading=16,textColor=INK,wordWrap='CJK')
SMALL=ParagraphStyle('small',parent=BODY,fontSize=8.8,leading=13)
TITLE=ParagraphStyle('title',parent=BODY,fontSize=13,leading=18,textColor=TEAL)

def paragraph(text,x,y,w,style=BODY):
 p=Paragraph(text,style);_,height=p.wrap(w,1000);p.drawOn(C,x,y-height);return y-height

def top(page,kicker,title,subtitle):
 C.setFillColor(TEAL);C.rect(0,H-11,W,11,fill=1,stroke=0)
 C.setFillColor(GRAY);C.setFont('CJK',9);C.drawString(44,H-42,'PHOTO VIDEO INDEX  /  '+kicker)
 C.setFillColor(INK);C.setFont('CJK',25);C.drawString(44,H-81,title)
 paragraph(subtitle,44,H-104,W-88)
 C.setStrokeColor(HexColor('#d7e2e5'));C.line(44,40,W-44,40)
 C.setFillColor(GRAY);C.setFont('CJK',8);C.drawString(44,26,'公开架构说明 · 不含私人素材、文件名或资料库内容')
 C.drawRightString(W-44,26,f'{page} / 3')

def box(x,y,w,h,title,text,fill=PALE):
 C.setFillColor(fill);C.setStrokeColor(HexColor('#c6dfe2'));C.roundRect(x,y,w,h,9,fill=1,stroke=1)
 paragraph(title,x+12,y+h-10,w-24,TITLE)
 paragraph(text,x+12,y+h-35,w-24,SMALL)

def arrow(x1,y1,x2,y2):
 import math
 C.setStrokeColor(TEAL);C.setFillColor(TEAL);C.setLineWidth(1.3);C.line(x1,y1,x2,y2)
 angle=math.atan2(y2-y1,x2-x1);length=6
 p=C.beginPath();p.moveTo(x2,y2);p.lineTo(x2-length*math.cos(angle-.45),y2-length*math.sin(angle-.45));p.lineTo(x2-length*math.cos(angle+.45),y2-length*math.sin(angle+.45));p.close();C.drawPath(p,fill=1,stroke=0)

top(1,'原理','先理解内容，再保存成一份索引','原件只读。本机模型把画面和声音转换成文字证据，检索工具再读取这份文件。')
box(44,624,W-88,63,'1  选择一个资料库母目录','可以包含许多层子目录。只扫描用户明确选择的范围，不主动遍历家庭目录或其他存储。')
cols=[44,216,388];cw=163
for x in cols:arrow(W/2,624,x+cw/2,593)
box(cols[0],487,cw,106,'照片 / RAW','Pillow、HEIC 与 RAW 解码<br/>校正方向和颜色<br/>生成临时模型输入')
box(cols[1],487,cw,106,'视频 / 动图','AVFoundation / 图像解码<br/>HDR 转 SDR 与 sRGB<br/>记录实际解码帧时间')
box(cols[2],487,cw,106,'音轨','本机 Whisper 双模型核对<br/>保留起止时间<br/>不可靠的转录单独隔离')
arrow(125,487,168,451);arrow(297,487,230,451);arrow(469,487,432,451)
box(44,351,329,100,'2  本机视觉理解 + 本机 OCR','Qwen3.8 27B 视觉模型由 MLX-VLM 运行。<br/>输出主体、属性、关系、场景和不确定性。<br/>文字走独立 OCR 与核对流程，避免凭上下文猜字。<br/>多人近景和全景省略不可靠的个人属性。')
box(389,351,162,100,'带时间码的声音','转录与视频帧相互关联，<br/>但不把音轨中的说话人<br/>直接认成画面中的人物。')
arrow(208,351,255,317);arrow(470,351,343,317)
box(44,240,W-88,77,'3  质量门槛','检查 JSON、原件路径、时间码、空字段、输出截断和事实依据。失败进入待复审状态，不把“生成了文字”当成“已经准确完成”。',HexColor('#fff7e8'))
arrow(W/2,240,W/2,212)
box(44,146,W-88,66,'4  母目录的一份 index.json','目录与媒体映射 + 已验证文字 + 时间码 + 明确的未完成状态。原图、音轨和抽帧不嵌入这份文件。')
arrow(W/2,146,W/2,121)
paragraph('你可以在任何设备上，把同一份文件交给本机已有的 AI 工具做语义检索或 agentic search。项目不要求绑定某个搜索网页。',52,113,W-104,SMALL)
C.showPage()

top(2,'文件布局','为什么只需要复制一个文件','关键是相对路径：换电脑、换盘符或复制到备份位置，不需要改写每个条目。')
box(44,538,247,152,'原始资料库','ExampleMedia/<br/>　index.json  ← 唯一需要更新的索引<br/>　旅行/Photo/001.jpg<br/>　旅行/Video/clip.mp4<br/><br/>以上是示例目录，不是真实私人文件名。')
box(307,538,244,152,'项目的本机工作区','程序与公开文档：可进 GitHub<br/>.models/：下载的模型<br/>.venv-mlx/：Python 环境<br/>.local/：抽帧、音轨、测试与断点<br/><br/>这些运行文件不属于 GitHub 发布包。')
paragraph('原件定位规则',44,509,W-88,TITLE)
box(44,429,W-88,57,'索引所在母目录 + relative_path','例如：ExampleMedia + 旅行/Photo/001.jpg。relative_link 对空格、中文和特殊字符做 URL 编码。')
arrow(293,429,293,405)
box(44,341,W-88,64,'复制到对应的备份根目录','只复制 index.json，且备份中保持相同的子目录结构。若索引与原件分开，AI 工具只需知道一次新的母目录位置。')
paragraph('AI 工具从文件中读到什么？',44,314,W-88,TITLE)
items=[('文件身份','稳定 library_id、条目 ID、类型、相对路径和原件变化指纹'),('画面证据','摘要、具体描述、对象及属性、位置关系、OCR、不确定性'),('视频证据','抽帧的 at_ms、分时段描述、转录的 start_ms / end_ms'),('处理状态','已完成、部分完成、待处理、失败或待复审；不能混为一谈')]
y=284
for title,text in items:
 C.setFillColor(TEAL);C.setFont('CJK',10.4);C.drawString(48,y,title)
 paragraph(text,126,y+3,W-174,SMALL);y-=43
paragraph('“完整列出文件”只代表目录登记完成。没有做过视觉或声音分析的条目，不能假称支持按画面内容检索。',48,101,W-96,SMALL)
C.showPage()

top(3,'安装与质量','公开源码，本机运行','首发：Apple Silicon Mac，macOS 15+；至少 48 GiB 内存与 45 GiB 可用磁盘。64 GiB 已实测；48 GiB 尚未逐款验证。')
box(44,596,W-88,88,'一键安装的责任','下载隔离 Python 环境、常规依赖、经过核对的模型权重与 OpenCode；生成本机配置并检查服务。<br/>模型与环境可重新下载，不应该保存在 GitHub。安装不等于自动开始全库处理。')
paragraph('质量监督怎样闭环？',44,566,W-88,TITLE)
steps=[('挑选样本','不同格式、分辨率、光线、人物、文字和视频场景'),('对照并修正','检查真实画面与文字；修复提示词、解码、抽帧、OCR 或校验'),('用新素材盲测','修正后回归旧样本，再测试新素材；十组通过才放行')]
y=463
for i,(title,text) in enumerate(steps):
 box(44,y,W-88,63,str(i+1)+'  '+title,text,HexColor('#fff7e8') if i==1 else PALE)
 if i<2:arrow(W/2,y,W/2,y-17)
 y-=83
paragraph('全库运行仍要有自动检查、异常队列、断点与抽查。样本通过是质量门槛，不是“AI 永远不会出错”的承诺。当前版本的验证状态以项目质量报告为准。',48,273,W-96,SMALL)
box(44,145,W-88,80,'GitHub 应该有什么？','只保留源码、安装入口、依赖声明、公开配置模板与文档。<br/>不提交私人媒体、索引、文件清单、模型、环境、缓存、个人配置、测试画面或运行日志。')
paragraph('正式处理默认只访问本机模型服务；不自动同步到 NAS 或云端。原件保持只读，索引使用安全落盘和可恢复的处理状态。',48,120,W-96,SMALL)
C.save()
print(str(OUTPUT))
