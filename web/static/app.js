let currentRunId = null;
let currentOffset = 0;
const PAGE_SIZE = 50;
let currentTotal = 0;
let statusPoll = null;
let currentLang = localStorage.getItem("litmon_lang") || "en";
let searchTopics = [];

const i18n = {
  en: {
    appTitle: "Literature Search",
    appSubtitle: "Manual Crossref and OpenAlex search.",
    topicPreset: "Topic Preset",
    customTopic: "Custom search",
    keywords: "Keywords",
    keywordsPlaceholder: "climate adaptation, wastewater treatment, remote sensing",
    from: "From",
    to: "To",
    journalPool: "Journal Pool",
    advanced: "Advanced",
    synonyms: "Synonyms",
    commaSeparated: "comma separated",
    negativeKeywords: "Negative Keywords",
    negativePlaceholder: "portfolio, stock market",
    issn: "ISSN",
    includeConferences: "Include conferences",
    maxResults: "Max results/source",
    minScore: "Min score",
    search: "Search",
    results: "Results",
    rankedPapers: "Ranked Papers",
    ready: "Ready.",
    allRelevance: "All relevance",
    sortScore: "Sort: Score",
    sortDate: "Sort: Date",
    sortTitle: "Sort: Title",
    filterKeyword: "Keyword",
    filterJournal: "Journal",
    filterYear: "Year",
    abstract: "Abstract",
    apply: "Apply",
    title: "Title",
    journal: "Journal",
    year: "Year",
    relevance: "Relevance",
    score: "Score",
    links: "Links",
    emptyInitial: "Run a search to see results.",
    emptyNoMatch: "No matching papers.",
    prev: "Prev",
    next: "Next",
    close: "Close",
    allPools: "All configured pools",
    searching: "Searching. This can take a little while for broad ranges...",
    queued: "Run {id}: queued.",
    runStatus: "Run {id}: {status}. fetched {fetched}, scored {scored}{error}",
    searchFailed: "Search failed: {error}",
    loadFailed: "Failed to load results: {error}",
    statusFailed: "Failed to check run status: {error}",
    poolsFailed: "Failed to load journal pools: {error}",
    topicsFailed: "Failed to load topic presets: {error}",
    pageInfo: "{start}-{end} of {total}",
    noAbstract: "No abstract available.",
    topics: "Topics",
    scoreComponents: "Score Components",
    total: "Total",
    rule: "Rule",
    textBm25: "Text (BM25)",
    recency: "Recency",
    journalScore: "Journal",
    matchedPipelines: "Matched Pipelines",
    matchedKeywords: "Matched Keywords",
    keywordHits: "Keyword Hits",
    sources: "Sources",
    fullBreakdown: "Full Breakdown",
    none: "None",
    noKeywordHits: "No keyword hits.",
    noSourceRecords: "No source records.",
    openAccessPdf: "Open Access PDF"
  },
  zh: {
    appTitle: "文献检索",
    appSubtitle: "手动检索 Crossref 与 OpenAlex 文献。",
    topicPreset: "专题预设",
    customTopic: "自定义检索",
    keywords: "关键词",
    keywordsPlaceholder: "气候适应，污水处理，遥感，环境治理",
    from: "开始日期",
    to: "结束日期",
    journalPool: "期刊池",
    advanced: "高级选项",
    synonyms: "同义词",
    commaSeparated: "用逗号分隔",
    negativeKeywords: "排除词",
    negativePlaceholder: "股票市场，投资组合",
    issn: "ISSN",
    includeConferences: "包含会议论文",
    maxResults: "每来源最大结果",
    minScore: "最低分",
    search: "检索",
    results: "检索结果",
    rankedPapers: "排序文献",
    ready: "就绪。",
    allRelevance: "全部相关性",
    sortScore: "排序：分数",
    sortDate: "排序：日期",
    sortTitle: "排序：标题",
    filterKeyword: "关键词",
    filterJournal: "期刊",
    filterYear: "年份",
    abstract: "摘要",
    apply: "应用",
    title: "标题",
    journal: "期刊",
    year: "年份",
    relevance: "相关性",
    score: "分数",
    links: "链接",
    emptyInitial: "运行一次检索后查看结果。",
    emptyNoMatch: "没有匹配的文献。",
    prev: "上一页",
    next: "下一页",
    close: "关闭",
    allPools: "全部已配置期刊池",
    searching: "正在检索。范围较宽时可能需要一点时间...",
    queued: "任务 {id}：已排队。",
    runStatus: "任务 {id}：{status}。已获取 {fetched}，已评分 {scored}{error}",
    searchFailed: "检索失败：{error}",
    loadFailed: "加载结果失败：{error}",
    statusFailed: "检查任务状态失败：{error}",
    poolsFailed: "加载期刊池失败：{error}",
    topicsFailed: "加载专题预设失败：{error}",
    pageInfo: "{start}-{end} / {total}",
    noAbstract: "暂无摘要。",
    topics: "主题",
    scoreComponents: "评分组成",
    total: "总分",
    rule: "规则分",
    textBm25: "文本分（BM25）",
    recency: "新近度",
    journalScore: "期刊",
    matchedPipelines: "命中管线",
    matchedKeywords: "命中关键词",
    keywordHits: "关键词命中",
    sources: "来源",
    fullBreakdown: "完整拆解",
    none: "无",
    noKeywordHits: "无关键词命中。",
    noSourceRecords: "无来源记录。",
    openAccessPdf: "开放获取 PDF"
  }
};

const today = new Date();
const prior = new Date(today);
prior.setDate(today.getDate() - 30);
document.querySelector('[name="date_to"]').value = today.toISOString().slice(0, 10);
document.querySelector('[name="date_from"]').value = prior.toISOString().slice(0, 10);

async function loadPools() {
  const res = await fetch("/api/journal-pools");
  const data = await res.json();
  const select = document.getElementById("journal-pools");
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.dataset.i18n = "allPools";
  all.textContent = t("allPools");
  select.appendChild(all);
  for (const pool of data.pools || []) {
    const opt = document.createElement("option");
    opt.value = pool.id;
    opt.textContent = `${pool.name} (${pool.issns.length})`;
    if (pool.id === "pool_environment_high_quality") {
      opt.selected = true;
    }
    select.appendChild(opt);
  }
  applyTranslations();
}

async function loadTopics() {
  const res = await fetch("/api/search-topics");
  const data = await res.json();
  searchTopics = data.topics || [];
  const select = document.getElementById("topic-presets");
  select.innerHTML = "";
  const custom = document.createElement("option");
  custom.value = "";
  custom.dataset.i18n = "customTopic";
  custom.textContent = t("customTopic");
  select.appendChild(custom);
  for (const topic of searchTopics) {
    const opt = document.createElement("option");
    opt.value = topic.id;
    opt.textContent = topicLabel(topic);
    select.appendChild(opt);
  }
  applyTranslations();
}

function listValue(form, name) {
  const value = form.get(name);
  if (!value) return [];
  return String(value).split(",").map(v => v.trim()).filter(Boolean);
}

document.getElementById("search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const pool = form.get("journal_pool_ids");
  const payload = {
    date_from: form.get("date_from"),
    date_to: form.get("date_to"),
    keywords: listValue(form, "keywords"),
    synonyms: listValue(form, "synonyms"),
    negative_keywords: listValue(form, "negative_keywords"),
    journal_pool_ids: pool ? [pool] : [],
    journal_issns: listValue(form, "journal_issns"),
    include_conferences: form.get("include_conferences") === "on",
    min_score: Number(form.get("min_score") || 0),
    max_results_per_source: Number(form.get("max_results_per_source") || 100)
  };
  setStatus(t("searching"));
  try {
    const res = await fetch("/api/search-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    currentRunId = data.search_run_id;
    currentOffset = 0;
    setStatus(formatMessage("queued", { id: currentRunId }));
    pollRunStatus();
  } catch (err) {
    setStatus(formatMessage("searchFailed", { error: err }));
  }
});

document.getElementById("refresh-results").addEventListener("click", () => { currentOffset = 0; loadResults(); });
document.getElementById("export-csv").addEventListener("click", () => exportResults("csv"));
document.getElementById("export-bibtex").addEventListener("click", () => exportResults("bibtex"));
document.getElementById("export-markdown").addEventListener("click", () => exportResults("markdown"));
document.getElementById("close-detail").addEventListener("click", () => {
  document.getElementById("detail").classList.remove("open");
});
document.getElementById("page-prev").addEventListener("click", () => { currentOffset = Math.max(0, currentOffset - PAGE_SIZE); loadResults(); });
document.getElementById("page-next").addEventListener("click", () => { currentOffset += PAGE_SIZE; loadResults(); });
document.getElementById("topic-presets").addEventListener("change", event => {
  const topic = searchTopics.find(item => item.id === event.target.value);
  if (topic) applyTopicPreset(topic);
});

function filterQuery() {
  const params = new URLSearchParams();
  const relevance = document.getElementById("filter-relevance").value;
  const sort = document.getElementById("filter-sort").value;
  const keyword = document.getElementById("filter-keyword").value;
  const journal = document.getElementById("filter-journal").value;
  const year = document.getElementById("filter-year").value;
  const hasDoi = document.getElementById("filter-has-doi").checked;
  const hasAbstract = document.getElementById("filter-has-abstract").checked;
  if (relevance) params.set("relevance", relevance);
  if (sort) params.set("sort", sort);
  if (keyword) params.set("keyword", keyword);
  if (journal) params.set("journal", journal);
  if (year) params.set("year", year);
  if (hasDoi) params.set("has_doi", "true");
  if (hasAbstract) params.set("has_abstract", "true");
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(currentOffset));
  return params;
}

function updatePagination(total) {
  currentTotal = total;
  const pag = document.getElementById("pagination");
  const info = document.getElementById("page-info");
  const prevBtn = document.getElementById("page-prev");
  const nextBtn = document.getElementById("page-next");
  if (total === 0) {
    pag.hidden = true;
    return;
  }
  pag.hidden = false;
  const start = currentOffset + 1;
  const end = Math.min(currentOffset + PAGE_SIZE, total);
  info.textContent = formatMessage("pageInfo", { start, end, total });
  prevBtn.disabled = currentOffset === 0;
  nextBtn.disabled = end >= total;
}

async function loadResults() {
  if (!currentRunId) return;
  const params = filterQuery();
  try {
    const res = await fetch(`/api/search-runs/${currentRunId}/results?${params}`);
    const data = await res.json();
    const body = document.getElementById("results-body");
    if (!data.results || data.results.length === 0) {
      body.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(t("emptyNoMatch"))}</td></tr>`;
      updatePagination(0);
      return;
    }
    updatePagination(data.total);
    body.innerHTML = "";
    for (const paper of data.results) {
      const tr = document.createElement("tr");
      const year = paper.publication_date || paper.year || "";
      const authors = paper.authors && paper.authors.length
        ? paper.authors.slice(0, 3).join(", ") + (paper.authors.length > 3 ? " et al." : "")
        : "";
      const doiLink = paper.doi ? safeLink(`https://doi.org/${encodeURIComponent(paper.doi)}`, "DOI") : "";
      const oaLink = safeLink(paper.oa_url, "OA");
      const links = [doiLink, oaLink].filter(Boolean).join(" ");
      tr.innerHTML = `
        <td><button class="paper-title" data-id="${paper.id}">${escapeHtml(paper.title || "")}</button><div class="muted">${escapeHtml(authors)}</div></td>
        <td>${escapeHtml(paper.journal || "")}</td>
        <td>${escapeHtml(year)}</td>
        <td><span class="badge ${paper.relevance_level}">${paper.relevance_level}</span></td>
        <td>${Number(paper.total_score).toFixed(1)}</td>
        <td>${links}</td>
      `;
      body.appendChild(tr);
    }
    body.querySelectorAll(".paper-title").forEach(button => {
      button.addEventListener("click", () => showDetail(button.dataset.id));
    });
  } catch (err) {
    setStatus(formatMessage("loadFailed", { error: err }));
  }
}

async function pollRunStatus() {
  if (!currentRunId) return;
  if (statusPoll) clearTimeout(statusPoll);
  try {
    const res = await fetch(`/api/search-runs/${currentRunId}`);
    const data = await res.json();
    setStatus(formatMessage("runStatus", {
      id: currentRunId,
      status: data.status,
      fetched: data.total_fetched,
      scored: data.total_scored,
      error: data.error_message ? `; ${data.error_message}` : ""
    }));
    if (["queued", "running"].includes(data.status)) {
      statusPoll = setTimeout(pollRunStatus, 1500);
      return;
    }
    currentOffset = 0;
    await loadResults();
  } catch (err) {
    setStatus(formatMessage("statusFailed", { error: err }));
  }
}

async function showDetail(paperId) {
  const res = await fetch(`/api/search-runs/${currentRunId}/results/${paperId}`);
  const paper = await res.json();
  const content = document.getElementById("detail-content");
  const authors = (paper.authors || []).join(", ") || "N/A";
  const topics = (paper.topics || []).join(", ") || "N/A";
  const breakdown = paper.score_breakdown || {};
  const keywordHits = (paper.keyword_hits || []).map(
    hit => `<li><strong>${escapeHtml(hit.keyword)}</strong> in ${escapeHtml(hit.field)} (${escapeHtml(hit.hit_type)}, weight ${hit.weight}): ${escapeHtml(hit.snippet || "")}</li>`
  ).join("");
  const sources = (paper.source_records || []).map(
    src => `<li>${escapeHtml(src.source)}: ${escapeHtml(src.source_work_id || "")}</li>`
  ).join("");
  const breakdownRows = Object.entries(breakdown).map(
    ([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`
  ).join("");

  content.innerHTML = `
    <h2>${escapeHtml(paper.title || "")}</h2>
    <p class="detail-meta">${escapeHtml(paper.journal || "N/A")} &middot; ${escapeHtml(paper.publication_date || paper.year || "N/A")}</p>
    <p class="detail-meta">${escapeHtml(authors)}</p>
    <p class="detail-meta">${escapeHtml(t("topics"))}: ${escapeHtml(topics)}</p>
    ${paper.doi ? `<p class="detail-meta">${safeLink(`https://doi.org/${encodeURIComponent(paper.doi)}`, `DOI: ${paper.doi}`)}</p>` : ""}
    ${paper.oa_url ? `<p class="detail-meta">${safeLink(paper.oa_url, t("openAccessPdf"))}</p>` : ""}

    <h3>${escapeHtml(t("abstract"))}</h3>
    <p class="abstract-text">${escapeHtml(paper.abstract || t("noAbstract"))}</p>

    <h3>${escapeHtml(t("scoreComponents"))}</h3>
    <table class="detail-table">
      <tr><td>${escapeHtml(t("total"))}</td><td><strong>${paper.total_score}</strong></td></tr>
      <tr><td>${escapeHtml(t("rule"))}</td><td>${paper.rule_score}</td></tr>
      <tr><td>${escapeHtml(t("textBm25"))}</td><td>${paper.text_score}</td></tr>
      <tr><td>${escapeHtml(t("recency"))}</td><td>${paper.recency_score}</td></tr>
      <tr><td>${escapeHtml(t("journalScore"))}</td><td>${paper.journal_score}</td></tr>
    </table>

    <h3>${escapeHtml(t("matchedPipelines"))}</h3>
    <ul>${(paper.matched_pipelines || []).map(p => `<li>${escapeHtml(p)}</li>`).join("") || `<li>${escapeHtml(t("none"))}</li>`}</ul>

    <h3>${escapeHtml(t("matchedKeywords"))}</h3>
    <ul>${(paper.matched_keywords || []).map(k => `<li>${escapeHtml(k)}</li>`).join("") || `<li>${escapeHtml(t("none"))}</li>`}</ul>

    <h3>${escapeHtml(t("keywordHits"))}</h3>
    <ul>${keywordHits || `<li>${escapeHtml(t("noKeywordHits"))}</li>`}</ul>

    <h3>${escapeHtml(t("sources"))}</h3>
    <ul>${sources || `<li>${escapeHtml(t("noSourceRecords"))}</li>`}</ul>

    <h3>${escapeHtml(t("fullBreakdown"))}</h3>
    <table class="detail-table">${breakdownRows}</table>
  `;
  document.getElementById("detail").classList.add("open");
}

function exportResults(format) {
  if (!currentRunId) return;
  const params = filterQuery();
  params.delete("limit");
  params.delete("offset");
  params.set("format", format);
  window.open(`/api/search-runs/${currentRunId}/export?${params}`, "_blank");
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function t(key) {
  return (i18n[currentLang] && i18n[currentLang][key]) || i18n.en[key] || key;
}

function formatMessage(key, values) {
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, value),
    t(key)
  );
}

function applyTranslations() {
  document.documentElement.lang = currentLang === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll(".language-switch button").forEach(button => {
    button.classList.toggle("active", button.dataset.lang === currentLang);
  });
  const topicSelect = document.getElementById("topic-presets");
  if (topicSelect) {
    for (const option of topicSelect.options) {
      const topic = searchTopics.find(item => item.id === option.value);
      if (topic) option.textContent = topicLabel(topic);
    }
  }
}

function topicLabel(topic) {
  if (currentLang === "zh" && topic.name_zh) {
    return topic.name_zh;
  }
  return topic.name || topic.id;
}

function applyTopicPreset(topic) {
  const form = document.getElementById("search-form");
  setField(form, "keywords", topic.keywords);
  setField(form, "synonyms", topic.synonyms);
  setField(form, "negative_keywords", topic.negative_keywords);
  setField(form, "journal_issns", []);
  setField(form, "max_results_per_source", topic.max_results_per_source || 100);
  setField(form, "min_score", topic.min_score || 0);
  if (topic.date_from) form.elements.date_from.value = topic.date_from;
  form.elements.include_conferences.checked = Boolean(topic.include_conferences);
  const pool = (topic.journal_pool_ids || [])[0] || "";
  form.elements.journal_pool_ids.value = pool;
  form.querySelector("details").open = true;
}

function setField(form, name, value) {
  const input = form.elements[name];
  if (!input) return;
  input.value = Array.isArray(value) ? value.join(", ") : (value || "");
}

document.querySelectorAll(".language-switch button").forEach(button => {
  button.addEventListener("click", () => {
    currentLang = button.dataset.lang;
    localStorage.setItem("litmon_lang", currentLang);
    applyTranslations();
    updatePagination(currentTotal);
  });
});

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function safeLink(url, label) {
  if (!url) return "";
  let parsed;
  try {
    parsed = new URL(url, window.location.origin);
  } catch (_err) {
    return "";
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    return "";
  }
  const a = document.createElement("a");
  a.href = parsed.href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = label;
  return a.outerHTML;
}

applyTranslations();
loadPools().catch(err => setStatus(formatMessage("poolsFailed", { error: err })));
loadTopics().catch(err => setStatus(formatMessage("topicsFailed", { error: err })));
