const form = document.querySelector("#report-form");
const reportTypeInput = document.querySelector("#report-type");
const tabsContainer = document.querySelector("#import-tabs");
const tabs = [...document.querySelectorAll(".import-tab")];
const panes = [...document.querySelectorAll(".import-pane")];
const archiveInput = document.querySelector("#package-archive");
const folderInput = document.querySelector("#package-folder");
const pathInput = document.querySelector("#package-path");
const modelInput = document.querySelector("#model");
const effortInput = document.querySelector("#reasoning-effort");
const fastModeInput = document.querySelector("#codex-fast-mode");
const fastModeRow = document.querySelector("#fast-mode-row");
const submitButton = document.querySelector("#generate-button");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const jobView = document.querySelector("#job-view");
const jobStatus = document.querySelector("#job-status");
const jobLog = document.querySelector("#job-log");
const jobError = document.querySelector("#job-error");
const resultActions = document.querySelector("#result-actions");
const stageList = document.querySelector("#stage-list");
const cancelButton = document.querySelector("#cancel-job");
const progressPanel = document.querySelector(".progress-panel");
const jobList = document.querySelector("#job-list");
const jobCount = document.querySelector("#job-count");
const refreshJobsButton = document.querySelector("#refresh-jobs");

const VIDEO_STAGES = [
  ["ingest", "导入校验", "字幕契约与质量门"],
  ["analyze", "原意分析与成稿", "理解增强、编辑规划与可视化成稿"],
  ["render", "页面渲染", "Markdown 与 HTML"],
  ["html_validate", "网页验收", "链接、布局与映射"],
  ["complete", "完成交付", "产物一致性确认"],
];

const MATERIAL_STAGES = [
  ["ingest", "素材解包", "ZIP 安全校验与文件登记"],
  ["extract", "文字提取", "Word、文本与图片来源整理"],
  ["analyze", "内容分析", "逐份读取与来源覆盖"],
  ["synthesize", "主题归纳", "关联、分歧与重点整合"],
  ["draft", "报告成稿", "素材层、分析层与判断层"],
  ["render", "页面渲染", "Markdown 转为 HTML"],
  ["validate", "质量验收", "来源覆盖与页面完整性"],
  ["complete", "完成交付", "产物一致性确认"],
];

let reportType = "video";
let importMode = "zip";
let engine = "codex";
let serviceConfig = null;
let modelCatalog = { codex: [], opencode: [] };
let pollTimer = null;
let listTimer = null;
let stageSignature = "";
let currentJobId = null;
let jobHistory = [];
let hasActiveJob = false;
let submitting = false;

function showError(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function renderStages(definitions) {
  const normalized = definitions.map((item) => Array.isArray(item)
    ? { key: item[0], label: item[1], description: item[2] }
    : item);
  const signature = normalized.map((item) => item.key).join("|");
  if (signature === stageSignature) return;
  stageSignature = signature;
  stageList.replaceChildren();
  normalized.forEach((item, index) => {
    const row = document.createElement("li");
    row.dataset.stage = item.key;
    row.dataset.index = String(index + 1);
    const marker = document.createElement("i");
    marker.textContent = String(index + 1);
    const copy = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = item.label;
    const description = document.createElement("small");
    description.textContent = item.description;
    copy.append(title, description);
    row.append(marker, copy);
    stageList.append(row);
  });
}

function selectMode(mode) {
  importMode = reportType === "material" ? "zip" : mode;
  tabs.forEach((tab) => {
    const selected = tab.dataset.mode === importMode;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  panes.forEach((pane) => {
    const selected = pane.dataset.pane === importMode;
    pane.classList.toggle("active", selected);
    pane.hidden = !selected;
  });
}

function selectReportType(nextType, { resetFile = false } = {}) {
  reportType = nextType;
  const material = reportType === "material";
  tabsContainer.hidden = material;
  document.querySelector("#material-mode-note").hidden = !material;
  document.querySelector("#input-title").textContent = material
    ? "导入素材 ZIP"
    : "导入视频字幕包";
  document.querySelector("#format-label").textContent = material
    ? "Word · 文本 · 图片"
    : "标准字幕包";
  document.querySelector("#report-type-description").textContent = material
    ? "汇总 ZIP 内全部文字与图片素材，生成一份带来源追踪的综合报告。"
    : "忠实整理单个视频的原意，并生成可视化单层报告；不做外部研判或 Agent 综合判断。";
  document.querySelector("#archive-title").textContent = material
    ? "选择素材 ZIP"
    : "选择字幕包 ZIP";
  document.querySelector("#archive-help").textContent = material
    ? "支持 TXT、Markdown、HTML、DOC/DOCX、RTF、PNG、JPG 和 WebP"
    : "压缩包内需包含唯一的 package.json 及其引用文件";
  document.querySelector("#content-id-label").textContent = material ? "素材 ID" : "视频 ID";
  selectMode(material ? "zip" : importMode);
  renderStages(material ? MATERIAL_STAGES : VIDEO_STAGES);
  updateEffortDescription();
  if (resetFile) {
    archiveInput.value = "";
    document.querySelector("#archive-name").textContent = "尚未选择文件";
  }
}

tabs.forEach((tab) => tab.addEventListener("click", () => selectMode(tab.dataset.mode)));
reportTypeInput.addEventListener("change", () => {
  selectReportType(reportTypeInput.value, { resetFile: true });
  showError(formError, "");
});

archiveInput.addEventListener("change", () => {
  document.querySelector("#archive-name").textContent = archiveInput.files[0]?.name || "尚未选择文件";
});

folderInput.addEventListener("change", () => {
  const first = folderInput.files[0];
  const folder = first?.webkitRelativePath?.split("/")[0];
  document.querySelector("#folder-name").textContent = folder
    ? `${folder} · ${folderInput.files.length} 个文件`
    : "尚未选择目录";
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    serviceConfig = config;
    fastModeInput.checked = config.default_codex_service_tier === "fast";
    document.querySelectorAll("[data-engine-card]").forEach((card) => {
      const name = card.dataset.engineCard;
      const available = config.engines[name]?.available;
      card.classList.toggle("disabled", !available);
      card.querySelector("input").disabled = !available;
    });
    if (!config.engines.codex?.available && config.engines.opencode?.available) {
      document.querySelector('input[name="engine"][value="opencode"]').checked = true;
      engine = "opencode";
    }
    await selectEngine(engine);
    document.querySelector("#max-upload").textContent = `${config.max_upload_mb} MB`;
  } catch {
    showError(formError, "无法读取服务配置，请刷新页面重试。");
  }
}

function updateEffortDescription() {
  document.querySelector("#effort-label").textContent = reportType === "video" ? "推理强度上限" : "推理强度";
  document.querySelector("#effort-note").textContent = engine === "codex"
    ? (reportType === "video"
      ? "选项会随模型变化；原意分析与成稿最高使用 high。"
      : "选项会随当前 Codex 模型自动变化。")
    : "选项来自该 OpenCode 模型的本机元数据，并作为 --variant 传入。";
}

function updateModelDependentControls() {
  const selected = modelCatalog[engine].find((item) => item.id === modelInput.value);
  const efforts = selected?.reasoning_efforts
    || serviceConfig?.reasoning_efforts?.[engine]
    || ["high"];
  const previousEffort = effortInput.value;
  effortInput.replaceChildren();
  efforts.forEach((effort) => {
    const option = document.createElement("option");
    option.value = effort;
    option.textContent = effort;
    effortInput.append(option);
  });
  const preferredEffort = selected?.default_reasoning_effort
    || serviceConfig?.default_reasoning_effort
    || "high";
  effortInput.value = efforts.includes(previousEffort)
    ? previousEffort
    : (efforts.includes(preferredEffort) ? preferredEffort : efforts[0]);

  const fastSupported = engine === "codex" && modelInput.value === "gpt-5.6-sol";
  fastModeInput.disabled = !fastSupported;
  fastModeRow.classList.toggle("disabled", !fastSupported);
  if (!selected) return;
  const modality = selected.vision ? "支持文字与图片" : "仅支持文字输入";
  const source = engine === "codex" ? "本机 Codex 模型清单" : "本机 OpenCode 已配置模型";
  document.querySelector("#model-note").textContent = [
    selected.description,
    modality,
    source,
  ].filter(Boolean).join(" · ");
}

async function loadEngineModels(requestedEngine) {
  modelInput.disabled = true;
  modelInput.replaceChildren();
  const loadingOption = document.createElement("option");
  loadingOption.value = "";
  loadingOption.textContent = "正在读取本机模型…";
  modelInput.append(loadingOption);
  document.querySelector("#model-note").textContent = requestedEngine === "codex"
    ? "正在读取本机 Codex 模型清单…"
    : "正在读取本机 OpenCode 模型清单…";
  try {
    const response = await fetch(`/api/models?engine=${requestedEngine}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "读取模型失败");
    if (engine !== requestedEngine) return;
    if (!Array.isArray(payload.models) || !payload.models.length) {
      throw new Error("没有找到可用于报告生成的模型");
    }
    modelCatalog[requestedEngine] = payload.models;
    modelInput.replaceChildren();
    payload.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.label;
      modelInput.append(option);
    });
    const configuredDefault = serviceConfig?.default_models?.[requestedEngine];
    modelInput.value = payload.models.some((item) => item.id === configuredDefault)
      ? configuredDefault
      : payload.models[0].id;
    modelInput.disabled = false;
    updateModelDependentControls();
  } catch (error) {
    if (engine !== requestedEngine) return;
    modelInput.replaceChildren();
    const unavailableOption = document.createElement("option");
    unavailableOption.value = "";
    unavailableOption.textContent = "模型清单不可用";
    modelInput.append(unavailableOption);
    document.querySelector("#model-note").textContent = `未能读取模型列表：${error.message}`;
  }
}

async function selectEngine(nextEngine) {
  engine = nextEngine;
  document.querySelectorAll("[data-engine-card]").forEach((card) => {
    card.classList.toggle("active", card.dataset.engineCard === engine);
  });
  document.querySelector("#engine-warning").hidden = engine !== "opencode";
  document.querySelector("#model-label").textContent = engine === "codex" ? "Codex 模型" : "OpenCode 模型";
  updateEffortDescription();
  fastModeRow.hidden = engine !== "codex";
  await loadEngineModels(engine);
}

document.querySelectorAll('input[name="engine"]').forEach((input) => {
  input.addEventListener("change", () => selectEngine(input.value));
});
modelInput.addEventListener("change", updateModelDependentControls);

function stageClass(status) {
  return ["pending", "running", "completed", "failed"].includes(status) ? status : "pending";
}

function updateStages(statuses = {}) {
  document.querySelectorAll("#stage-list li").forEach((item) => {
    item.classList.remove("pending", "running", "completed", "failed");
    const status = stageClass(statuses[item.dataset.stage] || "pending");
    item.classList.add(status);
    item.querySelector("i").textContent = status === "completed" ? "✓" : item.dataset.index;
  });
}

function displayStatus(status) {
  if (["queued", "running"].includes(status)) return "running";
  if (status === "completed") return "completed";
  return "failed";
}

function statusLabel(status) {
  return {
    running: "进行中",
    completed: "已完成",
    failed: "失败",
  }[displayStatus(status)] || "失败";
}

function updateStatus(status) {
  const normalized = displayStatus(status);
  jobStatus.textContent = statusLabel(status);
  jobStatus.className = `status-chip ${normalized}`;
}

function engineLabel(value) {
  return value === "codex" ? "Codex CLI" : value === "opencode" ? "OpenCode" : value || "—";
}

function reportTypeLabel(value) {
  return value === "material" ? "素材报告" : "视频报告";
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function secondsSince(value) {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return null;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
}

function formatElapsed(seconds) {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes} 分 ${remainder} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function formatRecency(value) {
  const seconds = secondsSince(value);
  if (seconds === null) return "尚无事件";
  if (seconds < 3) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  return `${Math.floor(seconds / 60)} 分钟前`;
}

function syncSubmitButton() {
  submitButton.disabled = submitting || hasActiveJob;
  submitButton.querySelector("span").textContent = submitting
    ? "正在提交…"
    : hasActiveJob
      ? "报告生成中"
      : "开始生成报告";
}

function stopJobPolling() {
  if (pollTimer === null) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

function renderEmptyJobState() {
  stopJobPolling();
  currentJobId = null;
  emptyState.hidden = false;
  jobView.hidden = true;
  jobStatus.textContent = "等待任务";
  jobStatus.className = "status-chip idle";
  renderJobList(jobHistory);
}

function renderSubmittingState() {
  stopJobPolling();
  currentJobId = null;
  renderJobList(jobHistory);
  emptyState.hidden = true;
  jobView.hidden = false;
  renderStages(reportType === "material" ? MATERIAL_STAGES : VIDEO_STAGES);
  updateStages({});
  jobStatus.textContent = "提交中";
  jobStatus.className = "status-chip running";
  document.querySelector("#job-title").textContent = "正在创建新任务";
  document.querySelector("#detail-report-type").textContent = reportTypeLabel(reportType);
  document.querySelector("#content-id-label").textContent = reportType === "material" ? "素材 ID" : "视频 ID";
  document.querySelector("#content-id").textContent = "等待服务确认";
  document.querySelector("#detail-engine").textContent = engineLabel(engine);
  document.querySelector("#detail-model").textContent = modelInput.value || "—";
  document.querySelector("#detail-effort").textContent = effortInput.value || "—";
  document.querySelector("#detail-service-tier").textContent = engine === "codex"
    ? (fastModeInput.checked && !fastModeInput.disabled ? "Fast" : "标准")
    : "—";
  document.querySelector("#detail-created-at").textContent = "刚刚";
  document.querySelector("#detail-job-id").textContent = "待分配";
  document.querySelector("#detail-started-at").textContent = "—";
  document.querySelector("#detail-finished-at").textContent = "—";
  document.querySelector("#detail-package").textContent = "正在上传并校验输入";
  document.querySelector("#detail-run-mode").textContent = "复用或续跑";
  document.querySelector("#job-activity").textContent = "正在上传输入并创建新任务…";
  document.querySelector("#job-progress-value").textContent = "0%";
  document.querySelector("#job-progress-bar").style.width = "0%";
  document.querySelector("#job-current-stage").textContent = "准备中";
  document.querySelector("#job-stage-elapsed").textContent = "0 秒";
  document.querySelector("#job-last-event").textContent = "刚刚";
  document.querySelector("#job-heartbeat").textContent = "等待服务确认";
  document.querySelector("#job-heartbeat").classList.remove("stale");
  const pulse = document.querySelector("#activity-pulse");
  pulse.classList.add("running");
  pulse.classList.remove("stale");
  jobLog.textContent = "正在上传输入并创建新任务…";
  document.querySelector(".log-panel summary span").textContent = "等待任务 ID";
  const rawLogLink = document.querySelector("#raw-log-link");
  rawLogLink.hidden = true;
  rawLogLink.removeAttribute("href");
  cancelButton.hidden = true;
  resultActions.hidden = true;
  showError(jobError, "");
}

function renderJobList(jobs) {
  jobList.replaceChildren();
  jobCount.textContent = `${jobs.length} 个任务`;
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "task-empty";
    empty.textContent = "本次运行尚无报告任务。";
    jobList.append(empty);
    return;
  }
  jobs.forEach((job) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "job-card";
    card.classList.toggle("selected", job.job_id === currentJobId);
    card.dataset.jobId = job.job_id;
    card.setAttribute("aria-pressed", String(job.job_id === currentJobId));

    const head = document.createElement("div");
    head.className = "job-card-head";
    const copy = document.createElement("div");
    copy.className = "job-card-copy";
    const title = document.createElement("h3");
    title.textContent = job.title || job.content_id;
    const identifier = document.createElement("span");
    identifier.className = "job-card-id";
    identifier.textContent = `${reportTypeLabel(job.report_type)} · ${job.content_id}`;
    copy.append(title, identifier);
    const state = document.createElement("span");
    state.className = `task-state ${displayStatus(job.status)}`;
    state.textContent = statusLabel(job.status);
    head.append(copy, state);

    const meta = document.createElement("div");
    meta.className = "job-card-meta";
    const engineMeta = document.createElement("span");
    engineMeta.innerHTML = "<b>引擎</b> ";
    engineMeta.append(document.createTextNode(engineLabel(job.engine)));
    const modelMeta = document.createElement("span");
    modelMeta.innerHTML = "<b>模型</b> ";
    modelMeta.append(document.createTextNode(job.model || "—"));
    const timeMeta = document.createElement("span");
    timeMeta.innerHTML = "<b>创建</b> ";
    timeMeta.append(document.createTextNode(formatTime(job.created_at)));
    meta.append(engineMeta, modelMeta, timeMeta);
    if (job.activity && displayStatus(job.status) === "running") {
      const activityMeta = document.createElement("span");
      activityMeta.innerHTML = "<b>当前</b> ";
      activityMeta.append(document.createTextNode(job.activity));
      meta.append(activityMeta);
    }
    card.append(head, meta);
    card.addEventListener("click", () => openJob(job.job_id, { scroll: true }));
    jobList.append(card);
  });
}

async function loadJobs({ selectActive = false } = {}) {
  const response = await fetch("/api/jobs", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "无法读取任务列表");
  jobHistory = Array.isArray(payload.jobs) ? payload.jobs : [];
  hasActiveJob = jobHistory.some((job) => displayStatus(job.status) === "running");
  renderJobList(jobHistory);
  syncSubmitButton();
  const active = jobHistory.find((job) => displayStatus(job.status) === "running");
  if (selectActive && active && active.job_id !== currentJobId) {
    await openJob(active.job_id);
    return;
  }
  if (
    currentJobId
    && !submitting
    && !jobHistory.some((job) => job.job_id === currentJobId)
  ) {
    renderEmptyJobState();
  }
}

function renderJobDetails(job) {
  if (job.stage_definitions) renderStages(job.stage_definitions);
  updateStatus(job.status);
  updateStages(job.stage_statuses);
  document.querySelector("#job-title").textContent = job.title || job.content_id;
  document.querySelector("#detail-report-type").textContent = reportTypeLabel(job.report_type);
  document.querySelector("#content-id-label").textContent = job.report_type === "material" ? "素材 ID" : "视频 ID";
  document.querySelector("#content-id").textContent = job.content_id;
  document.querySelector("#detail-engine").textContent = engineLabel(job.engine);
  document.querySelector("#detail-model").textContent = job.model || "—";
  document.querySelector("#detail-effort").textContent = job.reasoning_effort || "—";
  document.querySelector("#detail-service-tier").textContent = job.engine === "codex"
    ? (job.codex_service_tier === "fast" ? "Fast" : "标准")
    : "—";
  document.querySelector("#detail-created-at").textContent = formatTime(job.created_at);
  document.querySelector("#detail-job-id").textContent = job.job_id;
  document.querySelector("#detail-started-at").textContent = formatTime(job.started_at);
  document.querySelector("#detail-finished-at").textContent = formatTime(job.finished_at);
  document.querySelector("#detail-package").textContent = job.package_manifest || "—";
  document.querySelector("#detail-run-mode").textContent = "复用或续跑";
  const normalized = displayStatus(job.status);
  const progress = Number.isFinite(job.progress_percent) ? job.progress_percent : 0;
  const activityText = job.retrying
    ? `正在修复 ${job.current_stage_label || "当前阶段"}${job.current_stage_error ? `：${job.current_stage_error}` : ""}`
    : job.activity || (normalized === "running" ? "正在准备报告流水线…" : "任务已结束");
  document.querySelector("#job-activity").textContent = activityText;
  document.querySelector("#job-activity").title = activityText;
  document.querySelector("#job-progress-value").textContent = `${progress}%`;
  document.querySelector("#job-progress-bar").style.width = `${Math.max(0, Math.min(100, progress))}%`;
  document.querySelector("#job-current-stage").textContent = job.current_stage_label
    ? `${job.current_stage_label} · ${job.completed_stage_count}/${job.total_stage_count}`
    : `${job.completed_stage_count || 0}/${job.total_stage_count || 0}`;
  document.querySelector("#job-stage-elapsed").textContent = formatElapsed(secondsSince(job.stage_started_at));
  document.querySelector("#job-last-event").textContent = formatRecency(job.last_event_at);
  document.querySelector("#job-token-usage").textContent = Number(job.token_usage_total || 0).toLocaleString("zh-CN");
  const heartbeatAge = secondsSince(job.heartbeat_at);
  const heartbeat = document.querySelector("#job-heartbeat");
  heartbeat.textContent = normalized !== "running"
    ? "任务已结束"
    : heartbeatAge === null
      ? "等待心跳"
      : heartbeatAge <= 6
        ? `正常 · ${formatRecency(job.heartbeat_at)}`
        : `延迟 · ${formatRecency(job.heartbeat_at)}`;
  heartbeat.classList.toggle("stale", normalized === "running" && heartbeatAge > 6);
  const pulse = document.querySelector("#activity-pulse");
  pulse.classList.toggle("running", normalized === "running" && heartbeatAge !== null && heartbeatAge <= 6);
  pulse.classList.toggle("stale", normalized === "running" && heartbeatAge > 6);
  jobLog.textContent = job.log || (normalized === "running" ? "正在准备报告流水线…" : "本次任务没有运行日志。");
  jobLog.scrollTop = jobLog.scrollHeight;
  document.querySelector(".log-panel summary span").textContent = normalized === "running" ? "实时更新" : "任务记录";
  const rawLogLink = document.querySelector("#raw-log-link");
  rawLogLink.hidden = !job.raw_log_available;
  rawLogLink.href = job.raw_log_available ? `/api/jobs/${job.job_id}/raw-log` : "";

  const reportUrl = job.result?.report_url;
  const markdownUrl = job.result?.markdown_url;
  if (normalized === "completed" && reportUrl?.startsWith("/outputs/") && markdownUrl?.startsWith("/outputs/")) {
    document.querySelector("#open-report").href = reportUrl;
    document.querySelector("#download-markdown").href = markdownUrl;
    resultActions.hidden = false;
  } else {
    resultActions.hidden = true;
  }
  showError(jobError, normalized === "failed" ? (job.error || "报告未能完成，请查看日志。") : "");
  cancelButton.hidden = normalized !== "running";
  cancelButton.disabled = Boolean(job.cancel_requested);
  cancelButton.textContent = job.cancel_requested ? "正在停止…" : "停止当前任务";
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法读取任务状态");
    if (jobId !== currentJobId) return job;
    renderJobDetails(job);
    if (displayStatus(job.status) !== "running") {
      stopJobPolling();
      await loadJobs();
    }
    return job;
  } catch (error) {
    if (jobId === currentJobId) {
      showError(jobError, error.message);
    }
    return null;
  }
}

async function openJob(jobId, { scroll = false } = {}) {
  currentJobId = jobId;
  stopJobPolling();
  renderJobList(jobHistory);
  emptyState.hidden = true;
  jobView.hidden = false;
  const job = await pollJob(jobId);
  if ((!job || displayStatus(job.status) === "running") && currentJobId === jobId) {
    pollTimer = setInterval(() => pollJob(jobId), 1000);
  }
  if (scroll) progressPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

cancelButton.addEventListener("click", async () => {
  if (!currentJobId) return;
  cancelButton.disabled = true;
  cancelButton.textContent = "正在停止…";
  try {
    const response = await fetch(`/api/jobs/${currentJobId}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "停止任务失败");
    await pollJob(currentJobId);
  } catch (error) {
    cancelButton.disabled = false;
    cancelButton.textContent = "停止当前任务";
    showError(jobError, error.message);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError(formError, "");
  showError(jobError, "");
  resultActions.hidden = true;
  const data = new FormData();
  data.append("report_type", reportType);
  data.append("engine", engine);
  data.append("model", modelInput.value.trim());
  data.append("reasoning_effort", effortInput.value);
  data.append(
    "codex_service_tier",
    engine === "codex" && fastModeInput.checked && !fastModeInput.disabled ? "fast" : "default",
  );

  if (!modelInput.value.trim()) {
    showError(formError, "请选择要调用的模型。");
    modelInput.focus();
    return;
  }
  if (reportType === "material" || importMode === "zip") {
    if (!archiveInput.files.length) {
      showError(formError, reportType === "material" ? "请选择素材 ZIP 文件。" : "请选择字幕包 ZIP 文件。");
      return;
    }
    data.append("package_archive", archiveInput.files[0], archiveInput.files[0].name);
  } else if (importMode === "folder") {
    if (!folderInput.files.length) {
      showError(formError, "请选择完整字幕目录。");
      return;
    }
    [...folderInput.files].forEach((file) => {
      data.append("package_file", file, file.webkitRelativePath || file.name);
    });
  } else {
    if (!pathInput.value.trim()) {
      showError(formError, "请输入本机字幕包路径。");
      return;
    }
    data.append("package_path", pathInput.value.trim());
  }

  submitting = true;
  const previousJobId = currentJobId;
  renderSubmittingState();
  syncSubmitButton();
  try {
    const response = await fetch("/api/jobs", { method: "POST", body: data });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "任务提交失败");
    submitting = false;
    hasActiveJob = true;
    currentJobId = job.job_id;
    await loadJobs();
    await openJob(job.job_id);
  } catch (error) {
    submitting = false;
    await loadJobs().catch(() => {});
    if (previousJobId && jobHistory.some((item) => item.job_id === previousJobId)) {
      await openJob(previousJobId).catch(() => {});
    } else if (!currentJobId) {
      renderEmptyJobState();
    }
    syncSubmitButton();
    showError(formError, error.message);
  }
});

refreshJobsButton.addEventListener("click", () => {
  loadJobs().catch((error) => showError(jobError, error.message));
});

selectReportType(reportType);
loadConfig();
loadJobs({ selectActive: true }).catch((error) => showError(jobError, error.message));
clearInterval(listTimer);
listTimer = setInterval(() => {
  loadJobs({ selectActive: true }).catch(() => {});
}, 2000);
