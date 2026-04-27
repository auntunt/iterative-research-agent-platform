"use strict";
var useEffect = React.useEffect;
var useMemo = React.useMemo;
var useState = React.useState;
var API_BASE_URL = ((window.__APP_CONFIG__ && window.__APP_CONFIG__.API_BASE_URL) || "").replace(/\/+$/, "");
var TASKS_PAGE_SIZE = 8;

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return API_BASE_URL ? `${API_BASE_URL}${p}` : p;
}

function App() {
  const [taskText, setTaskText] = useState("研究迭代式多智能体系统如何缓解复杂 RAG 流程中的证据覆盖盲点");
  const [maxRounds, setMaxRounds] = useState(3);
  const [minEvidence, setMinEvidence] = useState(2);
  const [tasks, setTasks] = useState([]);
  const [taskPage, setTaskPage] = useState(1);
  const [taskPageMeta, setTaskPageMeta] = useState({ page: 1, page_size: TASKS_PAGE_SIZE, total: 0, total_pages: 0 });
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [evidence, setEvidence] = useState({ cards: [], critic_assessments: [] });
  const [customCards, setCustomCards] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("report");

  async function api(path, options) {
    const res = await fetch(apiUrl(path), { headers: { "Content-Type": "application/json" }, ...options });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function refresh() {
    const [taskPageRes, metricPage] = await Promise.all([
      api(`/tasks?page=${taskPage}&page_size=${TASKS_PAGE_SIZE}`),
      api("/metrics"),
    ]);
    setTasks(taskPageRes.tasks);
    setMetrics(metricPage);
    setTaskPageMeta({ page: taskPageRes.page, page_size: taskPageRes.page_size, total: taskPageRes.total, total_pages: taskPageRes.total_pages });
    setSelectedTaskIds((cur) => cur.filter((id) => taskPageRes.tasks.some((t) => t.task_id === id)));
    setSelectedId((cur) => {
      if (cur && taskPageRes.tasks.some((t) => t.task_id === cur)) return cur;
      return taskPageRes.tasks.length ? taskPageRes.tasks[0].task_id : null;
    });
  }

  async function submitTask(e) {
    e.preventDefault();
    setError("");
    try {
      const task = await api("/run_task", {
        method: "POST",
        body: JSON.stringify({
          task: taskText,
          metadata: {
            source: "ui", research_depth: "deep",
            max_research_rounds: Number(maxRounds),
            min_evidence_per_topic: Number(minEvidence),
            max_sources_per_query: 4, search_depth: "advanced",
            web_search_provider: "auto", max_topics: 5, report_format: "markdown",
          },
        }),
      });
      setSelectedId(task.task_id);
      setCustomCards([]);
      setActiveTab("report");
      await refresh();
    } catch (err) { setError(String(err.message || err)); }
  }

  async function deleteTask(taskId) {
    setError("");
    try {
      await api(`/task/${taskId}`, { method: "DELETE" });
      setCustomCards([]);
      setSelectedTaskIds((cur) => cur.filter((id) => id !== taskId));
      setSelected((cur) => (cur && cur.task_id === taskId ? null : cur));
      setEvidence((cur) => (selectedId === taskId ? { cards: [], critic_assessments: [] } : cur));
      setLogs((cur) => (selectedId === taskId ? [] : cur));
      setSelectedId((cur) => {
        if (cur !== taskId) return cur;
        const rem = tasks.filter((t) => t.task_id !== taskId);
        return rem.length ? rem[0].task_id : null;
      });
      if (tasks.length === 1 && taskPage > 1) setTaskPage((p) => Math.max(1, p - 1));
      await refresh();
    } catch (err) { setError(String(err.message || err)); }
  }

  async function deleteSelectedTasks() {
    if (!selectedTaskIds.length) return;
    setError("");
    try {
      const delSet = new Set(selectedTaskIds);
      await Promise.all(selectedTaskIds.map((id) => api(`/task/${id}`, { method: "DELETE" })));
      setCustomCards([]);
      setSelectedTaskIds([]);
      setSelected((cur) => (cur && delSet.has(cur.task_id) ? null : cur));
      setEvidence((cur) => (selectedId && delSet.has(selectedId) ? { cards: [], critic_assessments: [] } : cur));
      setLogs((cur) => (selectedId && delSet.has(selectedId) ? [] : cur));
      setSelectedId((cur) => {
        if (!cur || !delSet.has(cur)) return cur;
        const rem = tasks.filter((t) => !delSet.has(t.task_id));
        return rem.length ? rem[0].task_id : null;
      });
      if (selectedTaskIds.length >= tasks.length && taskPage > 1) setTaskPage((p) => Math.max(1, p - 1));
      await refresh();
    } catch (err) { setError(String(err.message || err)); }
  }

  function toggleTaskSelection(taskId) {
    setSelectedTaskIds((cur) => cur.includes(taskId) ? cur.filter((id) => id !== taskId) : [...cur, taskId]);
  }

  function toggleAllTaskSelection() {
    if (!tasks.length) return;
    setSelectedTaskIds((cur) => cur.length === tasks.length ? [] : tasks.map((t) => t.task_id));
  }

  useEffect(() => {
    refresh().catch((err) => setError(String(err.message || err)));
    const t = setInterval(() => refresh().catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [taskPage]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    async function loadDetail() {
      const [task, logPage, evidencePage] = await Promise.all([
        api(`/task/${selectedId}`),
        api(`/logs?task_id=${selectedId}&limit=80`),
        api(`/task/${selectedId}/evidence`),
      ]);
      if (!cancelled) { setSelected(task); setLogs(logPage.logs); setEvidence(evidencePage); }
    }
    loadDetail().catch((err) => setError(String(err.message || err)));
    const t = setInterval(() => loadDetail().catch(() => {}), 1500);
    return () => { cancelled = true; clearInterval(t); };
  }, [selectedId]);

  const timeline = useMemo(() => selected?.steps || [], [selected]);
  const allCards = useMemo(() => [...(evidence.cards || []), ...customCards], [evidence, customCards]);
  const latestAssessment = useMemo(() => {
    const a = evidence.critic_assessments || [];
    return a[a.length - 1] || null;
  }, [evidence]);

  function createCardFromSelection() {
    const text = String(window.getSelection ? window.getSelection() : "").trim();
    if (!text) { setError("请先在报告正文中选中文本。"); return; }
    setCustomCards((cards) => [{
      card_id: `selection-${Date.now()}`,
      topic_id: "user-selection",
      claim: selected?.task || "用户选中的报告证据",
      summary: text.slice(0, 1200),
      citation: { url: `task://${selected?.task_id || "local"}`, title: "选中的报告文本", paragraph_id: "selection", snippet: text.slice(0, 240) },
      confidence: 1,
      source_type: "user",
      created_at: new Date().toISOString(),
    }, ...cards]);
    setError("");
    if (window.getSelection) window.getSelection().removeAllRanges();
  }

  function exportCards(format) {
    if (!allCards.length) return;
    const isMarkdown = format === "markdown";
    downloadFile(
      `evidence-cards-${selected?.task_id || "local"}.${isMarkdown ? "md" : "json"}`,
      isMarkdown ? cardsToMarkdown(allCards) : JSON.stringify(allCards, null, 2),
      isMarkdown ? "text/markdown" : "application/json"
    );
  }

  const TABS = [
    { id: "report",   label: "研究报告" },
    { id: "evidence", label: `证据 (${allCards.length})` },
    { id: "timeline", label: "时间线" },
    { id: "logs",     label: "日志" },
  ];

  return h("main", { className: "shell" },
    // ── Masthead ────────────────────────────────────────────
    h("header", { className: "masthead" },
      h("div", { className: "masthead__inner" },
        h("p", { className: "eyebrow" }, "深度研究工作台"),
        h("h1", { className: "masthead__title" }, "研究智能体平台"),
        h("p", { className: "masthead__lead" }, "输入开放问题，让智能体分轮检索、交叉验证，并把每条结论追溯到证据卡片。")
      )
    ),

    // ── Workspace ───────────────────────────────────────────
    h("div", { className: "workspace" },

      // ── Sidebar ─────────────────────────────────────────
      h("aside", { className: "sidebar" },
        h("div", { className: "sidebar__inner" },

          // Form
          h("section", null,
            h("h2", { className: "section__title" }, "新建研究"),
            h("form", { onSubmit: submitTask, className: "researchForm" },
              h("p", { className: "researchForm__help" }, "系统默认深度模式、自动路由与 Markdown 输出，只需专注问题本身。"),
              h("textarea", {
                className: "researchForm__textarea",
                value: taskText,
                onChange: (e) => setTaskText(e.target.value),
                rows: 7,
                placeholder: "例如：研究智能体记忆的主流架构、评测方法与长期可靠性问题",
              }),
              h("div", { className: "researchForm__grid" },
                h("label", { className: "fieldLabel" }, "检索轮次",
                  h("input", { type: "number", min: 1, max: 5, value: maxRounds, onChange: (e) => setMaxRounds(e.target.value) })
                ),
                h("label", { className: "fieldLabel" }, "每主题证据数",
                  h("input", { type: "number", min: 1, max: 5, value: minEvidence, onChange: (e) => setMinEvidence(e.target.value) })
                )
              ),
              h("button", { type: "submit", className: "btn btn--primary btn--full" }, "开始研究 →")
            ),
            error ? h("p", { className: "errorMessage" }, error) : null
          ),

          // Global metrics
          metrics ? h("section", null,
            h("h3", { className: "section__subtitle" }, "平台概览"),
            h("div", { className: "metricsGrid" },
              h(MetricCard, { label: "研究次数", value: metrics.task_count }),
              h(MetricCard, { label: "成功率",   value: `${Math.round((metrics.task_metrics.success_rate || 0) * 100)}%` }),
              h(MetricCard, { label: "平均延迟", value: `${(metrics.task_metrics.avg_latency || 0).toFixed(2)}s` }),
              h(MetricCard, { label: "估算成本", value: `$${(metrics.task_metrics.estimated_cost || 0).toFixed(4)}` })
            )
          ) : null,

          // Task list header
          h("section", null,
            h("div", { className: "taskListHeader" },
              h("h3", { className: "section__subtitle" }, "最近研究"),
              h("div", { className: "taskBatchActions" },
                h("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: toggleAllTaskSelection, disabled: !tasks.length },
                  selectedTaskIds.length === tasks.length && tasks.length ? "取消全选" : "全选"
                ),
                h("button", { type: "button", className: "btn btn--danger btn--sm", onClick: deleteSelectedTasks, disabled: !selectedTaskIds.length },
                  selectedTaskIds.length ? `删除 (${selectedTaskIds.length})` : "删除所选"
                )
              )
            ),
            h("div", { className: "taskPager" },
              h("span", { className: "taskPager__info" },
                taskPageMeta.total
                  ? `第 ${taskPageMeta.page} / ${taskPageMeta.total_pages} 页 · 共 ${taskPageMeta.total} 条`
                  : "暂无研究记录"
              ),
              h("div", { className: "taskPager__actions" },
                h("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: () => setTaskPage((p) => Math.max(1, p - 1)), disabled: taskPageMeta.page <= 1 }, "← 上页"),
                h("button", { type: "button", className: "btn btn--ghost btn--sm", onClick: () => setTaskPage((p) => Math.min(taskPageMeta.total_pages || 1, p + 1)), disabled: !taskPageMeta.total_pages || taskPageMeta.page >= taskPageMeta.total_pages }, "下页 →")
              )
            ),
            h("div", { className: "taskList" },
              tasks.length
                ? tasks.map((task) => h(TaskItemComponent, {
                    key: task.task_id, task,
                    isActive: task.task_id === selectedId,
                    isChecked: selectedTaskIds.includes(task.task_id),
                    onOpen: () => setSelectedId(task.task_id),
                    onDelete: () => deleteTask(task.task_id),
                    onToggleSelect: () => toggleTaskSelection(task.task_id),
                  }))
                : h("p", { className: "emptyState" }, "暂无研究记录，提交第一个问题开始探索。")
            )
          )
        )
      ),

      // ── Detail ──────────────────────────────────────────
      h("section", { className: "detail" },
        selected ? h(React.Fragment, null,
          h("div", { className: "detailHeader" },
            h("div", { className: "detailHeader__copy" },
              h("p", { className: "detailHeader__trace" }, selected.trace_id),
              h("h2", { className: "detailHeader__title" }, selected.task)
            ),
            h(StatusBadge, { status: selected.status })
          ),
          h("div", { className: "insightStrip" },
            h(MetricCard, { label: "证据卡片", value: allCards.length }),
            h(MetricCard, { label: "研究步骤", value: timeline.filter((s) => s.agent === "researcher").length }),
            h(MetricCard, { label: "评审分数", value: latestAssessment ? Math.round((latestAssessment.coverage_score || 0) * 100) + "%" : "等待中" }),
            h(MetricCard, { label: "模型调用", value: selected.metrics.llm_calls })
          ),
          h(CriticBoxComponent, { assessment: latestAssessment }),
          h("nav", { className: "tabBar" },
            TABS.map(({ id, label }) =>
              h("button", {
                key: id, type: "button",
                className: `tabBar__tab${activeTab === id ? " tabBar__tab--active" : ""}`,
                onClick: () => setActiveTab(id),
              }, label)
            )
          ),
          activeTab === "report" ? h("div", { className: "tabContent" },
            selected.status === "running" && !selected.result
              ? h(RunningProgress, {
                  task: selected,
                  logs,
                  steps: timeline,
                  cards: allCards.length,
                  llmCalls: selected.metrics.llm_calls,
                })
              : h(React.Fragment, null,
                  h("div", { className: "reportActions" },
                    h("button", { type: "button", className: "btn btn--outline btn--sm", onClick: createCardFromSelection }, "选中生成卡片"),
                    h("button", { type: "button", className: "btn btn--outline btn--sm", onClick: () => exportCards("json") }, "导出 JSON"),
                    h("button", { type: "button", className: "btn btn--outline btn--sm", onClick: () => exportCards("markdown") }, "导出 MD")
                  ),
                  h("article", { className: "report markdownReport", tabIndex: 0 },
                    selected.result ? renderMarkdown(selected.result) : h("p", { className: "emptyReport" }, "等待输出…")
                  )
                )
          ) : null,
          activeTab === "evidence" ? h("div", { className: "tabContent" },
            allCards.length
              ? h("div", { className: "cardGrid" }, allCards.map((card) => h(EvidenceCard, { key: card.card_id, card })))
              : h("p", { className: "emptyState" }, "证据卡片将在智能体完成研究后显示在此处。")
          ) : null,
          activeTab === "timeline" ? h("div", { className: "tabContent" },
            h(AgentTimeline, { steps: timeline })
          ) : null,
          activeTab === "logs" ? h("div", { className: "tabContent" },
            h(LogsPanel, { logs })
          ) : null
        ) : h("div", { className: "detailEmpty" },
          h("div", { className: "detailEmpty__icon" }, "🔍"),
          h("p", { className: "detailEmpty__text" }, "从左侧选择一条研究记录，或提交新问题开始探索。")
        )
      )
    )
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
