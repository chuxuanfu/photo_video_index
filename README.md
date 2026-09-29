# 本地照片与视频文字索引

这个项目为每个 `Photo/`、`Video/` 文件夹各生成一份 `index.json`。照片描述、视频定时抽帧描述、带时间码的语音转录都写在 JSON 中；视频的原始抽帧、音轨和独立转录保存在 `Video/Resources/<视频相对路径及完整文件名>/`。搜索时可以打开原图，或跳到视频命中的时间。

项目已放在 `~/photo_video_index`。详细的 [配置、依赖和架构说明](PROJECT_CONFIG.md) 包含两张结构图；[Demo 实跑结果](DEMO_RESULTS.md) 展示当前样例的索引、搜索与时间码。

## 在新电脑安装

- macOS：双击 `setup.command`，或在终端运行 `bash ~/photo_video_index/setup.sh`。
- Linux x86-64 / ARM64：运行 `bash ~/photo_video_index/setup.sh`。
- Windows x86-64：双击 `setup.cmd`。若从 PowerShell 启动，可运行 `powershell -ExecutionPolicy Bypass -File .\setup.ps1`。

安装器创建本项目的 `.venv`，安装 Python 3.12、Pillow、FFmpeg 和适合平台的 Whisper 实现；准备 Ollama，并按内存自动选择和下载有视觉能力的开源模型。首次安装需要联网和下载空间；本机若已有模型则复用。支持内存至少约 8 GiB 的电脑；在低内存设备上会选较小模型，描述质量可能下降。模型和原始媒体不会随 `index.json` 同步。

安装器从 `config.example.json` 创建本机 `config.json`，并用 `.setup-model-state.json` 记录自动选的模型。整项目复制到新电脑后，自动模型会按新硬件重选；你手动修改的 `vision_model` 会保留。抽帧间隔、语音后端或搜索端口可直接编辑 `config.json`。命令行显式参数优先。当前这台 96 GB Apple Silicon Mac 选择 `qwen3.8:27b-mlx` 与 MLX Whisper；其他受支持电脑会按内存选可运行的视觉模型，并可用 faster-whisper 在本机转录。

## 先看 Demo

在 macOS 或 Linux 的终端运行：

```bash
cd ~/photo_video_index
./.venv/bin/python media_index.py index --photo-root demo/Photo --video-root demo/Video
./.venv/bin/python media_index.py serve --index demo/Photo/index.json --index demo/Video/index.json --no-semantic --open-browser
```

Windows 把 `./.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`。样例包含一张图、两段视频和一段合成英文语音。页面可搜索“蓝色正方形 红色圆形”、`red suitcase station` 或 `00:01:00.000`；点开视频结果会跳到命中时间。第二次建索引会跳过未变化的媒体。

## 处理自己的文件夹

macOS 可双击 `试跑.command`，选择 `Photo` 或 `Video` 文件夹；它只处理每类第一个文件，视频最多三帧，便于快速检查。完整建索引可在终端运行：

```bash
cd ~/photo_video_index
./.venv/bin/python media_index.py doctor
./.venv/bin/python media_index.py index --photo-root "/你的/Photo" --video-root "/你的/Video"
./.venv/bin/python media_index.py serve --index "/你的/Photo/index.json" --index "/你的/Video/index.json" --open-browser
```

只处理照片或视频时，可省略另一种 `--*-root`。命令行搜索示例：

```bash
./.venv/bin/python media_index.py search --index "/你的/Photo/index.json" --index "/你的/Video/index.json" --query "红色行李箱在车门旁"
```

搜索默认按本机配置决定是否调用 Ollama 扩展与重排文字候选；加 `--no-semantic` 可只做快速文字匹配。完整索引默认每 30 秒抽一帧；30 秒内短暂出现的物体可能漏掉，可把 `config.json` 中的 `frame_interval_seconds` 改为 `10` 后重新建索引。

## 跨电脑只复制 JSON

每个资料库只需复制它的 `index.json` 就能在另一台电脑检索文字和视频时间码。原件定位始终使用 **索引所在媒体根目录 + 条目的 `relative_path`**。若索引副本与原媒体分开，可以用 `--media-root 'library_id=/该电脑可访问的/Photo或Video'` 指定映射；`library_id` 可在 JSON 开头找到。目标电脑无法访问原媒体时仍能找到文件相对路径和视频时间，但不能打开原件。

索引中包含画面细节及对白，复制时也会复制这些文字。自动描述与转录可能出错，正式处理前建议抽查少量条目。搜索页只监听本机 `127.0.0.1`；在其他设备上打开原件需要该设备本身能访问文件，并运行自己的搜索服务。
