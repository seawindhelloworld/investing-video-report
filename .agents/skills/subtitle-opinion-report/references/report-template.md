# 视频原意报告编辑与视觉模板

## 产品目标

最终报告采用“财经研究简报”而不是大字海报或审计控制台。读者应先在紧凑封面与“视频内容速览”中抓住中心命题、关键变量和重要限定，再沿单层 `视频 / 作者内容` 阅读作者的完整推理。

外部知识与可选联网只用于理解专有名词、数据口径、公司关系、宏观语境、财务指标和可视化语义。它们不得成为页面信源，不得新增字幕未表达的事实、因果、建议或 Agent 结论；页面不展示非视频来源链接。

## 固定结构与阅读密度

报告必须按以下顺序组成：

1. 紧凑编辑封面：准确标题、一句话副标题、作者与发布日期、原视频链接；标题克制并尽量控制在桌面端两行内。
2. “视频内容速览”：2—3 张 `summary-dashboard` 卡，第一张标记 `报告整理 · 仅据字幕`。
3. 唯一 H2：`## 视频 / 作者内容`。
4. 与 `video-analysis.json` 每个可报告 section 一一对应的正文区块。

正文使用 17—18px 字号、约 40 个汉字的阅读行宽和 1.75 左右行距。每段只表达一个意思，建议不超过 220 个汉字，确定性门禁拒绝超过 280 个汉字的可见段落。桌面卡片最多三列，移动端降为单列。

不得出现投资决策总览、外部证据研判、Agent 综合判断、证据状态、情景卡、催化剂日历、延伸阅读或个性化买卖指令。

## 章节结构

每个 `video-section` 使用固定的编辑顺序：

1. 章节标题。
2. 一句 `section-lead`，直接给出该章结论或中心内容。
3. 2—4 个短段落，保留作者的对象、依据、条件、语气强度和推理顺序。
4. 最多一个确有表达增益的 `section-visual`。
5. 必要的作者观点或视频内转述观点卡。
6. 条件、限制或不确定性自然写入叙事，不另造 Agent 风险判断。

`video-section` 的 `id`、`data-section-id` 与 analysis section_id 必须完全一致。主视觉额外带 `data-visual-for="section_id"`，每个 section 最多一个。

## 语义可视化选择

可视化必须回答一个明确问题，而不是装饰页面：

- 一个关键数值：`metric-grid` / `metric-card`，visual_type=`kpi`；
- 三个以上可比较值：`comparison-grid`，visual_type=`comparison`；
- 明确时间顺序：`event-timeline`，visual_type=`timeline`；
- 视频明确说出的因果或机制链：`logic-flow` / `logic-step`，visual_type=`mechanism`；
- 公司、产品、行业或主体关系：`relationship-map`，visual_type=`relationship`；
- 五条片尾科技新闻：`quick-news-grid`，visual_type=`news_list`，固定标题“科技五大新闻”，不显示视频时间；
- 不满足上述条件：visual_type=`none`，用短叙事即可。

只有字幕明确出现的数据、关系、顺序和机制可以进入视觉组件。不得依据外部知识补齐数值、比较基准、因果箭头或主体关系。

## 观点与归属

- 讲者自己的判断、持仓或行动使用 `speaker-opinion-marker creator-view-card`。
- 讲者转述个人、机构或媒体的观点使用 `speaker-opinion-marker reported-view-card`。
- 两类卡片均写入 `data-speaker`、`data-stance-owner`、`data-attribution-mode`，并包含 `speaker-opinion-kicker`。
- 转述与概括使用 `view-summary`，页面会明确标为“观点摘要”；不要用 blockquote 制造逐字引用的错觉。
- 只有 `opinions.jsonl` 中 attribution_mode=`direct_quote` 的逐字引语才使用 blockquote。

广告、产品推广、订阅引导、销售话术和无关片尾套话不得进入正文或观点。

## 推荐骨架

```markdown
---
title: "{{准确、克制的编辑标题}}"
description: "{{一句话复述视频中心命题}}"
video_id: "{{video_id}}"
source_url: "{{视频 URL}}"
creator: "{{作者}}"
published_at: "{{发布时间}}"
report_date: "{{报告日期}}"
---

# {{编辑标题}}

[观看原视频]({{视频 URL}})

### 视频内容速览

<section class="summary-dashboard">
<article><span>报告整理 · 仅据字幕</span><strong>{{中心命题}}</strong><p>{{主要限定}}</p></article>
<article><span>{{关键变量}}</span><strong>{{视频中的核心变化}}</strong><p>{{对象或适用范围}}</p></article>
<article><span>{{结论条件}}</span><strong>{{作者最终倾向}}</strong><p>{{条件或不确定性}}</p></article>
</section>

## 视频 / 作者内容

<div class="layer-intro creator">{{简短编辑导语}}</div>

<section id="section-001" class="video-section" data-section-id="section-001">

### {{章节标题}}

<div class="section-lead">{{一句话结论}}</div>

{{短段落叙事}}

<div class="metric-grid section-visual" data-visual-for="section-001">
<article class="metric-card"><span>{{指标}}</span><strong>{{视频中的数值}}</strong><small>{{视频给出的对象或口径}}</small></article>
</div>

<div class="speaker-opinion-marker creator-view-card" data-speaker="{{讲者}}" data-stance-owner="{{讲者}}" data-attribution-mode="self"><span class="speaker-opinion-kicker">{{姓名 TAKE}}</span><strong>{{讲者}}</strong></div>
<div class="view-summary"><p>{{忠实概括，保留条件与语气}}</p></div>

</section>
```

## 成品自检

- 是否只有一个 H2，且精确为 `视频 / 作者内容`；
- 是否明确显示 `报告整理 · 仅据字幕`，速览只有 2—3 张卡；
- 是否每个可报告 section 有唯一锚点、一个 `section-lead`，且最多一个主视觉；
- 视觉类型是否与 presentation-plan 一致，且所有可见数据与关系均来自字幕；
- 是否保留说话人、观点归属者、强度、期限、条件和不确定性；
- 是否没有外部研判、Agent 判断、投资决策总览、外链和商业推广；
- 是否在 1440px 与 390px 下无横向溢出、遮挡、截断或失效链接；
- 是否通过结构、可读性、产物哈希和浏览器验收。
