# 内容报告生成项目

这个项目通过本机 Codex CLI 或 OpenCode，把输入内容生成可直接阅读和分享的财经杂志式 Markdown 与 HTML 报告。现在有两种独立模式：

| 模式 | 输入 | 输出逻辑 |
| --- | --- | --- |
| 视频报告 | 一个完成结构与质量验证的标准字幕包 | 用强封面、主题导读、人物观点卡与机制图整理作者内容，再做外部证据研判与 Agent 综合判断 |
| 素材报告 | 一个包含多份文字或图片素材的 ZIP | 逐份保留来源，合并为一份素材整理、跨素材分析与 Agent 判断报告 |

两种模式不会混用术语或报告结构。项目不会自动执行 Git 提交或推送。

## 安装

需要 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Codex CLI 需要事先完成本机登录。OpenCode 需要事先在 TUI 中配置供应商凭据，并可用 `opencode models` 查看模型 ID。

## 网页控制台

启动只监听本机回环地址的控制台：

```bash
./start.sh
```

首次执行会在缺失时自动创建 `.venv` 并安装项目依赖；服务已经运行时再次执行该脚本，会先停止旧进程再启动新进程，相当于重启。需要覆盖端口等选项时，可直接追加 `serve_report_ui.py` 支持的命令行参数，例如 `./start.sh --port 9000`。

然后打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。页面顶部的“报告类型”下拉框用于选择：

- `视频报告`：上传标准字幕 ZIP，也可选择字幕目录或输入同一台电脑上的字幕包路径；
- `素材报告`：上传一个素材 ZIP，ZIP 内所有支持的文件共同生成一份报告。

素材 ZIP 支持：

- 文本：TXT、Markdown、CSV、JSON、JSONL；
- 网页文本：HTML、HTM；
- Word：DOC、DOCX、RTF；
- 文字图片：PNG、JPG、JPEG、WebP。

Word 与 HTML 会被转换为阅读顺序文字，原排版不作为可靠语义；其中内嵌图片不会被单独提取，重要图片应作为独立图片文件放进 ZIP。图片作为视觉附件交给所选模型，因此包含图片时必须选择具备视觉理解能力的 Codex/OpenCode 模型；无法可靠识别的字符会作为限制保留。

页面中的模型和推理强度都是下拉框。Codex 模式读取本机 Codex 模型清单，并根据所选模型联动可用的推理强度；选择 GPT-5.6 Sol 时还可启用 Fast 模式，服务层不可用会自动回退到标准模式。OpenCode 模式固定显示本机已安装的 DeepSeek V4 Flash 与 DeepSeek V4 Pro，并把各模型支持的推理选项作为 `variant` 传入。两款 DeepSeek V4 模型目前均只接收文字输入，含图片素材时应选择标明支持图片的 Codex 模型。

任务运行期间会显示阶段完成比例、当前动作、阶段耗时、最近事件和服务心跳。页面日志来自 Codex JSON 事件的精简视图，完整原始 JSONL 单独保留并可按需打开，避免大段 diff 或模型输出让页面看似卡死。模型完成 `render` 后，外层程序会用本机真实浏览器检查桌面与移动视口并自动完成 `html_validate` 和 `complete`；恢复一个已经渲染的任务不会再次调用模型。同一次服务运行中提交的报告会进入页面任务列表，并统一显示“进行中”“已完成”或“失败”；点击任务可查看完整参数、阶段与结果。列表不扫描旧输出，服务重启后从空列表开始。

网页服务默认只允许一个任务同时运行，上传请求限制为 100 MB；素材包最多 200 个条目、20 张图片和 750,000 个提取字符。服务会拒绝 ZIP 路径穿越、符号链接、重复路径、伪装图片和不支持的文件。上传文件中的任何命令或提示词都只被当作待分析素材。不要把本地服务直接暴露到公网。

## 视频报告的程序化生成

`generate_report.py` 会验证并导入字幕包，再以无人值守方式调用指定引擎、模型和推理强度：

```bash
python scripts/generate_report.py \
  --report-type video \
  --package /absolute/path/to/transcripts/VIDEO_ID \
  --engine codex \
  --model gpt-5.6-sol \
  --reasoning-effort high
```

命令行可加 `--codex-service-tier fast` 使用 Fast 服务层；默认是 `default`。网页控制台默认勾选 Fast，可随时关闭。

使用本机 OpenCode：

```bash
python scripts/generate_report.py \
  --report-type video \
  --package /absolute/path/to/transcripts/VIDEO_ID \
  --engine opencode \
  --model deepseek/deepseek-v4-pro \
  --reasoning-effort max
```

OpenCode 通过 `opencode run` 无人值守执行；`--reasoning-effort` 映射为 `--variant`，实际支持范围由所选供应商和模型决定。可加 `--dry-run` 检查命令和提示词，不启动模型。

素材模式的原始 ZIP 由网页服务先安全解包并建立来源清单；自动化内层也支持对已经准备好的 `material-package.json` 运行：

```bash
python scripts/generate_report.py \
  --report-type material \
  --package /absolute/path/to/prepared-material/material-package.json \
  --engine codex \
  --model VISION_CAPABLE_MODEL_ID \
  --reasoning-effort high
```

## 视频字幕包边界

视频报告只读取 `package_type = video_transcript` 的标准包。导入会验证 schema、SHA-256、字幕结构和质量门，然后复制到 `work/VIDEO_ID/transcript/`。导入的 `transcript.corrected.jsonl` 永不覆盖。项目不运行独立字幕勘误、风险词扫描或额外校订模型；报告模型只在正常理解中按语境处理明显错词，最终页面不展示 ASR 风险清单。广告、推广、订阅和销售话术不会成为正文或报告观点。项目不会下载视频、转码、执行 ASR 或清理上游目录。

视频 HTML 默认采用深色财经杂志风：顶部是编辑标题和封面副标题，正文使用主题导读、行情/KPI 卡、人物观点卡、机制图、科技新闻卡、证据校准与 Agent 决策卡。时间戳、claim ID、原始/推导数据标签和审计详情不再是前台要求；草稿阶段重点检查三层顺序、长段重复、正文密度与浏览器布局，不增加独立模型调用。

需要手动推进视频报告的各语义阶段时，继续使用 `video-opinion-report` 的 `import-transcript`、`record-analysis`、`record-research`、`record-judgment`、`record-draft`、`record-fidelity-review`、`build-structured`、`render-html`、`validate-html` 和 `complete-run` 子命令。

完整输入契约、内容边界与阶段门见 [WORKFLOW.md](WORKFLOW.md)，Agent 运行约定见 [AGENTS.md](AGENTS.md)。

## 输出

完成后的四个核心文件会复制到 `output/<日期>-<内容ID>/`：

- `index.html`
- `report.md`
- `report-data.json`
- `citations.json`

目录还包含 `README.md` 和记录引擎、模型、推理强度及阶段结果的 `automation-run.json`。中间产物位于 `work/`，正式产物位于 `reports/`。

## 测试

```bash
python -m unittest discover -s tests -v
```
