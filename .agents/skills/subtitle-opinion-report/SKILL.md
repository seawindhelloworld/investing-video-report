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

Stop if schema, checksum, transcript structure, upstream-audit integrity, or quality validation fails. Do not launch a separate transcript-correction stage or model call and do not create a second corrected transcript. Obvious ASR wording may be normalized naturally while performing the required content analysis and report writing; preserve uncertainty whenever context is insufficient.

## 1. Extract meaning and opinions

Read [content-boundaries.md](references/content-boundaries.md) and [opinion-schema.md](references/opinion-schema.md). Read the immutable `work/{video_id}/transcript/transcript.corrected.jsonl` completely. Do not launch a correction pass, risk-word scan, extra model call, or derived transcript; understand obvious wording from context and keep the upstream file unchanged. Advertising, promotion, subscription prompts, sales passages, unrelated passages, and boilerplate outros follow the existing content-selection schema and never become opinions or report prose. Produce `video-analysis.json` and `opinions.jsonl`, register them with `record-analysis`, then use the generated `transcript/transcript.report.jsonl` for the remaining stages. ASR risk lists stay internal and are not a required reader-facing section.

## 2. Research subjective opinions

Read [research-guidelines.md](references/research-guidelines.md). Deduplicate opinions and cluster them by topic. The current Agent researches every topic sequentially; do not start subagents or parallel Agent work. Require evidence, counterevidence, conditions, dates, and direct URLs. Register complete topic files with `record-research`.

## 3. Synthesize Agent judgments

Read [agent-judgment.md](references/agent-judgment.md). After research, produce `agent-judgment.json` with conclusion, confidence, time horizon, priced-in read, what must be true, measurable disconfirmers, downside mechanism, action posture, missing evidence, source URLs, and `source_as_of`. Register it with `record-judgment`.

## 4. Draft and review

Read [report-template.md](references/report-template.md). Preserve this ordered structure:

1. `第一部分｜视频 / 作者内容`
2. `第二部分｜外部证据研判`
3. `第三部分｜Agent 综合判断`

Part one must be grounded only in the corrected transcript and written in direct content voice. Keep external evidence and Agent inference out of it. Label report-authored disclaimers, transcript uncertainty, omissions, and chart explanations as non-creator content. Omit advertising, promotion, subscription prompts, and sales language from the report regardless of location; middle source text remains preserved in transcript evidence even though it does not become report prose or an opinion. Use the fixed heading `科技五大新闻` for five closing technology items and hide their video timestamps on the page.

Within the same Agent run and context, make a lightweight editorial plan before drafting; do not start another model call or create another planning artifact. Write an editorial title and deck, 3–5 cover hooks, and a natural topic sequence. Use the supported magazine components—summary dashboard, market/KPI cards, speaker takes, mechanism visuals, news cards, evidence deltas, and decision briefs—only where they improve comprehension. Claim IDs, timestamp trails, ASR notices, provenance badges, and per-topic audit disclosures are optional internal details, not presentation requirements.

Run the first fidelity review against the imported transcript package without exposing external research to the reviewer. Because this project has no source audio, never claim that an uncertain word was confirmed by listening. Register the draft and passing review with `record-draft` and `record-fidelity-review`.

## 5. Build and validate

Create `report.md`, `report-data.json`, `citations.json`, and HTML under the single directory `reports/<published-date>-<video_id>/`. Use only `assets/report-template.html`. The fidelity review must record the SHA-256 of the reviewed draft and report transcript. Register structured artifacts with `build-structured`, render that same recorded draft with `render-html`, visually inspect the page, and bind `html-validation.json` to the SHA-256 values of all four final artifacts. Run `complete-run` only after every gate passes.

Return video identity, input package, stage results, opinion/research/judgment counts, review result, report paths, and remaining limitations.
