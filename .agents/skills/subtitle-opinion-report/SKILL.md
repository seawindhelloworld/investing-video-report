---
name: subtitle-opinion-report
description: Generate, resume, review, or validate a three-layer opinion report from a validated timestamped transcript package. Use when Codex is asked to import corrected subtitles, extract creator meaning and subjective opinions, research those opinions, form source-backed Agent judgments, or publish the final Markdown and HTML report.
---

# Subtitle Opinion Report

This project begins with a transcript package. It never downloads media, runs ASR, or edits the transcript.

## Preflight

1. Read the project-root `WORKFLOW.md` and `AGENTS.md`.
2. Inspect `state/processed-reports.json` and `work/*/manifest.json`.
3. Resume an incomplete report run for the same video ID instead of importing it twice.
4. If no run exists, require a package exported by the transcript project and run:

```text
video-opinion-report import-transcript --package /absolute/path/to/transcript-package
```

Stop if schema, checksum, corrected-transcript structure, correction log, or quality validation fails.

## 1. Extract meaning and opinions

Read [content-boundaries.md](references/content-boundaries.md) and [opinion-schema.md](references/opinion-schema.md). Use `work/{video_id}/transcript/transcript.corrected.jsonl` as the only creator-content input. Keep the original ASR, correction log, validation result, modality, attribution, context, and unresolved terms visible for audit. Produce traceable `video-analysis.json` and `opinions.jsonl`, then register them with `record-analysis`.

## 2. Research subjective opinions

Read [research-guidelines.md](references/research-guidelines.md). Deduplicate opinions and cluster them by topic. Use one researcher for one topic; use parallel read-only research subagents only for two or more independent topics. Require evidence, counterevidence, conditions, dates, and direct URLs. Register complete topic files with `record-research`.

## 3. Synthesize Agent judgments

Read [agent-judgment.md](references/agent-judgment.md). After research, produce `agent-judgment.json` with conclusion, confidence, time horizon, priced-in read, what must be true, measurable disconfirmers, downside mechanism, action posture, missing evidence, source URLs, and `source_as_of`. Register it with `record-judgment`.

## 4. Draft and review

Read [report-template.md](references/report-template.md). Preserve this ordered structure:

1. `第一部分｜视频 / 作者内容`
2. `第二部分｜外部证据研判`
3. `第三部分｜Agent 综合判断`

Part one must be grounded only in the corrected transcript and written in direct content voice. Keep external evidence and Agent inference out of it. Label report-authored disclaimers, transcript uncertainty, omissions, and chart explanations as non-creator content. Exclude advertising and sales language. Use the fixed heading `科技五大新闻` for five closing technology items and hide their video timestamps on the page.

Run the first fidelity review against the imported transcript package without exposing external research to the reviewer. Because this project has no source audio, never claim that an uncertain word was confirmed by listening. Register the draft and passing review with `record-draft` and `record-fidelity-review`.

## 5. Build and validate

Create `report.md`, `report-data.json`, `citations.json`, and HTML under one dated report directory. Use `assets/report-template.html` unless an approved replacement exists. Register structured artifacts with `build-structured`, render with `render-html`, visually inspect the page, record a passed `html-validation.json`, and run `complete-run` only after every gate passes.

Return video identity, input package, stage results, transcript caveats, correction and unresolved-term counts, opinion/research/judgment counts, review result, report paths, and remaining limitations.
