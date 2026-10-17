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
};
let activeId = null,
  activeRun = null,
  cursor = 0,
  previewKey = "",
  polling = false;
let token = "";
const screenshotUrls = new Map();
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
    throw new Error(msg);
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
      node("span", r.request.description),
      node("small", labels[r.status] || r.status),
    );
    b.onclick = () => selectRun(r.id);
    $("history").append(b);
  }
}
async function selectRun(id) {
  for (const url of screenshotUrls.values()) URL.revokeObjectURL(url);
  screenshotUrls.clear();
  activeId = id;
  cursor = 0;
  previewKey = "";
  activeRun = null;
  $("logs").replaceChildren();
  $("spec").textContent = "正在读取需求…";
  $("qa").textContent = "正在读取验证记录…";
  $("game").hidden = true;
  $("game").srcdoc = "";
  $("empty").hidden = false;
  notice();
  await refreshRun();
  await loadHistory();
}
function updatePipeline(run) {
  let stage = run.stage;
  if (stage.endsWith(".review")) stage = "browser_test";
  if (stage === "repair") stage = "build";
  const order = ["brief", "retrieval", "design", "build", "browser_test"];
  let idx = order.indexOf(stage);
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
async function refreshRun() {
  if (!activeId || polling) return;
  polling = true;
  const rid = activeId;
  try {
    const run = await json(`/api/runs/${rid}`);
    if (rid !== activeId) return;
    activeRun = run;
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
    $("resume").hidden = run.status !== "failed";
    $("download").hidden = run.status !== "passed";
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
    if (events.length || !$("spec").children.length) {
      await renderSpec(run);
      await renderQA(run);
    }
    if (
      run.status === "passed" &&
      previewKey !== `${rid}/${run.approved_version}`
    ) {
      const response = await api(
        `/api/runs/${rid}/preview/${run.approved_version}`,
      );
      let html = await response.text();
      if (rid !== activeId) return;
      const csp = response.headers
        .get("Content-Security-Policy")
        .replace(/sandbox allow-scripts;?/, "");
      const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
      html = html.replace(/<head(?:\s[^>]*)?>/i, (m) => m + meta);
      $("game").srcdoc = html;
      $("game").hidden = false;
      $("empty").hidden = true;
      previewKey = `${rid}/${run.approved_version}`;
    }
  } catch (e) {
    notice(e.message);
  } finally {
    polling = false;
  }
}
$("generate-form").onsubmit = async (e) => {
  e.preventDefault();
  const b = $("generate");
  b.disabled = true;
  notice();
  try {
    const r = await json("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        description: $("description").value.trim(),
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
    const r = await api(
      `/api/runs/${activeId}/artifacts/v${activeRun.approved_version}.html`,
    );
    const url = URL.createObjectURL(await r.blob());
    const a = node("a");
    a.href = url;
    a.download = "游造-游戏.html";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
      $("description").focus();
    }),
);
(async () => {
  await loadConfig();
  try {
    await loadHistory();
  } catch (e) {
    notice(e.message);
  }
})();
setInterval(refreshRun, 1500);
setInterval(() => loadHistory().catch(() => {}), 7000);
