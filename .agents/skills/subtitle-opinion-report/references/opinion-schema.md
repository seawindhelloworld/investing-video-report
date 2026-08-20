# 观点数据契约

每一条观点至少包含：

```json
{
  "opinion_id": "opinion-001",
  "timestamp_start": 1574.2,
  "timestamp_end": 1652.0,
  "exact_quote": "我认为这次议息会议总体上对美股是利好的",
  "faithful_paraphrase": "视频作者判断此次议息会议整体利好美股",
  "opinion_type": "market_judgment",
  "target": "美国股票市场",
  "time_horizon": "视频中未明确",
  "stated_basis": ["政策表述偏宽松", "降息预期增强"],
  "qualifiers": ["总体上", "我认为"],
  "context_before": "",
  "context_after": "",
  "research_status": "pending"
}
```

要求：

- `exact_quote` 保留原话，不做润色；
- `faithful_paraphrase` 不得强化或弱化语气；
- `stated_basis` 只记录视频明确提出的依据；
- `time_horizon`、`target` 未明确时写“视频中未明确”，不得自行推断；
- Agent 识别出的隐含条件另存为外部分析字段，不归因给视频作者。
