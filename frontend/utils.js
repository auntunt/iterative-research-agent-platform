"use strict";

var h = React.createElement;

function formatElapsed(seconds) {
  if (seconds < 60) return seconds + "s";
  var m = Math.floor(seconds / 60);
  var s = seconds % 60;
  return m + "m " + (s < 10 ? "0" : "") + s + "s";
}

function formatTaskTime(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return date.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function statusLabel(status) {
  if (status === "success") return "已完成";
  if (status === "failed") return "失败";
  if (status === "running") return "进行中";
  return status || "未知";
}

function taskSummary(task) {
  if (task.status === "success") return "报告与证据卡片已生成，可查看或导出。";
  if (task.status === "running") return "正在检索来源、抽取证据并撰写报告…";
  if (task.status === "failed") return "研究未成功，请查看日志定位失败步骤。";
  return "等待进入研究流程。";
}

function score(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function preferredCardText(card) {
  const summary = cleanCardText(card.summary || "");
  const snippet = cleanCardText((card.citation && card.citation.snippet) || "");
  if (summary && !looksLikeNoise(summary)) return summary;
  if (snippet) return snippet;
  return summary || snippet || "暂无可展示摘要";
}

function cleanCardText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function looksLikeNoise(text) {
  const lowered = cleanCardText(text).toLowerCase();
  const signals = [
    "选择您的 cookie 首选项", "必要 cookie", "性能 cookie", "接受或拒绝",
    "如果您同意", "注册登录", "登录/注册", "跳转到主要内容",
    "文档建议反馈控制台", "cookie preferences", "accept or reject", "sign in", "log in",
  ];
  return signals.some((s) => lowered.includes(s));
}

function inlineMarkdown(text) {
  const nodes = [];
  const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))/g;
  let cursor = 0, match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    if (match[2])              nodes.push(h("strong", { key: nodes.length }, match[2]));
    else if (match[3])         nodes.push(h("code",   { key: nodes.length }, match[3]));
    else if (match[4] && match[5])
      nodes.push(h("a", { key: nodes.length, href: match[5], target: "_blank", rel: "noreferrer" }, match[4]));
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function renderMarkdown(markdown) {
  const lines = markdown.split(/\r?\n/);
  const nodes = [];
  let paragraph = [], list = [], code = [], inCode = false, codeLang = "";

  function flushParagraph() {
    if (!paragraph.length) return;
    nodes.push(h("p", { key: `p-${nodes.length}` }, inlineMarkdown(paragraph.join(" "))));
    paragraph = [];
  }
  function flushList() {
    if (!list.length) return;
    nodes.push(h("ul", { key: `ul-${nodes.length}` },
      list.map((item, i) => h("li", { key: i }, inlineMarkdown(item)))
    ));
    list = [];
  }
  function flushCode() {
    nodes.push(h("pre", { key: `code-${nodes.length}`, className: "codeBlock" },
      h("code", { "data-lang": codeLang }, code.join("\n"))
    ));
    code = []; codeLang = "";
  }

  lines.forEach((line) => {
    if (line.startsWith("```")) {
      if (inCode) { flushCode(); inCode = false; }
      else { flushParagraph(); flushList(); inCode = true; codeLang = line.slice(3).trim(); }
      return;
    }
    if (inCode) { code.push(line); return; }
    if (!line.trim()) { flushParagraph(); flushList(); return; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph(); flushList();
      nodes.push(h(`h${heading[1].length}`, { key: `h-${nodes.length}` }, inlineMarkdown(heading[2])));
      return;
    }
    const item = line.match(/^[-*]\s+(.+)$/);
    if (item) { flushParagraph(); list.push(item[1]); return; }
    paragraph.push(line.trim());
  });

  if (inCode) flushCode();
  flushParagraph();
  flushList();
  return nodes;
}

function cardsToMarkdown(cards) {
  return cards.map((card) => [
    `## ${card.card_id}`, "",
    `- 主题：${card.topic_id}`,
    `- 结论：${card.claim}`,
    `- 来源：[${card.citation.title} #${card.citation.paragraph_id}](${card.citation.url})`,
    `- 置信度：${Math.round((card.confidence || 0) * 100)}%`, "",
    card.summary, "",
  ].join("\n")).join("\n");
}

function downloadFile(name, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
