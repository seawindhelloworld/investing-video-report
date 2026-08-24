# 视频报告编辑与视觉模板

## 产品目标

最终报告是一份可直接阅读和分享的财经杂志式成品，不是审计控制台。读者应在首屏理解主题，在三分钟内抓住核心观点，并能沿着“视频内容 → 外部证据 → Agent 判断”顺序继续深入。

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

第一部分用直接内容语气呈现事件、数据、推理和讲者判断。广告、推广、订阅和销售话术不进入正文。讲者个人判断使用 `speaker-opinion-marker`，显示实际姓名与简短英文 kicker，例如 `JASON TAKE` 或 `RAY DALIO VIEW`。

第二部分不复述第一部分，只回答哪些命题得到支持、哪些被收窄、哪些仍需等待。第三部分给出跨主题取舍、观察姿态和最重要的下一验证，不把字段清单机械铺满页面。

## 默认阅读体验

页面采用以下优先级：

1. 强封面：编辑标题、副标题、作者与日期；
2. 3—5 个主题导读卡；
3. 一组真正有记忆点的行情或 KPI 卡；
4. 每个主要主题一段连贯叙事，并按需要加入机制图、双向力量卡或人物观点卡；
5. “科技五大新闻”使用独立新闻卡；
6. 外部证据使用“证据校准”式标题；
7. Agent 判断使用结论优先的决策卡；
8. 来源作为延伸阅读置于末尾，不占据主阅读动线。

`details.report-detail` 是可选的深度阅读组件，不再承担强制审计职责，也不要求每个主题都有。默认内容可以比旧版更完整，只需避免跨层整段重复和超长无分段文字。

## 视觉组件

优先复用模板已经支持的组件：

- `summary-dashboard`：封面后的主题导读；
- `market-board`：跨资产或同口径数字卡；
- `metric-grid` / `metric-card`：事件、KPI 或关键阈值；
- `story-visual`：四步以内的因果或资金流；
- `dual-thesis`：短期/长期、多头/空头等双向力量；
- `speaker-opinion-marker`：讲者观点；
- `quick-news-grid`：科技五大新闻；
- `topic-brief`、`evidence-delta`、`decision-brief`：三层结论卡。

所有组件必须减少阅读成本。数字可以直接服务叙事，不强制增加“原始数据”“推导数据”或“示意图”标签。不得使用脚本、iframe、远程字体或任意可执行 HTML；图形优先使用 HTML/CSS、短表或静态本地资源。

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

<div class="speaker-opinion-marker" data-speaker="{{讲者}}"><span class="speaker-opinion-kicker">{{讲者 TAKE}}</span><strong>{{讲者}}</strong><span class="speaker-opinion-topic">{{主题}}</span></div>

> {{讲者判断}}

### 科技五大新闻

<section class="quick-news-grid" markdown="1">
{{五张新闻卡}}
</section>

## 第二部分｜外部证据研判

### 证据校准 01｜{{主题}} · {{编辑结论}}

<section class="evidence-delta" markdown="1">
{{一致证据、反方证据与成立条件}}
</section>

## 第三部分｜Agent 综合判断

### {{判断标题}}

<section class="decision-brief" markdown="1">
{{一句判断、最重要的风险与下一验证}}
</section>

## 延伸阅读

{{关键来源链接}}
```

## 成品自检

- 首屏是否像完整财经报告，而不是程序输出；
- 标题与导读是否抓住本期真正矛盾，而不是只复制视频标题；
- 讲者观点是否有醒目的人物卡；
- 图表与数字卡是否确实帮助理解；
- 三层是否各自增加信息，而不是重复；
- 页面在 1440px 和 390px 视口均无横向溢出；
- 打印时封面独立成页，卡片与表格尽量不被拆开。
