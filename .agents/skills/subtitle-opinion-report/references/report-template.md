# 视频报告编辑与视觉模板

## 产品目标

最终报告是一份可直接阅读和分享的财经杂志式成品，不是审计控制台。读者应在封面后的第一个决策屏理解主要资产、证据状态和下一验证，在三分钟内抓住核心观点，并能沿着“视频内容 → 外部证据 → Agent 判断”顺序继续深入。

内部仍可保留结构化分析、研究来源和流程状态，但默认 HTML 不展示 ASR 风险清单、字幕时间戳、claim ID、原始/推导数据标签或产物哈希。只有当不确定性会实质改变核心结论时，才用自然语言写进正文。

## 写作前的轻量编辑规划

在同一次运行、同一上下文中确定：

- 一个有编辑判断的主标题与一句封面副标题；
- 一个核心命题与 3—5 条封面导读；
- 主要主题、次要主题及其阅读顺序；
- 每个主题最适合的表达：正文、人物观点卡、数字卡、对照卡、机制图或短表；
- 第二层新增了什么证据，第三层给出了什么决策增量。

不为规划另起模型调用、子任务或文件。章节数量随内容自然变化，不套固定六章，也不为凑数制造图表。

## 固定三层结构

1. `第一部分｜视频 / 作者内容`
2. `第二部分｜外部证据研判`
3. `第三部分｜Agent 综合判断`

第一部分之前固定放置一个 `investor-dashboard`，并明确标注“报告综合 · 非视频原内容”。它不属于第一部分，不能被读者误认为视频作者原话。每个主要投资主题只展示资产/公司、视频核心观点、外部证据状态、Agent 姿态、期限、下一催化剂和关键反证；保持短句，不在首屏重写完整论证。

第一部分用直接内容语气呈现事件、数据、推理和讲者判断。广告、推广、订阅和销售话术不进入正文。讲者自己的判断使用 `speaker-opinion-marker creator-view-card`；讲者转述其他人或机构的观点使用 `speaker-opinion-marker reported-view-card`。两者都必须写明 `data-speaker`、`data-stance-owner` 和 `data-attribution-mode`，显示实际姓名/机构与简短英文 kicker，例如 `JASON TAKE`、`CNBC VIEW` 或 `RAY DALIO VIEW`。

第二部分不复述第一部分，只回答哪些命题得到支持、哪些被收窄、哪些仍需等待。第三部分给出跨主题取舍、观察姿态和最重要的下一验证，不把字段清单机械铺满页面。

## 默认阅读体验

页面采用以下优先级：

1. 强封面：编辑标题、副标题、作者与日期；
2. 投资决策总览：一屏扫描主要资产、证据状态、姿态、催化剂与反证；
3. 第一部分的 3—5 个视频内容导读卡；
4. 一组真正有记忆点的行情或 KPI 卡；
5. 每个主要主题一段连贯叙事，并按需要加入机制图、双向力量卡或两类人物观点卡；
6. “科技五大新闻”使用独立新闻卡；
7. 外部证据使用“证据校准”式标题和状态网格；
8. Agent 判断使用结论优先的决策卡、情景卡和催化剂日历；
9. 来源作为延伸阅读置于末尾，不占据主阅读动线。

`details.report-detail` 是可选的深度阅读组件，不再承担强制审计职责，也不要求每个主题都有。默认内容可以比旧版更完整，只需避免跨层整段重复和超长无分段文字。

## 视觉组件

优先复用模板已经支持的组件：

- `summary-dashboard`：第一部分内的视频内容主题导读；
- `investor-dashboard`：位于第一部分之前的投资决策总览；
- `asset-map`：把主题映射到公司、指数、商品或政策变量；
- `market-board`：跨资产或同口径数字卡；
- `metric-grid` / `metric-card`：事件、KPI 或关键阈值；
- `story-visual`：四步以内的因果或资金流；
- `dual-thesis`：短期/长期、多头/空头等双向力量；
- `creator-view-card`：实际讲者自己的观点；
- `reported-view-card`：实际讲者转述的他人或机构观点；
- `evidence-status-grid`：支持、收窄、冲突、未知等证据增量；
- `scenario-grid`：基准、上行、下行情景及触发条件；
- `catalyst-calendar`：下一次财报、政策、价格或数据验证；
- `plain-language-note`：把专业机制翻译为普通投资者能直接理解的短注；
- `quick-news-grid`：科技五大新闻；
- `topic-brief`、`evidence-delta`、`decision-brief`：三层结论卡。

所有组件必须减少阅读成本。数字可以直接服务叙事，不强制增加“原始数据”“推导数据”或“示意图”标签。不得使用脚本、iframe、远程字体或任意可执行 HTML；图形优先使用 HTML/CSS、短表或静态本地资源。

Markdown 表格的分隔行只使用 `---`，不要写 `:---`、`---:` 或 `:---:` 对齐语法；当前安全渲染器会把这些写法转换为内联 style 属性并拒绝成稿。

## 推荐骨架

```markdown
---
title: "{{编辑标题}}"
video_id: "{{video_id}}"
source_url: "{{视频 URL}}"
creator: "{{作者}}"
published_at: "{{发布时间}}"
report_date: "{{报告日期}}"
description: "{{一句封面副标题}}"
---

# {{编辑标题}}

<section id="investor-dashboard" class="investor-dashboard" data-as-of="{{资料截止日}}" markdown="1">
<header class="investor-dashboard-header"><span class="dashboard-kicker">INVESTOR DASHBOARD</span><strong>投资决策总览</strong><small>报告综合 · 非视频原内容</small></header>
<div class="investor-dashboard-grid">
<article class="investor-topic" data-status="mixed"><div class="investor-topic-head"><span class="asset-tags">{{资产 / 公司}}</span><span class="status-pill">{{证据状态}}</span></div><strong>{{主题}}</strong><p class="video-core"><b>视频核心观点</b>{{一句话}}</p><dl><dt>Agent 姿态</dt><dd>{{观察姿态}}</dd><dt>期限</dt><dd>{{期限}}</dd><dt>下一催化</dt><dd>{{事件或数据}}</dd><dt>关键反证</dt><dd>{{量化反证}}</dd></dl></article>
{{其余主要主题卡}}
</div>
</section>

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator" markdown="1">
{{一句核心命题与本期阅读切口}}
</div>

### 一页看懂

<section class="summary-dashboard" markdown="1">
{{3—5 个导读卡}}
</section>

<section class="market-board" markdown="1">
{{同口径行情或 KPI 卡；没有合适数字时省略}}
</section>

### {{主题}}

<section class="topic-brief" markdown="1">
{{结论、关键依据与核心限定}}
</section>

<section class="story-visual" markdown="1">
{{真正有帮助的机制图；没有则省略}}
</section>

<div class="speaker-opinion-marker creator-view-card" data-speaker="{{讲者}}" data-stance-owner="{{讲者}}" data-attribution-mode="self"><span class="speaker-opinion-kicker">{{讲者 TAKE}}</span><strong>{{讲者}}</strong><span class="speaker-opinion-topic">{{主题}}</span></div>

> {{讲者判断}}

<div class="speaker-opinion-marker reported-view-card" data-speaker="{{讲者}}" data-stance-owner="{{被转述者 / 机构}}" data-attribution-mode="reported"><span class="speaker-opinion-kicker">{{机构 VIEW}}</span><strong>{{观点归属者}}</strong><span class="speaker-opinion-topic">由 {{讲者}} 转述</span></div>

> {{被转述的观点及原有限定}}

### 科技五大新闻

<section class="quick-news-grid" markdown="1">
{{五张新闻卡}}
</section>

## 第二部分｜外部证据研判

本部分为外部证据研判，不代表视频作者观点，也不构成投资建议。

### 证据校准 01｜{{主题}} · {{编辑结论}}

<section class="evidence-delta" markdown="1">
{{一致证据、反方证据与成立条件}}
</section>

<section class="evidence-status-grid" markdown="1">
<article data-status="supported"><span>支持</span><strong>{{得到支持的部分}}</strong><p>{{证据增量}}</p></article>
<article data-status="narrowed"><span>收窄</span><strong>{{需要缩小的结论}}</strong><p>{{适用条件}}</p></article>
<article data-status="challenged"><span>冲突</span><strong>{{主要反方证据}}</strong><p>{{为何重要}}</p></article>
<article data-status="unknown"><span>未知</span><strong>{{仍缺的证据}}</strong><p>{{下一验证}}</p></article>
</section>

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频内容、既有外部研判和注明日期的公开资料形成的综合判断，不代表视频作者观点，也不构成投资建议。

### {{判断标题}}

<section class="decision-brief" markdown="1">
{{一句判断、最重要的风险与下一验证}}
</section>

<section class="scenario-grid" markdown="1">
<article data-scenario="bear"><span>下行情景</span><strong>{{触发条件}}</strong><p>{{传导与结果}}</p></article>
<article data-scenario="base"><span>基准情景</span><strong>{{当前判断}}</strong><p>{{成立条件}}</p></article>
<article data-scenario="bull"><span>上行情景</span><strong>{{触发条件}}</strong><p>{{潜在增量}}</p></article>
</section>

<aside class="plain-language-note"><strong>用投资者的话说</strong>{{把专业机制翻成一到两句普通语言}}</aside>

## 延伸阅读

{{关键来源链接}}
```

## 成品自检

- 封面后的首个决策屏是否能快速回答“看什么、为什么、等什么、什么会推翻”；
- 投资决策总览是否醒目标注“报告综合 · 非视频原内容”，且没有混入作者内容层；
- 标题与导读是否抓住本期真正矛盾，而不是只复制视频标题；
- 作者自有观点与作者转述观点是否使用不同的人物卡并保留正确归属；
- 图表与数字卡是否确实帮助理解；
- 三层是否各自增加信息，而不是重复；
- 页面在 1440px 和 390px 视口均无横向溢出；
- 打印时封面独立成页，卡片与表格尽量不被拆开。
