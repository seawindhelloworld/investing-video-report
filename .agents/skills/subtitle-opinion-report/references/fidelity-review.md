# 视频原意审查

## 审查输入

第一轮只使用：

- 导入的原始 ASR、校订字幕、质量结果和勘误记录；
- 视频分析结果；
- 报告正文。

第一轮不要读取外部研判结果，避免用外部“正确答案”反向改写视频原意。

## 逐段检查

- 是否能找到对应视频时间戳；
- 是否保留“可能”“认为”“倾向于”“一定”等语气强度；
- 是否保留限制条件、时间范围和对象范围；
- 是否把推测写成结论；
- 是否错误归因；
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

## 输出

```json
{
  "paragraph_id": "p-012",
  "verdict": "overstated",
  "video_timestamp": "00:18:32-00:20:05",
  "problem": "正文把可能性判断写成确定结论",
  "original_modality": "可能与……有关",
  "suggested_revision": "作者认为这可能与……有关"
}
```
