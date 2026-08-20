# 视频字幕观点报告

这是拆分后的报告项目。它不下载视频、不转码、不执行 ASR，也不修改字幕；唯一输入是由字幕项目导出的、带时间戳且完成勘误的标准字幕包。项目在此基础上生成“字幕 / 作者内容 → 外部证据研判 → Agent 综合判断”三层 Markdown 与 HTML 报告。

## 与字幕项目的边界

```text
video-transport-text
  └─ transcripts/VIDEO_ID/package.json
        │  带 SHA-256 的标准字幕包
        ▼
video-subtitle-opinion-report
  └─ import → analyze → research → judgment → review → render
```

报告项目只读取字幕包中的文本、时间戳、勘误记录、质量结果和视频元数据。它不会访问或清理上游的原视频、音频、模型、Cookie 或下载配置。

## 安装

需要 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 从字幕包开始

```bash
video-opinion-report import-transcript \
  --package /absolute/path/to/video-transport-text/transcripts/VIDEO_ID
```

导入会验证 `package_type`、schema、所有必需文件、SHA-256、校订字幕结构、勘误记录和质量门。成功后，输入被复制到本项目的 `work/VIDEO_ID/transcript/`，后续运行不依赖上游目录。

语义阶段由 Agent 生成文件后，通过 CLI 校验并推进：

```bash
video-opinion-report record-analysis --video-id VIDEO_ID \
  --video-analysis work/VIDEO_ID/video-analysis.json \
  --opinions work/VIDEO_ID/opinions.jsonl

video-opinion-report record-research --video-id VIDEO_ID \
  --research-dir work/VIDEO_ID/research

video-opinion-report record-judgment --video-id VIDEO_ID \
  --judgment work/VIDEO_ID/agent-judgment.json

video-opinion-report record-draft --video-id VIDEO_ID \
  --markdown work/VIDEO_ID/draft-body.md

video-opinion-report record-fidelity-review --video-id VIDEO_ID \
  --review work/VIDEO_ID/fidelity-review-body.json
```

生成结构化产物和页面：

```bash
video-opinion-report build-structured --video-id VIDEO_ID \
  --video-analysis work/VIDEO_ID/video-analysis.json \
  --opinions work/VIDEO_ID/opinions.jsonl \
  --research-dir work/VIDEO_ID/research \
  --agent-judgment work/VIDEO_ID/agent-judgment.json \
  --fidelity-review work/VIDEO_ID/fidelity-review-body.json \
  --report-data reports/DATE-VIDEO_ID/report-data.json \
  --citations reports/DATE-VIDEO_ID/citations.json

video-opinion-report render-html --video-id VIDEO_ID \
  --markdown reports/DATE-VIDEO_ID/report.md \
  --template assets/report-template.html \
  --output reports/DATE-VIDEO_ID/index.html

video-opinion-report validate-html --video-id VIDEO_ID \
  --validation reports/DATE-VIDEO_ID/html-validation.json

video-opinion-report complete-run --video-id VIDEO_ID
```

完整内容规则、阶段门和文件契约见 [WORKFLOW.md](WORKFLOW.md)，Codex 运行约定见 [AGENTS.md](AGENTS.md)。

## 测试

```bash
python -m unittest discover -s tests -v
```
