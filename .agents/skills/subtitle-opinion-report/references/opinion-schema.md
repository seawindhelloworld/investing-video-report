# 观点数据契约

本契约用于 `video_meaning_v1`。`video-analysis.json` 使用 `schema_version = 2`，并显式写入 `workflow_profile = video_meaning_v1`。

`video-analysis.json` 的新任务使用 `schema_version = 2`。每个可报告 section 至少包含：

```json
{
  "section_id": "section-003",
  "title": "企业软件的 AI 分化",
  "segment_start": "seg-000081",
  "segment_end": "seg-000146",
  "summary": "财报缓解了需求骤停担忧，但 AI 变现与资产负债表约束仍需验证。",
  "key_points": [
    "订单韧性不能外推为整个板块复苏",
    "AI 合作仍缺实际收入与毛利证据"
  ]
}
```

每一条观点至少包含：

```json
{
  "opinion_id": "opinion-001",
  "section_id": "section-003",
  "timestamp_start": 1574.2,
  "timestamp_end": 1652.0,
  "segment_start": "seg-000112",
  "segment_end": "seg-000119",
  "exact_quote": "我认为这次议息会议总体上对美股是利好的",
  "faithful_paraphrase": "此次议息会议整体利好美股",
  "speaker": "Jason",
  "stance_owner": "Jason",
  "attribution_mode": "self",
  "opinion_type": "market_judgment",
  "target": "美国股票市场",
  "time_horizon": "视频中未明确",
  "stated_basis": ["政策表述偏宽松", "降息预期增强"],
  "qualifiers": ["总体上", "我认为"],
  "context_before": "",
  "context_after": "",
  "research_status": "not_applicable"
}
```

要求：

- `exact_quote` 保留原话，不做润色；
- `faithful_paraphrase` 不得强化或弱化语气；
- `section_id` 必须指向覆盖该观点字幕区间的 section；
- `speaker` 是视频中实际说出这段话的人；`stance_owner` 是观点真正归属的人或机构；
- `attribution_mode` 只能是 `self`、`reported`、`direct_quote` 或 `uncertain`。例如 Jason 转述 CNBC 判断时，speaker 为 Jason、stance_owner 为 CNBC、attribution_mode 为 reported；
- `self` 只用于 speaker 与 stance_owner 相同的观点。无法可靠判断归属时使用 `uncertain`，不得默认归到作者；
- `stated_basis` 只记录视频明确提出的依据；
- `time_horizon`、`target` 未明确时写“视频中未明确”，不得自行推断；
- `video_meaning_v1` 不生成外部研究；`research_status` 固定为 `not_applicable`。模型为理解原意进行的可选联网查询不得产生新的观点记录。
