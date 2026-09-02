---
name: subtitle-opinion-report
description: Generate, resume, review, or validate a source-only visual report from a validated timestamped video transcript package. Use when Codex is asked to import corrected subtitles, preserve creator meaning and attributed opinions, or publish the final Markdown and HTML report.
---

# Subtitle Meaning Report

This project begins with a transcript package. It never downloads media, runs ASR, or edits the transcript.

## Preflight

1. Read the project-root `WORKFLOW.md` and `AGENTS.md`.
2. Inspect `state/processed-reports.json` and `work/*/manifest.json`.
3. New video runs use `workflow_profile = video_meaning_v1`. A completed report is reused without overwriting its verified artifacts. A legacy `video_full_v1` report may be opened or reused after completion, but this branch does not resume an unfinished legacy workflow and provides no completed-report regeneration path.
4. If no run exists, require a package exported by the transcript project and run:

```text
video-opinion-report import-transcript --package /absolute/path/to/transcript-package
```

Stop if schema, checksum, transcript structure, or upstream-audit integrity fails. Treat upstream coverage, gap, and video-end alignment metrics as non-blocking audit metadata: this project consumes the transcript as text and does not reconstruct the media timeline. Preserve every imported segment unchanged. Do not launch a separate correction stage or create a second corrected transcript. Obvious ASR wording may be normalized naturally while understanding and writing; preserve uncertainty whenever context is insufficient.

## 1. Analyze meaning and write the report

Run all model work serially inside `analyze`, displayed as “原意分析与成稿”. Never use multiple Agents. The normal path has three bounded passes:

1. `understand`: read [content-boundaries.md](references/content-boundaries.md), [opinion-schema.md](references/opinion-schema.md), and the complete `transcript.corrected.model.txt`. Use optional web access and relevant stock, macro, finance, technology, accounting, or earnings knowledge to disambiguate the source. Write internal-only `understanding-notes.json`, `video-analysis.json`, and `opinions.jsonl`.
2. `plan`: read [report-template.md](references/report-template.md) plus the compact outputs above; do not reread the transcript or search the web. Write `presentation-plan.json` with 2—3 summary cards and at most one semantic visual per reportable section.
3. `draft`: write `report.md` from the analysis, opinions, and presentation plan. Only inspect a targeted transcript range when a recorded ambiguity requires it; do not reread the whole transcript. Register all artifacts atomically.

The execution layer may run one targeted repair pass after a deterministic validation failure. The repair pass is not a new research or judgment stage.

Web search is available but optional. Use it when the particular video benefits from term disambiguation, data-scope checking, detail clarification, or a faithful visual treatment. Search is not an external-research stage: it must not add a new thesis, investment recommendation, causal claim, or Agent conclusion to the report, and it must not change the ownership, strength, conditions, or conclusion of a video claim. If outside material conflicts with the transcript, preserve the video's meaning and state uncertainty only when material to understanding.

Produce these model-authored artifacts in the same displayed stage:

- internal-only `understanding-notes.json`, which may contain direct URLs used for disambiguation but never becomes report evidence;
- schema-v2 `video-analysis.json` with `workflow_profile = video_meaning_v1`;
- `opinions.jsonl`, with `research_status = not_applicable` on every item;
- internal `presentation-plan.json`, binding every section to its editorial lead and optional visual type;
- the final source-only `report.md`.

Every reportable section needs a summary, key points, a transcript segment range, and one matching `video-section` page anchor. Every opinion must bind its section, timestamps, exact quote, actual speaker, stance owner, and attribution mode. Advertising, promotion, subscription prompts, sales passages, unrelated passages, and boilerplate outros follow [content-boundaries.md](references/content-boundaries.md) and never become opinions or report prose.

Register all three artifacts together:

```text
video-opinion-report record-meaning-report --video-id VIDEO_ID --understanding-notes /absolute/path/understanding-notes.json --presentation-plan /absolute/path/presentation-plan.json --video-analysis /absolute/path/video-analysis.json --opinions /absolute/path/opinions.jsonl --markdown /absolute/path/report.md
```

The command deterministically validates analysis, opinion attribution, transcript ranges, content selection, section-to-page mapping, source-only structure, links, and promotion exclusion. Any failure fails the whole analyze stage and blocks rendering.

Model invocation count is operational telemetry, not a completion rule. The execution layer may retry or perform a serial revision when needed. Never use multi Agent, subagents, task delegation, or parallel Agent work.

## 2. Report structure

The report has one content layer: `视频 / 作者内容`. Use a compact editorial cover, a 2—3 card “视频内容速览”, and a coherent research-brief body labeled `报告整理 · 仅据字幕`. Keep the reading column narrow, paragraphs short, desktop card grids to at most three columns, and mobile layouts to one column. Every section has one lead sentence and at most one meaningful visualization.

Use chapter summaries, key-data cards, creator-view cards, reported-view cards, mechanisms explicitly expressed in the video, and “科技五大新闻” when that segment exists. Preserve the distinction between the speaker and the true stance owner. Omit advertising and sales language regardless of location.

Do not add an investor dashboard, external-evidence assessment, Agent judgment, scenario grid, catalyst calendar, extension reading, personalized trade instruction, or any claim that the transcript does not express. Network use during generation does not authorize extra report content.

## 3. Build and validate

Create `report.md`, `report-data.json`, `citations.json`, and HTML under `reports/<published-date>-<video_id>/`. Use only `assets/report-template.html`.

After analyze completes, the outer deterministic program builds schema-v3 `report-data.json`, writes a compatibility `citations.json` containing video/transcript provenance and an empty `external_sources` list, renders the recorded Markdown, visually inspects desktop and mobile viewports, and binds `html-validation.json` to all final-artifact hashes. Run `complete-run` only after every gate passes.

Return video identity, workflow profile, model/token telemetry, stage results, report paths, and remaining source uncertainties. Do not report research or Agent-judgment counts because those artifacts do not exist in this workflow.
