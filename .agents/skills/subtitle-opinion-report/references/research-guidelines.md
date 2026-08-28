# 外部观点研判规则

## 目标

为视频作者的主观判断提供独立、多信源、带条件的第二视角。不要把主观观点简化成事实核查式的“真/假”。

## 研究维度

- 作者明确给出的前提是否足以支撑结论；
- 支持该判断的证据和观点；
- 反对该判断的证据和观点；
- 时间范围是否改变结论；
- 对象范围是否改变结论；
- 市场或公众是否已经提前计价；
- 结论成立所依赖的条件；
- 现阶段无法消除的不确定性。

## 信源优先级

1. 原始政策文件、机构公告、论文和公开数据；
2. 信誉良好的研究机构和行业组织；
3. 有明确作者、日期、方法和证据的专业分析；
4. 权威媒体对原始材料的报道。

不要把搜索摘要当作证据。打开并核对原始页面。记录标题、发布者、作者、发布日期、访问日期、URL、证据摘要和适用范围。

同一主题的多条观点应复用共同证据，避免为每条 opinion 重复搜索。正常情况下每个主题保留 3—5 个最有解释力的直接来源，全部主题合计不超过 24 个来源和 24 次搜索；只有关键命题无法由预算内的一手资料确认时才可超出，并说明原因。研究以 analysis、opinions 的结构化语境为主，仅在观点归属或限定词不清时按 segment 范围读取紧凑字幕，不重新通读全文。

## 输出等级

- `supported`：外部证据总体支持；
- `partially_supported`：方向部分一致，但需要条件；
- `mixed`：重要信源存在明显分歧；
- `not_supported`：外部证据倾向不支持；
- `insufficient`：信息不足；
- `conditional`：结论取决于时间范围、对象或前提。

同时返回主要反方视角。即使研判与视频作者一致，也要描述适用条件和不确定性。

## JSON 契约

每个主题独立写一个 schema-v1 JSON。`published_at`、`accessed_at` 和 `researched_at` 必须是可解析的 ISO 日期或日期时间，不能写“未注明”“截至资料日”等文字；发布日期确实无法取得时，应改用能提供日期的可核对来源。

```json
{
  "schema_version": 1,
  "video_id": "video-001",
  "topic_id": "topic-01",
  "theme": "主题",
  "researched_at": "2026-08-28",
  "disclaimer": "独立研判，不代表视频作者观点。",
  "topic_summary": "本主题的证据增量",
  "assessments": [
    {
      "opinion_id": "opinion-001",
      "status": "partially_supported",
      "conclusion": "结论",
      "supporting_evidence": ["支持证据"],
      "counterevidence": ["反方证据"],
      "applicable_conditions": ["适用条件"],
      "time_horizon": "未来两个季度",
      "priced_in": "已计价判断或无法观测的缺口",
      "uncertainties": ["不确定性"]
    }
  ],
  "sources": [
    {
      "source_id": "source-001",
      "title": "来源标题",
      "publisher": "发布机构",
      "author": "作者或发布机构",
      "published_at": "2026-08-27",
      "accessed_at": "2026-08-28",
      "url": "https://example.com/direct-source",
      "evidence_summary": "该来源直接支持什么",
      "scope": "适用对象与期限"
    }
  ]
}
```

全部主题文件合计必须恰好覆盖每个 opinion_id 一次，topic_id 与 source_id 在整个 research 目录内都必须唯一。
