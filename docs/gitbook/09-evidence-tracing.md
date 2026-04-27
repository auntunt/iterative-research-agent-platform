# 9. 证据追踪

证据追踪是这个项目最重要的展示点之一。系统不是只生成一篇看起来合理的报告，而是要求报告结论能回到具体证据卡片、具体 URL 和具体段落。

## EvidenceCard

证据卡片的数据结构如下：

```text
card_id
topic_id
claim
summary
citation
confidence
source_type
created_at
```

其中 `citation` 包含：

```text
url
title
paragraph_id
snippet
```

这意味着一条证据不仅知道来自哪个网页，还知道来自页面中的哪一段。

## 证据生成流程

Researcher 生成证据卡片的流程：

```text
ResearchTopic
  -> search_queries
  -> WebSearchTool
  -> candidate URLs
  -> WebFetchTool
  -> paragraphs
  -> preliminary cards
  -> LLM refinement
  -> EvidenceCard
```

如果 LLM 能成功输出结构化 JSON，则使用 LLM 生成的 claim 和 summary；如果失败，则使用规则兜底摘要。

## card_id

`card_id` 使用主题、URL 和段落 ID 生成哈希：

```text
sha1(topic_id:url:paragraph_id)
```

这个设计用于去重。即使同一个来源在多轮检索中被重复抓取，也不会重复加入证据集。

## Critic 如何评估证据

Critic 会检查：

- 每个研究主题是否有足够证据卡片。
- 每张证据是否有 URL。
- 每张证据是否有 paragraph_id。
- 不同来源数量是否足够支撑忠实度。

然后输出：

```text
sufficient
coverage_score
faithfulness_score
answer_relevancy_score
missing_topics
follow_up_topics
notes
```

如果某个主题证据不足，Critic 会生成 follow-up topic，让系统下一轮继续检索。

## Writer 如何使用证据

Writer 不是直接根据原始搜索结果写报告，而是基于 EvidenceCard 生成报告：

```text
EvidenceCard -> citation number -> paragraph with citation marker
```

报告末尾会生成引用索引：

```text
1. [title#paragraph_id](url)
2. [title#paragraph_id](url)
```

这使报告中的引用和证据卡片可以对应起来。

## 证据血统链

一条报告结论的完整追踪路径：

```text
Markdown 报告中的结论
  -> 引用编号 [n]
  -> 引用索引第 n 项
  -> Citation(url, title, paragraph_id)
  -> EvidenceCard(card_id, topic_id, claim, summary)
  -> ResearchTopic(topic_id, question, rationale)
  -> Planner 生成的任务图
```

这条链路就是项目“可信研究”的核心。

## 导出产物

调用 `POST /task/{task_id}/artifacts` 后，系统会导出：

```text
report.md
evidence_cards.json
citations.json
trace.json
metrics.json
run_manifest.json
```

这些文件适合放在演示材料里，但真实运行生成的 `artifacts/` 目录不建议提交到 GitHub 主仓库，避免仓库变大和混入临时数据。
