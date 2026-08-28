# 视频原意审查

## 审查输入

第一轮只使用：

- 字幕包身份，以及从 `transcript.report.jsonl` 确定性生成、包含全部可报告 segment ID、时间和文字的 `transcript.report.model.txt`；
- 视频分析、观点结果；
- 报告正文。

不要同时加载 corrected JSONL、report JSONL、Markdown 字幕和完整 corrections 审计。model view 头部记录的 `source_sha256` 用于绑定权威 report JSONL；这种去重只减少上下文，不改变可审查文字。

第一轮不要读取外部研判结果，避免用外部“正确答案”反向改写视频原意。

## 逐段检查

- 是否能找到对应视频时间戳；
- 是否保留“可能”“认为”“倾向于”“一定”等语气强度；
- 是否保留限制条件、时间范围和对象范围；
- 是否把推测写成结论；
- 是否错误归因；
- 是否混淆实际说话人与观点归属者，或把转述观点写成作者自己的判断；
- 是否因截取局部内容改变上下文；
- 是否加入视频中不存在的因果关系；
- 是否把 Agent 理解包装成视频作者观点。

## 问题类型

- `faithful`
- `overstated`
- `understated`
- `misattributed`
- `decontextualized`
- `unsupported_by_video`
- `uncertain_asr`

关键人名、数字、否定词和核心观点必须对照校订字幕、原始 ASR 与勘误记录。若字幕包保留未解决词项，审查结果必须标记 `uncertain_asr`；本项目不得声称已经回听音频确认。

## 覆盖契约

新分析产物使用 schema v2 时，审查文件也使用 schema v2：

- `section_checks` 必须与 analysis 中全部 section_id 一一对应；已写入正文时使用 `coverage_status = included` 并给出 `report_locations`，确有必要省略时使用 `intentionally_omitted` 并写明 `omission_reason`；
- `opinion_checks` 必须与全部 opinion_id 一一对应，并原样复核 `speaker`、`stance_owner`、`attribution_mode` 与正文位置；
- “覆盖”指事件、推理链、限定与观点归属在语义上完整，不要求逐字复制字幕；
- 广告、推广和已登记非报告内容不属于可报告 section，不应为了形式完整而写回正文。

## 输出示例

```json
{
  "schema_version": 2,
  "video_id": "video-001",
  "external_research_visible_to_reviewer": false,
  "overall_verdict": "passed",
  "post_revision_verdict": "passed",
  "draft_sha256": "...",
  "transcript_sha256": "...",
  "section_checks": [
    {
      "section_id": "section-003",
      "status": "passed",
      "coverage_status": "included",
      "report_locations": ["第一部分 / 企业软件的 AI 分化"],
      "omission_reason": ""
    }
  ],
  "opinion_checks": [
    {
      "opinion_id": "opinion-001",
      "status": "passed",
      "speaker": "Jason",
      "stance_owner": "CNBC",
      "attribution_mode": "reported",
      "report_locations": ["第一部分 / CNBC VIEW"]
    }
  ],
  "exclusion_checks": [],
  "unresolved_transcript_checks": []
}
```
