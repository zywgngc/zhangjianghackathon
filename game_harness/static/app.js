const $ = (id) => document.getElementById(id);
const labels = {
  queued: "排队中",
  running: "生成中",
  passed: "验证通过",
  failed: "运行失败",
  cancelled: "已取消",
  needs_attention: "需要检查",
};
const stageNames = {
  brief: "需求改写",
  retrieval: "知识检索",
  design: "游戏设计",
  build: "代码生成",
  repair: "缺陷修复",
  browser_test: "浏览器实测",
  complete: "完成",
  queued: "排队",
  project_planning: "完整项目规划",
  project_verification: "逐项验收",
  project_repair: "集成缺陷修复",
  project_dependency_repair: "模块接口修复",
  project_visual_review: "独立画面审查",
};
let activeId = null,
  activeRun = null,
  cursor = 0,
  previewKey = "",
  polling = false,
  projectDetails = null,
  previewAttemptAt = 0;
let token = "";
const screenshotUrls = new Map();
const projectDocuments = new Map();
try {
  token = sessionStorage.getItem("harness-token") || "";
} catch {}
$("token").value = token;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let msg = `请求失败（${response.status}）`;
    try {
      const d = await response.json();
      msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    } catch {}
    const error = new Error(msg);
    error.status = response.status;
    throw error;
  }
  return response;
}
const json = async (path, opts) => (await api(path, opts)).json();
function notice(text = "") {
  $("notice").textContent = text;
}
function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function paragraph(parent, title, content) {
  parent.append(node("h3", title));
  if (Array.isArray(content)) {
    const ul = node("ul");
    content.forEach((s) => ul.append(node("li", String(s))));
    parent.append(ul);
  } else parent.append(node("p", String(content)));
}
const SAVE_LIMIT = 1024 * 1024;
function validGameSave(values) {
  if (!values || typeof values !== "object" || Array.isArray(values))
    return false;
  const entries = Object.entries(values);
  if (
    entries.length > 2048 ||
    entries.some(
      ([key, value]) => key.length > 4096 || typeof value !== "string",
    )
  )
    return false;
  return (
    new TextEncoder().encode(JSON.stringify(values)).byteLength <= SAVE_LIMIT
  );
}
function gameSaveFor(rid) {
  try {
    const values = JSON.parse(localStorage.getItem(`game-save:${rid}`) || "{}");
    return validGameSave(values) ? values : {};
  } catch {
    return {};
  }
}
// This function is serialized into the opaque iframe. It deliberately has no
// closure over the parent's access token, sessionStorage, or other run saves.
function installGameSave(runId, initial) {
  let values = Object.assign(Object.create(null), initial);
  const has = (key) => Object.prototype.hasOwnProperty.call(values, key);
  function commit(next) {
    if (
      new TextEncoder().encode(JSON.stringify(next)).byteLength > 1024 * 1024 ||
      Object.keys(next).length > 2048
    ) {
      throw new DOMException("游戏存档超过 1 MB", "QuotaExceededError");
    }
    values = next;
    window.parent.postMessage(
      { type: "harness-game-save", runId, values: { ...values } },
      "*",
    );
  }
  const facade = Object.freeze({
    getItem(key) {
      key = String(key);
      return has(key) ? values[key] : null;
    },
    setItem(key, value) {
      key = String(key);
      if (key.length > 4096)
        throw new DOMException("存档键过长", "QuotaExceededError");
      const next = Object.assign(Object.create(null), values);
      next[key] = String(value);
      commit(next);
    },
    removeItem(key) {
      const next = Object.assign(Object.create(null), values);
      delete next[String(key)];
      commit(next);
    },
    clear() {
      commit(Object.create(null));
    },
    key(index) {
      return Object.keys(values)[Number(index)] ?? null;
    },
    get length() {
      return Object.keys(values).length;
    },
  });
  Object.defineProperty(window, "localStorage", {
    value: facade,
    writable: false,
    configurable: false,
  });
}
window.addEventListener("message", (event) => {
  if (event.source !== $("game").contentWindow || !activeId || $("game").hidden)
    return;
  const message = event.data;
  if (
    !message ||
    message.type !== "harness-game-save" ||
    message.runId !== activeId ||
    !validGameSave(message.values)
  )
    return;
  try {
    localStorage.setItem(
      `game-save:${activeId}`,
      JSON.stringify(message.values),
    );
  } catch {}
});
function showPreview(html, csp, rid, key) {
  const meta = document.createElement("meta");
  meta.httpEquiv = "Content-Security-Policy";
  meta.content = (csp || "").replace(/sandbox\s+allow-scripts;?/i, "");
  const parameters = JSON.stringify([rid, gameSaveFor(rid)])
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
  const shim = `<script>(${installGameSave.toString()})(...${parameters});<\/script>`;
  html = html.replace(
    /<head(?:\s[^>]*)?>/i,
    (head) => head + (meta.content ? meta.outerHTML : "") + shim,
  );
  $("game").srcdoc = html;
  $("game").hidden = false;
  $("empty").hidden = true;
  previewKey = key;
}
async function downloadResponse(response, filename) {
  const url = URL.createObjectURL(await response.blob());
  const link = node("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function artifactButton(parent, rid, name, label) {
  const button = node("button", label);
  button.type = "button";
  button.onclick = async () => {
    try {
      await downloadResponse(
        await api(`/api/runs/${rid}/artifacts/${encodeURIComponent(name)}`),
        name,
      );
    } catch (error) {
      notice(error.message);
    }
  };
  parent.append(button);
}
async function loadConfig() {
  try {
    const c = await json("/api/config");
    $("model").textContent = c.model;
  } catch (e) {
    $("model").textContent = "连接待配置";
    notice(e.message);
  }
}
async function loadHistory() {
  const runs = await json("/api/runs");
  $("history").replaceChildren();
  for (const r of runs) {
    const b = node("button");
    b.type = "button";
    b.classList.toggle("active", r.id === activeId);
    b.append(
      node(
        "span",
        `${r.request.mode === "project" ? "项目 · " : "小游戏 · "}${r.request.description}`,
      ),
      node("small", labels[r.status] || r.status),
    );
    b.onclick = () => selectRun(r.id);
    $("history").append(b);
  }
}
async function selectRun(id) {
  for (const url of screenshotUrls.values()) URL.revokeObjectURL(url);
  screenshotUrls.clear();
  projectDocuments.clear();
  activeId = id;
  history.replaceState(null, "", "#run=" + id);
  cursor = 0;
  previewKey = "";
  activeRun = null;
  projectDetails = null;
  previewAttemptAt = 0;
  $("logs").replaceChildren();
  $("spec").textContent = "正在读取需求…";
  $("qa").textContent = "正在读取验证记录…";
  $("game").hidden = true;
  $("game").srcdoc = "";
  $("empty").hidden = false;
  $("preview-disclaimer").hidden = true;
  notice();
  await refreshRun();
  await loadHistory();
}
function updatePipeline(run) {
  const project = run.request.mode === "project";
  const captions = project
    ? ["完整规格", "架构规划", "功能 DAG", "增量实现", "逐项验收"]
    : ["需求改写", "知识检索", "游戏设计", "开发修复", "实测与 QA"];
  document.querySelectorAll("#pipeline b").forEach((element, index) => {
    element.textContent = captions[index];
  });
  let stage = run.stage;
  if (stage.endsWith(".review")) stage = "browser_test";
  if (stage === "repair") stage = "build";
  const order = ["brief", "retrieval", "design", "build", "browser_test"];
  let idx = project
    ? stage === "project_planning"
      ? 1
      : stage.startsWith("feature:")
        ? 3
        : stage === "project_repair"
          ? 3
          : stage === "project_verification"
            ? 4
            : 0
    : order.indexOf(stage);
  if (run.status === "passed") idx = 5;
  document.querySelectorAll("#pipeline [data-stage]").forEach((el, i) => {
    el.classList.toggle("current", i === idx && run.status === "running");
    el.classList.toggle("done", i < idx);
  });
}
async function artifact(name, rid = activeId) {
  return (
    await api(`/api/runs/${rid}/artifacts/${encodeURIComponent(name)}`)
  ).json();
}
async function renderSpec(run) {
  const rid = run.id,
    frag = document.createDocumentFragment();
  if (run.artifacts.includes("brief.json")) {
    const b = await artifact("brief.json", rid);
    paragraph(frag, b.title, b.rewritten_prompt);
    paragraph(frag, "用户明确要求", b.user_requirements);
    if (b.system_requirements?.length)
      paragraph(frag, "系统默认要求", b.system_requirements);
    paragraph(frag, "补充假设", b.assumptions);
    paragraph(frag, "验收标准", b.acceptance_criteria);
  }
  if (run.artifacts.includes("design.json")) {
    const d = await artifact("design.json", rid);
    paragraph(frag, `引擎：${d.engine.toUpperCase()}`, d.engine_reason);
    paragraph(frag, "玩法规则", d.mechanics);
    paragraph(frag, "操作方式", d.controls);
    paragraph(frag, "胜利／失败", `${d.win_condition} / ${d.lose_condition}`);
    paragraph(
      frag,
      "视觉与屏幕适配",
      `${d.visual_style}；${d.responsive_strategy}`,
    );
  }
  if (run.artifacts.includes("retrieval.json")) {
    const r = await artifact("retrieval.json", rid);
    paragraph(
      frag,
      "检索证据",
      r.documents.map((d) => `${d.title} · ${d.source} · ${d.id}`),
    );
    if (r.warnings.length) paragraph(frag, "检索提示", r.warnings);
  }
  if (activeId !== rid) return;
  if (frag.childNodes.length) $("spec").replaceChildren(frag);
  else $("spec").textContent = "需求代理正在准备内容。";
}
async function renderQA(run) {
  const rid = run.id,
    frag = document.createDocumentFragment();
  const reports = run.artifacts
    .filter((n) => /^v\d+\.report\.json$/.test(n))
    .sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)));
  for (const name of reports) {
    const r = await artifact(name, rid);
    paragraph(
      frag,
      `候选版本 ${parseInt(name.slice(1))} · ${r.passed ? "浏览器通过" : "浏览器未通过"}`,
      r.scope,
    );
    if (r.static_errors.length)
      paragraph(frag, "静态检查问题", r.static_errors);
    if (r.browser_errors?.length)
      paragraph(frag, "浏览器检查问题", r.browser_errors);
    if (r.infrastructure_error)
      paragraph(frag, "环境问题", r.infrastructure_error);
    for (const v of r.viewports)
      paragraph(
        frag,
        `${v.name} ${v.width}×${v.height} · ${v.passed ? "通过" : "未通过"}`,
        v.passed ? v.checks : v.errors,
      );
    const gallery = node("div", undefined, "screenshots");
    for (const v of r.viewports.filter((x) => x.screenshot)) {
      const key = `${rid}/${v.screenshot}`;
      if (!screenshotUrls.has(key)) {
        const response = await api(
          `/api/runs/${rid}/artifacts/${v.screenshot}`,
        );
        screenshotUrls.set(key, URL.createObjectURL(await response.blob()));
      }
      const figure = node("figure");
      const img = node("img");
      img.src = screenshotUrls.get(key);
      img.alt = `${v.name} 实际浏览器截图`;
      figure.append(img, node("figcaption", `${v.width} × ${v.height}`));
      gallery.append(figure);
    }
    frag.append(gallery);
    const reviewName = name.replace(".report.", ".review.");
    if (run.artifacts.includes(reviewName)) {
      const q = await artifact(reviewName, rid);
      paragraph(frag, "QA 结论", q.summary);
      if (q.defects.length)
        paragraph(
          frag,
          "发现的缺陷",
          q.defects.map(
            (d) => `${d.severity}：${d.evidence}；修复建议：${d.fix}`,
          ),
        );
      paragraph(frag, "验证范围与限制", q.limitations);
    }
  }
  if (activeId !== rid) return;
  if (frag.childNodes.length) $("qa").replaceChildren(frag);
  else $("qa").textContent = "代码生成后会进行三种屏幕尺寸的浏览器实测与 QA。";
}
const featureLabels = {
  pending: "等待依赖",
  running: "开发中",
  implemented: "已实现 · 待验收",
  verified: "功能检查通过",
  needs_work: "待修复",
  blocked: "受阻",
};
const acceptanceLabels = {
  passed: "通过",
  failed: "失败",
  not_run: "未运行",
  manual_required: "待人工复核",
  visual_required: "待视觉验证",
  incomplete: "未完成",
};
async function projectDocument(run, name) {
  if (!run.artifacts.includes(name)) return null;
  const key = `${run.id}/${name}`;
  if (!projectDocuments.has(key))
    projectDocuments.set(key, await artifact(name, run.id));
  return projectDocuments.get(key);
}
async function renderProject(run, details) {
  const rid = run.id;
  const [specification, implementation] = await Promise.all([
    projectDocument(run, "specification.json"),
    projectDocument(run, "implementation-plan.json"),
  ]);
  if (rid !== activeId) return;
  const frag = document.createDocumentFragment();
  const original = specification?.text ?? run.request.specification ?? "";
  const characters = original
    ? Array.from(original).length
    : run.request.specification_characters || 0;
  paragraph(
    frag,
    "完整规格原文",
    `${characters.toLocaleString()} 字符 · ${specification?.chunks?.length ?? "待归档"} 个分片 · ${specification?.requirements?.length ?? "待提取"} 项需求 · ${specification?.acceptance_tests?.length ?? "待提取"} 项正式验收`,
  );
  paragraph(
    frag,
    "锁定技术栈",
    `React + TypeScript + Vite + ${run.request.engine === "babylon" ? "Babylon.js" : "Phaser"}；原始规格 ${run.request.case_id || "自定义"}`,
  );
  if (specification?.sha256 || run.request.specification_sha256)
    paragraph(
      frag,
      "原文 SHA-256",
      specification?.sha256 || run.request.specification_sha256,
    );
  const actions = node("div", undefined, "report-actions");
  const sourceButton = node("button", "下载完整原文 .txt");
  sourceButton.type = "button";
  sourceButton.disabled = !original;
  if (!original) sourceButton.textContent = "完整原文归档中";
  sourceButton.onclick = () =>
    downloadResponse(
      new Response(original, {
        headers: { "Content-Type": "text/plain;charset=utf-8" },
      }),
      `${run.request.case_id || rid}-完整规格.txt`,
    );
  actions.append(sourceButton);
  for (const [name, label] of [
    ["specification.json", "原文与分片索引"],
    ["implementation-plan.json", "实现计划 JSON"],
    ["project-progress.json", "功能进度 JSON"],
  ]) {
    if (run.artifacts.includes(name)) artifactButton(actions, rid, name, label);
  }
  frag.append(actions);
  if (specification?.warnings?.length)
    paragraph(frag, "原文提示", specification.warnings);
  const plan = implementation?.plan;
  if (plan?.architecture) paragraph(frag, "项目架构", plan.architecture);
  if (plan?.interfaces) {
    const contract = node("details");
    contract.append(
      node("summary", "模块接口契约"),
      node("pre", plan.interfaces),
    );
    frag.append(contract);
  }
  paragraph(
    frag,
    "功能 DAG",
    "功能实现状态与整体验收独立记录。每个节点展示负责人、依赖与对应验收项。",
  );
  const records = details.features || [];
  const definitions =
    plan?.features || records.map((record) => record.definition || record);
  const list = node("ol", undefined, "feature-list");
  for (const feature of definitions) {
    const record = records.find(
      (entry) => (entry.feature_id || entry.id) === feature.id,
    );
    const status = record?.status || "pending";
    const item = node("li", undefined, "feature-card");
    const heading = node("div", undefined, "feature-heading");
    heading.append(
      node("strong", `${feature.id} · ${feature.title}`),
      node("span", featureLabels[status] || status, `feature-state ${status}`),
    );
    item.append(
      heading,
      node(
        "p",
        `负责人：${feature.owner || record?.owner || "待分配"} · 依赖：${feature.depends_on?.join("、") || "无"}`,
      ),
    );
    if (feature.objective) item.append(node("p", feature.objective));
    item.append(
      node("p", `文件：${feature.owned_paths?.join("、") || "待分配"}`),
      node(
        "p",
        `验收：${feature.acceptance_ids?.join("、") || "基础设施节点"}`,
      ),
    );
    if (record?.result?.message || record?.result?.summary)
      item.append(node("p", record.result.message || record.result.summary));
    list.append(item);
  }
  if (definitions.length) frag.append(list);
  else
    frag.append(
      node("p", "规划代理正在建立带依赖、文件归属和验收映射的功能计划。"),
    );
  if (specification?.chunks?.length) {
    const chunks = node("details");
    chunks.append(
      node("summary", `无损原文分片索引（${specification.chunks.length} 个）`),
    );
    const ul = node("ul");
    for (const chunk of specification.chunks)
      ul.append(
        node(
          "li",
          `${chunk.id} · ${Array.from(chunk.text).length.toLocaleString()} 字符 · 原文第 ${chunk.source?.line_start}–${chunk.source?.line_end} 行`,
        ),
      );
    chunks.append(ul);
    frag.append(chunks);
  }
  $("spec").replaceChildren(frag);

  const qa = document.createDocumentFragment();
  const validation = details.validation;
  const acceptance =
    validation?.acceptance ||
    validation?.evaluation ||
    validation?.evidence ||
    validation;
  const reported = acceptance?.tests || validation?.tests || [];
  const tests =
    specification?.acceptance_tests?.map((test) => ({
      ...test,
      ...(reported.find(
        (result) => (result.id || result.requirement_id) === test.id,
      ) || { status: "not_run" }),
    })) || reported;
  const counts = acceptance?.counts || {
    passed: tests.filter((test) => test.status === "passed").length,
    failed: tests.filter((test) => test.status === "failed").length,
    not_run: tests.filter((test) => test.status === "not_run").length,
    manual_required: tests.filter((test) => test.status === "manual_required")
      .length,
    visual_required: tests.filter((test) => test.status === "visual_required")
      .length,
  };
  paragraph(
    qa,
    run.status === "passed" ? "项目正式验收通过" : "项目尚未全部验收",
    "构建成功与功能已实现不代表测试通过。失败、未运行和待人工复核会分别保留。",
  );
  const summary = node("div", undefined, "report-summary");
  for (const state of ["passed", "failed", "not_run", "visual_required", "manual_required"])
    summary.append(
      node("span", `${acceptanceLabels[state]} ${counts[state] || 0}`, state),
    );
  qa.append(summary);
  const downloads = node("div", undefined, "report-actions");
  for (const name of run.artifacts.filter(
    (name) =>
      name === "project-validation.json" ||
      /^project-build-\d+\.json$/.test(name),
  ))
    artifactButton(
      downloads,
      rid,
      name,
      name === "project-validation.json"
        ? "下载完整验收报告"
        : `下载构建报告 ${name.match(/\d+/)[0]}`,
    );
  qa.append(downloads);
  if (validation?.errors?.length)
    paragraph(qa, "构建或运行问题", validation.errors);
  if (tests.length) {
    const table = node("table", undefined, "acceptance-table");
    const header = node("tr");
    ["验收项", "状态", "失败与缺失证据"].forEach((label) =>
      header.append(node("th", label)),
    );
    const thead = node("thead");
    thead.append(header);
    table.append(thead);
    const body = node("tbody");
    for (const test of tests) {
      const row = node("tr");
      const problems = [...(test.errors || []), ...(test.missing || [])];
      row.append(
        node(
          "td",
          `${test.id || test.requirement_id}${test.title || test.name ? " · " + (test.title || test.name) : ""}`,
        ),
        node(
          "td",
          acceptanceLabels[test.status] || test.status || "未运行",
          test.status || "not_run",
        ),
        node(
          "td",
          problems
            .map((problem) =>
              typeof problem === "string" ? problem : JSON.stringify(problem),
            )
            .join("；") ||
            (test.status === "passed"
              ? "已有执行证据，详见报告"
              : "尚无完整执行证据"),
        ),
      );
      body.append(row);
    }
    table.append(body);
    qa.append(table);
  } else
    qa.append(
      node("p", "原文验收项归档后会逐项列出。当前尚未执行，不能视为通过。"),
    );
  $("qa").replaceChildren(qa);
}
async function refreshRun() {
  if (!activeId || polling) return;
  polling = true;
  const rid = activeId;
  try {
    const run = await json(`/api/runs/${rid}`);
    if (rid !== activeId) return;
    activeRun = run;
    const isProject = run.request.mode === "project";
    if (isProject) {
      projectDetails = await json(`/api/projects/${rid}`);
      if (rid !== activeId) return;
    }
    notice(run.error || "");
    $("run-title").textContent =
      run.request.description.length > 30
        ? run.request.description.slice(0, 30) + "…"
        : run.request.description;
    $("status").textContent = labels[run.status] || run.status;
    $("status").classList.toggle(
      "error",
      ["failed", "needs_attention"].includes(run.status),
    );
    const duration = Math.max(
      0,
      Math.floor(
        ((["queued", "running"].includes(run.status)
          ? Date.now() / 1000
          : run.updated) -
          run.created) /
          60,
      ),
    );
    $("usage").textContent =
      `${stageNames[run.stage] || (run.stage.endsWith(".review") ? "QA 审查" : run.stage)} · ${run.calls} 次调用 · ${run.tokens.toLocaleString()} tokens · ${duration} 分钟`;
    $("cancel").hidden = !["queued", "running"].includes(run.status);
    $("resume").hidden = run.status !== "failed" &&
      !(isProject && ["needs_attention", "cancelled"].includes(run.status));
    $("download").hidden = !isProject && run.status !== "passed";
    $("download").textContent = isProject
      ? run.status === "passed"
        ? "下载正式项目 ZIP ↓"
        : "下载候选项目 ZIP ↓"
      : "下载游戏 ↓";
    $("empty-description").textContent = isProject
      ? "等待首次可预览构建。功能与验收进度可在其他标签查看。"
      : "通过浏览器验证与 QA 的版本，会在这里出现。";
    $("preview-disclaimer").hidden = !isProject;
    $("preview-disclaimer").classList.toggle(
      "verified",
      isProject && run.status === "passed",
    );
    $("preview-disclaimer").textContent =
      run.status === "passed"
        ? "正式项目：整体验收已通过，可下载同一项目及验收证据。"
        : "候选项目 · 尚未全部验收。此处仅表示已有可构建版本；功能实现、逐项测试与最终交付仍分别验证。";
    updatePipeline(run);
    if (run.error) notice(run.error);
    const events = await json(`/api/runs/${rid}/events?after=${cursor}`);
    if (rid !== activeId) return;
    for (const e of events) {
      const li = node("li");
      const stamp = node(
        "time",
        new Date(e.time * 1000).toLocaleTimeString("zh-CN", { hour12: false }),
      );
      li.append(
        stamp,
        node("b", stageNames[e.stage] || e.stage),
        node("span", e.kind),
      );
      const payload = { ...e.payload };
      delete payload.message;
      li.append(
        node(
          "p",
          (e.payload.message ? e.payload.message + " " : "") +
            (Object.keys(payload).length ? JSON.stringify(payload) : ""),
        ),
      );
      $("logs").append(li);
      cursor = e.id;
    }
    if (isProject) {
      await renderProject(run, projectDetails);
    } else if (events.length || !$("spec").children.length) {
      await renderSpec(run);
      await renderQA(run);
    }
    if (isProject && Date.now() - previewAttemptAt > 4000) {
      previewAttemptAt = Date.now();
      try {
        const response = await api(`/api/projects/${rid}/candidate`);
        const html = await response.text();
        if (rid !== activeId) return;
        const key = `${rid}/${response.headers.get("X-Harness-Source-SHA256") || html}`;
        if (key !== previewKey)
          showPreview(
            html,
            response.headers.get("Content-Security-Policy"),
            rid,
            key,
          );
      } catch (error) {
        if (error.status !== 409) throw error;
        if (previewKey)
          $("preview-disclaimer").textContent =
            "显示上一次可构建的候选 · 当前源码尚无完整构建，整体验收仍未完成。";
      }
    } else if (
      !isProject &&
      run.status === "passed" &&
      previewKey !== `${rid}/${run.approved_version}`
    ) {
      const response = await api(
        `/api/runs/${rid}/preview/${run.approved_version}`,
      );
      const html = await response.text();
      if (rid !== activeId) return;
      showPreview(
        html,
        response.headers.get("Content-Security-Policy"),
        rid,
        `${rid}/${run.approved_version}`,
      );
    }
  } catch (e) {
    notice(e.message);
  } finally {
    polling = false;
  }
}
$("generate-form").onsubmit = async (e) => {
  e.preventDefault();
  const maximum = $("workflow-mode").value === "project" ? 1000000 : 8000;
  if ($("description").value.length > maximum) {
    notice(
      `当前模式最多 ${maximum.toLocaleString()} 字符；完整长规格请选择多文件项目模式。`,
    );
    return;
  }
  const b = $("generate");
  b.disabled = true;
  notice();
  try {
    const project = $("workflow-mode").value === "project";
    const r = await json(project ? "/api/projects" : "/api/runs", {
      method: "POST",
      body: JSON.stringify({
        ...(project
          ? { specification: $("description").value }
          : { description: $("description").value.trim() }),
        engine: $("engine").value,
      }),
    });
    await selectRun(r.id);
  } catch (e) {
    notice(e.message);
  } finally {
    b.disabled = false;
  }
};
$("cancel").onclick = async () => {
  try {
    await json(`/api/runs/${activeId}/cancel`, { method: "POST" });
    await refreshRun();
    await loadHistory();
  } catch (e) {
    notice(e.message);
  }
};
$("resume").onclick = async () => {
  try {
    await json(`/api/runs/${activeId}/resume`, { method: "POST" });
    notice();
    await refreshRun();
  } catch (e) {
    notice(e.message);
  }
};
$("download").onclick = async () => {
  try {
    if (activeRun?.request.mode === "project") {
      await downloadResponse(
        await api(`/api/projects/${activeId}/archive`),
        `游造-${activeRun.status === "passed" ? "正式" : "候选"}-项目.zip`,
      );
      return;
    }
    const r = await api(
      `/api/runs/${activeId}/artifacts/v${activeRun.approved_version}.html`,
    );
    await downloadResponse(r, "游造-游戏.html");
  } catch (e) {
    notice(e.message);
  }
};
$("save-token").onclick = async () => {
  token = $("token").value.trim();
  try {
    sessionStorage.setItem("harness-token", token);
  } catch {}
  notice();
  await loadConfig();
  try {
    await loadHistory();
    const requested = location.hash.match(/^#run=([a-f0-9]{32})$/);
    if (requested) await selectRun(requested[1]);
  } catch (e) {
    notice(e.message);
  }
};
$("refresh").onclick = () => loadHistory().catch((e) => notice(e.message));
document.querySelectorAll("[data-tab]").forEach(
  (b) =>
    (b.onclick = () => {
      document.querySelectorAll("[data-tab]").forEach((x) => {
        x.classList.toggle("selected", x === b);
        $("panel-" + x.dataset.tab).hidden = x !== b;
      });
    }),
);
function updateInputCount() {
  $("input-count").textContent =
    `${$("description").value.length.toLocaleString()} / ${Number($("description").maxLength).toLocaleString()} 字符`;
}
function updateWorkflowMode() {
  const project = $("workflow-mode").value === "project";
  $("description").maxLength = project ? 1000000 : 8000;
  $("description-label").textContent = project
    ? "完整游戏规格"
    : "你想做什么游戏？";
  $("description").placeholder = project
    ? "粘贴完整需求、数值配置、关卡和验收标准。保留全部原文，系统按章节分解功能与验证任务。"
    : "例如：做一个太空接星星游戏，左右移动飞船接星星，躲避陨石，支持手机触屏。";
  $("benchmark-cases").hidden = !project;
  $("single-examples").hidden = project;
  $("mode-caption").textContent = project
    ? "完整原文 · 功能 DAG · 逐项验收 · 项目 ZIP"
    : "简体中文 · 手机适配 · 单文件离线运行";
  $("engine").replaceChildren();
  const choices = project
    ? [
        ["auto", "按规格选择并锁定"],
        ["phaser", "Phaser · 2D 项目"],
        ["babylon", "Babylon.js · 3D 项目"],
      ]
    : [
        ["auto", "由设计代理选择"],
        ["canvas", "Canvas · 动作游戏"],
        ["dom", "DOM · 棋盘与卡牌"],
      ];
  for (const [value, label] of choices) {
    const option = node("option", label);
    option.value = value;
    $("engine").append(option);
  }
  updateInputCount();
}
$("workflow-mode").onchange = updateWorkflowMode;
$("description").oninput = updateInputCount;
$("spec-file").onchange = async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 4 * 1000000)
      throw new Error("规格文件超过容量上限；支持最多 1,000,000 字符。");
    const text = new TextDecoder("utf-8", {
      fatal: true,
      ignoreBOM: true,
    }).decode(await file.arrayBuffer());
    if (text.length > 1000000)
      throw new Error(
        "规格超过 1,000,000 字符，请拆分为独立项目；内容未截断。",
      );
    $("workflow-mode").value = "project";
    updateWorkflowMode();
    $("description").value = text;
    updateInputCount();
    notice(
      `已完整导入 ${file.name}，共 ${text.length.toLocaleString()} 字符。`,
    );
  } catch (error) {
    notice(error.message);
  } finally {
    event.target.value = "";
  }
};
document.querySelectorAll("[data-case]").forEach((button) => {
  button.onclick = async () => {
    document.querySelectorAll("[data-case]").forEach((element) => {
      element.disabled = true;
    });
    notice();
    try {
      const run = await json(`/api/benchmarks/${button.dataset.case}/runs`, {
        method: "POST",
      });
      await selectRun(run.id);
    } catch (error) {
      notice(error.message);
    } finally {
      document.querySelectorAll("[data-case]").forEach((element) => {
        element.disabled = false;
      });
    }
  };
});
updateWorkflowMode();
const examples = {
  star: "做一个精美的太空接星星游戏：左右移动飞船接星星，每颗10分，接到陨石扣一条命，初始3条命，100分胜利。支持手机触屏和键盘，中文界面。",
  memory:
    "做一个森林主题的记忆翻牌游戏，4×4共8对动物卡片，90秒内找出全部配对获胜，时间用尽失败，支持暂停和重新开始，手机也能玩。",
  snake:
    "做一个霓虹风格贪吃蛇游戏，吃到食物加10分并变长，达到100分获胜，撞墙或撞自己失败。支持键盘方向键和手机方向按钮。",
};
document.querySelectorAll("[data-example]").forEach(
  (b) =>
    (b.onclick = () => {
      $("description").value = examples[b.dataset.example];
      updateInputCount();
      $("description").focus();
    }),
);
(async () => {
  await loadConfig();
  try {
    await loadHistory();
    const requested = location.hash.match(/^#run=([a-f0-9]{32})$/);
    if (requested) await selectRun(requested[1]);
  } catch (e) {
    notice(e.message);
  }
})();
setInterval(refreshRun, 1500);
setInterval(() => loadHistory().catch(() => {}), 7000);
