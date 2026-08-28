# Agent 综合判断规则

## 目标与边界

在“视频作者观点”和“外部观点研判”之后，形成第三条可审计证据链：

1. 视频正文：作者明确表达的事实、推理、结论与限定词；
2. 外部研判：独立信源对具体作者观点的支持、反对与适用条件；
3. Agent 综合判断：基于前两层、最新一手资料和明确推理形成的决策性结论。

第三层不得改写前两层，不得冒充视频作者观点，不得把未知信息写成确定事实，也不得输出个性化投资建议。报告中必须写明：

> 本节为 Agent 基于视频内容、既有外部研判和注明日期的公开资料形成的综合判断，不代表视频作者观点，也不构成投资建议。

## 证据分层

每个主题都应区分：

- `fact`：财报、监管文件、官方数据等可核对事实；
- `management_claim`：公司管理层的描述、目标或指引；
- `inference`：由多项证据连接出的推论；
- `agent_judgment`：Agent 对强弱、条件和行动姿态的判断。

市场价格、估值、共识、持仓、期权和资金流都必须有日期。缺少可靠实时数据时，写成“无法判断是否已充分计价”，不得用泛泛的“市场尚未认识到”代替。

## 每个主题的最小结构

- 一句话结论；
- 置信度和适用期限；
- 支持证据与主要反方证据；
- `What is priced in`，以及无法观测的缺口；
- `What must be true`；
- 可量化、带期限的反证信号；
- 下行机制：`Shock → Transmission → Constraint → Outcome`；
- 行动姿态：`watchlist`、`wait_for_proof`、`re_underwrite`、`avoid_for_now` 或 `research_only`；
- 缺失证据和下一次验证节点；
- 一手来源 URL 与 `source_as_of`。

行动姿态是研究工作流状态，不是买入、卖出、仓位或收益承诺。

## JSON 契约

顶层字段名必须是 `topics`，不是 `judgments`。每个主题使用以下结构；`next_verification` 可以是字符串或非空列表，其他字段名不得自行改写：

```json
{
  "schema_version": 1,
  "video_id": "video-001",
  "source_as_of": "2026-08-28",
  "disclaimer": "不代表视频作者观点，也不构成投资建议。",
  "cross_topic_summary": "跨主题结论",
  "topics": [
    {
      "topic_id": "topic-01",
      "theme": "主题",
      "conclusion": "结论",
      "evidence_layers": {
        "facts": ["可核对事实"],
        "management_claims": [],
        "inference": "证据之间的推论",
        "agent_judgment": "Agent 的独立判断"
      },
      "confidence": "medium",
      "time_horizon": "未来两个季度",
      "priced_in": "已计价判断或无法观测的缺口",
      "what_must_be_true": ["成立条件"],
      "disconfirmers": ["未来 6 个月指标低于 10%"],
      "downside_mechanism": {
        "shock": "冲击",
        "transmission": "传导",
        "constraint": "约束",
        "outcome": "结果"
      },
      "action_posture": "wait_for_proof",
      "missing_evidence": "缺失证据",
      "next_verification": ["下一次验证"],
      "source_urls": ["https://example.com/source"]
    }
  ]
}
```

## 写作要求

- 先给判断，再给证据，不复述整段视频；
- 公司经营判断与股票定价判断分开；
- 好公司不自动等于好价格，强财报不自动等于长期回报；
- 对未来结论使用条件句，并给出什么会改变判断；
- 横向排序只能比较同一维度，例如“经营证据强度”，不得把不同期限、不同风险混成总分；
- 需要使用公开网络资料时优先公司 IR、SEC、政府和行业组织等一手来源，不能把搜索摘要当证据。

## 审查门

Agent 判断发布前至少检查：

- 是否与作者观点和外部研判视觉隔离；
- 是否把事实、管理层主张、推断和判断分开；
- 是否标注价格与资料日期；
- 是否包含反方证据、成立条件、反证信号和缺失证据；
- 是否避免未被数据支持的精确目标价、收益率或买卖指令；
- 所有来源链接是否可打开且直接支持相邻表述。
