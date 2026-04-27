"use strict";

function MetricCard({ label, value }) {
  return h("div", { className: "metricCard" },
    h("span", { className: "metricCard__label" }, label),
    h("strong", { className: "metricCard__value" }, value)
  );
}

function StatusBadge({ status }) {
  return h("span", { className: `statusBadge statusBadge--${status || "pending"}` },
    h("span", { className: "statusBadge__dot", "aria-hidden": "true" }),
    statusLabel(status)
  );
}

function EvidenceCard({ card }) {
  const body = preferredCardText(card);
  const snippet = cleanCardText((card.citation && card.citation.snippet) || "");
  const showSnippet = snippet && snippet !== body;
  const confidence = Math.round((card.confidence || 0) * 100);
  const u = (card.citation && card.citation.url) || "";
  const isLocal = !u.startsWith("http");
  const title = (card.citation && card.citation.title) || "";
  const paragraphId = (card.citation && card.citation.paragraph_id) || "";

  return h("article", { className: `evidenceCard${card.source_type === "user" ? " evidenceCard--user" : ""}` },
    h("header", { className: "evidenceCard__header" },
      h("span", { className: "evidenceCard__topic" }, card.topic_id),
      h("span", { className: "evidenceCard__confidence" }, `${confidence}%`)
    ),
    h("p", { className: "evidenceCard__claim" }, card.claim || card.topic_id),
    h("p", { className: "evidenceCard__body" }, body),
    showSnippet ? h("blockquote", { className: "evidenceCard__snippet" }, snippet) : null,
    h("div", { className: "evidenceCard__bar" },
      h("div", { className: "evidenceCard__barFill", style: { width: `${confidence}%` } })
    ),
    h("footer", { className: "evidenceCard__footer" },
      isLocal
        ? h("span", { className: "evidenceCard__source evidenceCard__source--local", title: u }, `${title} #${paragraphId}`)
        : h("a", { className: "evidenceCard__source", href: u, target: "_blank", rel: "noreferrer" }, `${title} #${paragraphId}`)
    )
  );
}

function CriticBoxComponent({ assessment }) {
  if (!assessment) return null;
  const ok = assessment.sufficient;
  return h("div", { className: `criticBox criticBox--${ok ? "ok" : "warn"}` },
    h("div", { className: "criticBox__header" },
      h("span", { className: "criticBox__icon", "aria-hidden": "true" }, ok ? "✓" : "⚠"),
      h("strong", null, ok ? "Critic 已批准证据" : "Critic 要求补充证据")
    ),
    h("p", { className: "criticBox__scores" },
      `覆盖度 ${score(assessment.coverage_score)} · 忠实度 ${score(assessment.faithfulness_score)} · 相关性 ${score(assessment.answer_relevancy_score)}`
    ),
    assessment.missing_topics && assessment.missing_topics.length
      ? h("p", { className: "criticBox__gaps" }, `缺口主题：${assessment.missing_topics.join("、")}`)
      : null
  );
}

function AgentTimeline({ steps }) {
  if (!steps || !steps.length) return h("p", { className: "emptyState" }, "暂无时间线数据。");
  return h("ol", { className: "timeline" },
    steps.map((step) => h("li", { key: step.step_id, className: "timelineItem" },
      h("div", { className: `timelineNode timelineNode--${step.status}` }, step.step_id),
      h("div", { className: "timelineContent" },
        h("div", { className: "timelineTop" },
          h("strong", { className: "timelineAgent" }, step.agent),
          h(StatusBadge, { status: step.status })
        ),
        h("p", { className: "timelineInput" }, step.input),
        h("p", { className: "timelineMeta" },
          `${step.llm_provider || "n/a"} ${step.llm_model || ""} · ${step.latency.toFixed(3)}s · 重试 ${step.retries}`
        )
      )
    ))
  );
}

function LogsPanel({ logs }) {
  if (!logs || !logs.length) return h("p", { className: "emptyState" }, "暂无日志。");
  return h("div", { className: "logsPanel" },
    logs.map((entry) => h("div", { key: entry.id, className: `logEntry logEntry--${entry.level.toLowerCase()}` },
      h("span", { className: "logEntry__level" }, entry.level),
      h("span", { className: "logEntry__event" }, entry.event),
      h("time", { className: "logEntry__time" }, new Date(entry.created_at).toLocaleString())
    ))
  );
}

function TaskItemComponent({ task, isActive, isChecked, onOpen, onDelete, onToggleSelect }) {
  return h("div", {
    className: ["taskItem", `taskItem--${task.status}`, isActive ? "taskItem--active" : "", isChecked ? "taskItem--selected" : ""].filter(Boolean).join(" "),
  },
    h("label", { className: "taskItem__check", onClick: (e) => e.stopPropagation() },
      h("input", { type: "checkbox", checked: isChecked, onChange: onToggleSelect, "aria-label": `选择研究：${task.task}` })
    ),
    h("button", { type: "button", className: "taskItem__body", onClick: onOpen },
      h("div", { className: "taskItem__meta" },
        h(StatusBadge, { status: task.status }),
        h("time", { className: "taskItem__time" }, formatTaskTime(task.created_at))
      ),
      h("p", { className: "taskItem__title" }, task.task),
      h("p", { className: "taskItem__subtitle" }, taskSummary(task))
    ),
    h("button", {
      type: "button", className: "taskItem__delete", title: "删除",
      onClick: (e) => { e.stopPropagation(); onDelete(); },
    }, "×")
  );
}

function RunningProgress({ task, logs, steps, cards, llmCalls }) {
  var elapsedState = useState(0);
  var elapsed = elapsedState[0];
  var setElapsed = elapsedState[1];

  useEffect(function() {
    var start = task.created_at ? new Date(task.created_at) : new Date();
    var timer = setInterval(function() {
      setElapsed(Math.floor((Date.now() - start.getTime()) / 1000));
    }, 1000);
    return function() { clearInterval(timer); };
  }, [task.task_id]);

  var researcherCount = steps.filter(function(s) { return s.agent === "researcher"; }).length;
  var hasCritic      = steps.some(function(s) { return s.agent === "critic"; });
  var hasSynth       = steps.some(function(s) { return s.agent === "synthesizer"; });

  var PHASES = [
    { label: "初始化任务",                        done: true,                            active: false },
    { label: "检索来源" + (researcherCount ? " ×" + researcherCount : ""), done: researcherCount > 0 && hasCritic, active: researcherCount > 0 && !hasCritic },
    { label: "Critic 评审证据",                   done: hasCritic && hasSynth,           active: hasCritic && !hasSynth },
    { label: "撰写研究报告",                      done: hasSynth,                        active: false },
  ];

  var recentLogs = logs.slice(-6).reverse();

  return h("div", { className: "runningProgress" },

    h("div", { className: "rp__hero" },
      h("div", { className: "rp__spinnerWrap" },
        h("div", { className: "rp__spinnerOuter" }),
        h("div", { className: "rp__spinnerInner" })
      ),
      h("div", { className: "rp__heroText" },
        h("p", { className: "rp__title" }, "深度研究进行中"),
        h("p", { className: "rp__subtitle" }, "智能体正在多轮检索来源、抽取证据并交叉验证，请耐心等待…"),
        h("p", { className: "rp__elapsed" }, "已用时 " + formatElapsed(elapsed))
      )
    ),

    h("div", { className: "rp__main" },

      h("div", { className: "rp__phases" },
        h("p", { className: "rp__sectionLabel" }, "阶段进度"),
        h("div", { className: "rp__phaseList" },
          PHASES.map(function(phase, i) {
            var cls = "rp__phase" + (phase.active ? " rp__phase--active" : "") + (phase.done ? " rp__phase--done" : "");
            return h("div", { key: i, className: cls },
              h("div", { className: "rp__phaseIcon" },
                phase.done ? "✓" : phase.active ? h("div", { className: "rp__phasePulse" }) : "·"
              ),
              h("span", { className: "rp__phaseLabel" }, phase.label)
            );
          })
        )
      ),

      h("div", { className: "rp__stats" },
        h("p", { className: "rp__sectionLabel" }, "实时数据"),
        h("div", { className: "rp__statGrid" },
          h("div", { className: "rp__stat" }, h("strong", null, cards),    h("span", null, "证据卡片")),
          h("div", { className: "rp__stat" }, h("strong", null, llmCalls), h("span", null, "LLM 调用")),
          h("div", { className: "rp__stat" }, h("strong", null, steps.length), h("span", null, "已完成步骤"))
        )
      )
    ),

    recentLogs.length ? h("div", { className: "rp__feed" },
      h("p", { className: "rp__sectionLabel" }, "最新动态"),
      h("div", { className: "rp__feedList" },
        recentLogs.map(function(entry) {
          return h("div", { key: entry.id, className: "rp__feedEntry" },
            h("span", { className: "rp__feedLevel rp__feedLevel--" + entry.level.toLowerCase() }, entry.level),
            h("span", { className: "rp__feedEvent" }, entry.event),
            h("time",  { className: "rp__feedTime" }, new Date(entry.created_at).toLocaleTimeString())
          );
        })
      )
    ) : null
  );
}
