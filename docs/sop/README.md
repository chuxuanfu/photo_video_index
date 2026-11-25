# 中文 SOP：源稿、生成与检查

阅读 [MemorySearch 本机使用 SOP（PDF）](../MemorySearch-SOP-zh-CN.pdf)。
内容维护在 [Markdown 源稿](../MemorySearch-SOP-zh-CN.md)，不要直接修改 PDF。

本指南面向已安装 `local-supervised-index` 启动器及照片自动核验原生查看器的版本。
`<MEMORYSEARCH_PROJECT>` 是安装文件夹的占位名。仓库的其他历史版本不一定包含这些入口；
本提交只提供文档，不安装、升级、启动或重建应用和索引。

## 四个交付文件

```text
docs/MemorySearch-SOP-zh-CN.pdf
docs/MemorySearch-SOP-zh-CN.md
docs/sop/build_sop.swift
docs/sop/README.md
```

所有操作图均由代码绘制，是合成示意。搜索词、演示图片和视频控件仅说明用法，
不来自用户图库或搜索记录，也不代表搜索准确率保证。

## 生成 PDF

在包含上述 `docs` 目录的根目录运行：

```sh
task_sop_qa="$(mktemp -d /tmp/memorysearch-sop-qa.XXXXXX)"
/usr/bin/swift -module-cache-path "$task_sop_qa/module-cache" \
  docs/sop/build_sop.swift \
  docs/MemorySearch-SOP-zh-CN.md \
  docs/MemorySearch-SOP-zh-CN.pdf \
  "$task_sop_qa"
```

需要 macOS 已有的 Swift、AppKit、CoreText、CoreGraphics、PDFKit 和苹方字体。
不需要搜索模型、图库、网络服务或额外 Python、Node、排版依赖。
源稿用 `<!-- PAGE -->` 显式分页；生成器仅支持本稿使用的简单标题、段落、列表、
两列表格、提示及四种合成图示。

成功退出码为 `0`。失败会输出具体检查错误，禁止通过静默缩小正文掩盖溢出。
最终 PDF 仅在生成与自动检查通过后替换；构建时间等元数据可能使重建后的文件哈希不同。

## 自动与逐页检查

- 恰好 7 页 A4，每页可提取中文，8 项关键文案完整。
- 正文不超出保留区域，所用中文、等宽和符号字体全部嵌入。
- 元数据使用通用文档名称，移除构建机器的系统版本信息。
- 提取文本不含用户绝对路径或本机文件 URL。
- 每页渲染成约 1191 × 1684 像素的 PNG，另生成 `extracted.txt`、`layout-boxes.json` 和 `qa.json`。

这些检查文件、PNG、模块缓存及临时文件留在临时目录，**不要提交**。
逐页打开 `page-01.png` 至 `page-07.png`，检查中文、按钮文案、图示、表格、页码和边缘；
再核对提取文本和字体报告。自动通过不能替代目视复核；生成器的 `visual_review` 默认为待复核。

本公开版已完成 7/7 页目视检查、中文提取、字体嵌入与版面检查。
附加的 PDF 结构审计确认：没有附件、批注、表单、打开动作、JavaScript 或截图位图，
没有个人路径、用户名、凭据及真实图库内容。文件夹标识已替换为通用占位符，
操作示意由矢量绘图生成。

## 验证边界

启动、核验分组、展开、恢复原序、回退和播放说明来自相应版本的源码核对及合成客户端验证。
它们不是私人图库验收、真实搜索延迟测量，也不能证明未安装相应版本的系统具备这些功能。
没有用真实用户截图、媒体、查询、逐条判定或日志补充本指南。
