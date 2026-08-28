---
name: subtitle-opinion-report
description: Generate, resume, review, or validate a three-layer opinion report from a validated timestamped transcript package. Use when Codex is asked to import corrected subtitles, extract creator meaning and subjective opinions, research those opinions, form source-backed Agent judgments, or publish the final Markdown and HTML report.
---

# Subtitle Opinion Report

This project begins with a transcript package. It never downloads media, runs ASR, or edits the transcript.

## Preflight

1. Read the project-root `WORKFLOW.md` and `AGENTS.md`.
2. Inspect `state/processed-reports.json` and `work/*/manifest.json`.
3. Resume an incomplete report run for the same video ID instead of importing it twice. A completed report is reused by default. Only an explicit regenerate request may archive immutable `work`, `reports`, and `output` revisions before resetting stages after ingest; never treat an incomplete run as a new revision.
4. If no run exists, require a package exported by the transcript project and run:

```text
video-opinion-report import-transcript --package /absolute/path/to/transcript-package
```

Stop if schema, checksum, transcript structure, or upstream-audit integrity fails. Treat upstream coverage, gap, and video-end alignment metrics as non-blocking audit metadata: this project consumes the transcript as text and does not reconstruct the media timeline. Preserve every imported segment unchanged; advertising, transitions, outros, and obvious ASR noise are handled later by content selection. Do not launch a separate transcript-correction stage or model call and do not create a second corrected transcript. Obvious ASR wording may be normalized naturally while performing the required content analysis and report writing; preserve uncertainty whenever context is insufficient.

## 1. Extract meaning and opinions

Run this as the isolated `analyze` model stage with web search disabled. Read [content-boundaries.md](references/content-boundaries.md) and [opinion-schema.md](references/opinion-schema.md). Read the deterministic `transcript.corrected.model.txt` completely; it losslessly carries every segment ID, timestamp, and text value from the immutable `transcript.corrected.jsonl` while omitting repeated JSON keys. The JSONL remains authoritative and must never be overwritten. From `corrections.json`, extract only the correction summary and unresolved terms; do not load usage or historical model-response metadata. Do not launch a correction pass, risk-word scan, or extra model call; understand obvious wording from context and keep the upstream file unchanged. Advertising, promotion, subscription prompts, sales passages, unrelated passages, and boilerplate outros follow the existing content-selection schema and never become opinions or report prose. Produce schema-v2 `video-analysis.json` and `opinions.jsonl`; every reportable section needs a summary and key points, and every opinion must bind its section, actual speaker, stance owner, and attribution mode. Register them with `record-analysis`, which creates `transcript.report.jsonl` and its lossless compact model view. ASR risk lists stay internal and are not a required reader-facing section.

## 2. Research subjective opinions

Run this as a new isolated `research` model stage. Read [research-guidelines.md](references/research-guidelines.md), the recorded opinions, and analysis. Deduplicate opinions and cluster them by topic. Do not reread the full report transcript; only open the relevant segment range in `transcript.report.model.txt` when an opinion needs contextual disambiguation. The current Agent researches every topic sequentially; do not start subagents or parallel Agent work. Reuse shared sources across opinions, normally keep 3–5 decisive direct sources per topic, and stay within the automation search/source budget unless a critical claim cannot otherwise be verified. Require evidence, counterevidence, conditions, dates, and direct URLs. Register complete topic files with `record-research`.

## 3. Synthesize Agent judgments

Run this as a new isolated `judgment` model stage with web search disabled. Read [agent-judgment.md](references/agent-judgment.md). Use the registered analysis and research rather than adding a second research pass. Produce `agent-judgment.json` with conclusion, confidence, time horizon, priced-in read, what must be true, measurable disconfirmers, downside mechanism, action posture, missing evidence, source URLs, and `source_as_of`. Register it with `record-judgment`.

## 4. Draft and review

Read [report-template.md](references/report-template.md). Preserve this ordered structure:

1. `第一部分｜视频 / 作者内容`
2. `第二部分｜外部证据研判`
3. `第三部分｜Agent 综合判断`

Before part one, add exactly one investor dashboard labeled `报告综合 · 非视频原内容`; it is report front matter, not creator content. Part one must be grounded only in the corrected transcript, semantically cover every reportable section, and be written in direct content voice. Keep external evidence and Agent inference out of it. Distinguish the creator's own take from views the creator reports from another person or institution. Label report-authored disclaimers, transcript uncertainty, omissions, and chart explanations as non-creator content. Omit advertising, promotion, subscription prompts, and sales language from the report regardless of location; middle source text remains preserved in transcript evidence even though it does not become report prose or an opinion. Use the fixed heading `科技五大新闻` for five closing technology items and hide their video timestamps on the page.

Run drafting as a new isolated model stage with web search disabled. Within this one drafting call and context, make a lightweight editorial plan before writing; do not start another model call or create another planning artifact. Write an editorial title and deck, 3–5 cover hooks, and a natural topic sequence. Use the supported magazine components—investor dashboard, asset map, summary dashboard, market/KPI cards, creator and reported-view cards, mechanism visuals, evidence-status grid, scenario grid, catalyst calendar, plain-language notes, news cards, evidence deltas, and decision briefs—only where they improve comprehension. Treat the six investor questions as an internal completeness checklist, not six mandatory visible headings. Claim IDs, timestamp trails, ASR notices, provenance badges, and per-topic audit disclosures are optional internal details, not presentation requirements.

Run the first fidelity review in a fresh isolated model context with web search disabled. Expose only package identity, the complete lossless `transcript.report.model.txt`, analysis, opinions, and draft; do not read research files, Agent judgment, citations, external evidence, duplicate transcript encodings, or historical correction-model metadata. Check every reportable section and opinion, including speaker, stance owner, attribution mode, qualifiers, and reasoning chain; any omitted section needs an explicit reason. Because this project has no source audio, never claim that an uncertain word was confirmed by listening. Bind the review to the authoritative report JSONL SHA-256 recorded in the model-view header. Register the draft and passing review with `record-draft` and `record-fidelity-review`.

For Codex video automation, the UI-selected reasoning effort is a ceiling rather than a mandatory level for every stage: analyze, research, and draft cap at `high`; judgment caps at `xhigh`; fidelity review caps at `medium`. Lower user-selected levels remain lower. This preserves the strongest reasoning for synthesis while avoiding routine `xhigh` context replay.

## 5. Build and validate

Create `report.md`, `report-data.json`, `citations.json`, and HTML under the single directory `reports/<published-date>-<video_id>/`. Use only `assets/report-template.html`. The fidelity review must record the SHA-256 of the reviewed draft and report transcript. After the model stages finish, the outer deterministic program registers structured artifacts with `build-structured`, renders that same recorded draft with `render-html`, visually inspects the page, and binds `html-validation.json` to the SHA-256 values of all four final artifacts. Run `complete-run` only after every gate passes.

Return video identity, input package, stage results, opinion/research/judgment counts, review result, report paths, and remaining limitations.
