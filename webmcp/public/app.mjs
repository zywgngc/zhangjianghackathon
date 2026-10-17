import { registerResearchTools } from './webmcp.mjs';
import { label, sourceText, errorMessage, handoffReasons } from './ui-zh.mjs';
const $ = (id) => document.getElementById(id);
let current = null;
let busy = false;
let questionKey = '';
async function api(path, method = 'GET', body) {
  const prefix = document.body.dataset.apiBase ?? '/api';
  const tokenStore = document.body.dataset.authStore;
  let token = '';
  if (tokenStore) {
    try {
      token = sessionStorage.getItem(tokenStore) ?? '';
    } catch {}
  }
  path = prefix + path.slice('/api'.length);
  const response = await fetch(path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(
      data.error?.message ?? JSON.stringify(data.detail ?? response.status),
    );
    failure.code = data.error?.code;
    failure.status = response.status;
    throw failure;
  }
  return data;
}
function error(err) {
  $('error').textContent = errorMessage(err);
  $('error-original').textContent = [err.code, err.message].filter(Boolean).join(': ');
  $('error-details').hidden = false;
}
function renderQuestions(run) {
  const latest = new Map();
  for (const job of run.jobs)
    if (job.request.projectRevision === run.revision)
      latest.set(job.request.discussion.threadId, job);
  const pending = [...latest.values()].filter(
    (j) => j.result?.status === 'needs_clarification' && j.result.questions.some((q) => q.required),
  );
  const key = `${run.id}:${run.revision}:${run.mainUsed}:${run.materialUsed}:${pending.map((j) => j.result.clarificationId).join(',')}`;
  if (key === questionKey) return;
  questionKey = key;
  $('clarifications').replaceChildren();
  for (const job of pending) {
    const result = job.result;
    const form = document.createElement('form');
    const title = document.createElement('h3');
    title.textContent = '素材门户需要补充信息';
    form.append(title);
    const fields = [];
    for (const q of result.questions) {
      const label = document.createElement('label');
      label.textContent = q.prompt;
      let field;
      if (q.options.length) {
        field = document.createElement('select');
        field.append(
          new Option(
            q.required
              ? '请选择一个答案'
              : `使用默认值：${q.options.find((o) => o.optionId === q.defaultOptionId)?.label}`,
            '',
          ),
          ...q.options.map((o) => new Option(o.label, o.optionId)),
        );
        field.required = q.required;
      } else {
        field = document.createElement('textarea');
        field.rows = 2;
        field.required = q.required;
      }
      label.append(field);
      form.append(label);
      fields.push({ q, field });
    }
    const button = document.createElement('button');
    button.textContent = '提交回答';
    button.disabled = run.mainUsed >= 3 || run.materialUsed >= 3 || run.status === 'cancelled';
    form.append(button);
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (busy) return;
      busy = true;
      try {
        const r = current;
        const answers = fields.map(({ q, field }) =>
          q.options.length
            ? {
                questionId: q.questionId,
                optionId: field.value || q.defaultOptionId,
                text: null,
                source: field.value ? 'developer_choice' : 'system_default',
              }
            : {
                questionId: q.questionId,
                optionId: null,
                text: field.value,
                source: 'developer_choice',
              },
        );
        await api(`/api/runs/${r.id}/follow-ups`, 'POST', {
          contractVersion: '0.2',
          requestId: crypto.randomUUID(),
          projectId: r.projectId,
          expectedProjectRevision: r.revision,
          discussion: {
            runId: r.id,
            threadId: r.mainThreadId,
            round: r.mainUsed + 1,
            inReplyToRequestId: r.lastMainRequestId,
            followUp: {
              message: '请使用这些选择回答待处理的素材门户问题。',
              clarificationId: result.clarificationId,
              answers,
            },
          },
        });
        await refresh();
      } catch (e) {
        error(e);
      } finally {
        busy = false;
      }
    };
    $('clarifications').append(form);
  }
}
async function list() {
  const selected = $('runs').value;
  const runs = await api('/api/runs');
  $('runs').replaceChildren(
    new Option('选择一个任务', ''),
    ...runs.map(
      (r) => new Option(`${label(r.caseId)} · ${label(r.status)} · ${r.id.slice(0, 8)}`, r.id),
    ),
  );
  $('runs').value = selected;
}
function render(run) {
  if (current?.id !== run.id || current?.revision !== run.revision) {
    $('handoff-status').textContent = '';
    $('handoff-blockers').replaceChildren();
  }
  current = run;
  $('status').textContent =
    `${label(run.input.caseId)} · ${label(run.status)} · ${label(run.stage)} · 第 ${run.revision} 版`;
  const option = [...$('runs').options].find((o) => o.value === run.id);
  if (option)
    option.textContent = `${label(run.input.caseId)} · ${label(run.status)} · ${run.id.slice(0, 8)}`;
  $('counters').replaceChildren(
    ...[
      `开发者追问：${run.mainUsed}/3`,
      `素材门户追问：${run.materialUsed}/3`,
      `门户请求次数：${run.portalAttempts}/${run.maxPortalAttempts}`,
    ].map((text) => {
      const span = document.createElement('span');
      span.textContent = text;
      return span;
    }),
  );
  $('decisions').replaceChildren(
    ...run.decisions.map((d) => {
      const li = document.createElement('li');
      li.textContent = `${label(d.id)}：${label(d.value)} · ${label(d.source)}${d.locked ? ' · 已锁定' : ''}`;
      return li;
    }),
  );
  const last = run.blueprints.at(-1);
  const blueprint = last?.projectRevision === run.revision ? last : null;
  $('send').disabled = run.mainUsed >= 3 || ['cancelled', 'failed'].includes(run.status);
  $('cancel').disabled = run.status === 'cancelled';
  $('follow-hint').textContent =
    run.mainUsed >= 3
      ? '本任务的 3 次开发者追问已用完，已有蓝图仍可查看和导出。'
      : `还可追问 ${3 - run.mainUsed} 次；素材门户的追问额度独立计算，首轮请求不计入。`;
  $('export').disabled = !blueprint;
  $('handoff').disabled = !blueprint;
  $('state').textContent = JSON.stringify(run, null, 2);
  const mechanics = run.plan?.mechanics ?? [];
  renderQuestions(run);
  $('mechanics').replaceChildren(
    ...mechanics.map((m) => {
      const card = document.createElement('article');
      const title = document.createElement('h3');
      title.textContent = sourceText(m.title);
      const body = document.createElement('p');
      body.textContent = m.behavior.map(sourceText).join(' ');
      const detail = document.createElement('p');
      detail.textContent = `${m.checks.length} 项验收要求 · ${blueprint?.candidates.filter((c) => c.mechanicId === m.id).length ?? 0} 个候选方案 · 行为尚未验证`;
      card.append(title, body, detail);
      return card;
    }),
  );
}
async function refresh() {
  const id = $('runs').value;
  if (id) return render(await api(`/api/runs/${encodeURIComponent(id)}`));
  current = null;
  questionKey = '';
  $('status').textContent = '请选择历史任务，或开始新的研究。';
  $('state').textContent = '尚未选择任务。';
  $('handoff-status').textContent = '';
  for (const name of ['counters', 'decisions', 'mechanics', 'clarifications', 'handoff-blockers'])
    $(name).replaceChildren();
  for (const name of ['send', 'export', 'cancel', 'handoff']) $(name).disabled = true;
}
$('start').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  $('error').textContent = '';
  $('error-details').hidden = true;
  try {
    const input = {
      requestId: crypto.randomUUID(),
      projectId: crypto.randomUUID(),
      caseId: $('case').value,
      brief: $('brief').value,
    };
    if ($('style').value) input.preferences = { implementationStyle: $('style').value };
    const result = await api('/api/runs', 'POST', input);
    await list();
    $('runs').value = result.runId;
    await refresh();
  } catch (e) {
    error(e);
  } finally {
    busy = false;
  }
});
$('follow').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!current || busy) return;
  busy = true;
  try {
    const run = current;
    await api(`/api/runs/${run.id}/follow-ups`, 'POST', {
      contractVersion: '0.2',
      requestId: crypto.randomUUID(),
      projectId: run.projectId,
      expectedProjectRevision: run.revision,
      discussion: {
        runId: run.id,
        threadId: run.mainThreadId,
        round: run.mainUsed + 1,
        inReplyToRequestId: run.lastMainRequestId,
        followUp: {
          message: $('note').value,
          clarificationId: $('preference').value ? run.decisionBatch.clarificationId : null,
          answers: $('preference').value
            ? [
                {
                  questionId: 'implementationStyle',
                  optionId: $('preference').value,
                  text: null,
                  source: 'developer_choice',
                },
              ]
            : [],
        },
      },
    });
    $('note').value = '';
    $('preference').value = '';
    await refresh();
  } catch (e) {
    error(e);
  } finally {
    busy = false;
  }
});
$('cancel').onclick = async () => {
  if (current)
    try {
      await api(`/api/runs/${current.id}/cancel`, 'POST', {});
      await refresh();
    } catch (e) {
      error(e);
    }
};
$('export').onclick = async () => {
  if (!current) return;
  try {
    const data = await api(`/api/runs/${current.id}/blueprint`);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = `${data.caseId}-blueprint-v${data.version}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error(e);
  }
};
$('runs').onchange = () => refresh().catch(error);
$('refresh').onclick = () => refresh().catch(error);
$('handoff').hidden = document.body.dataset.handoff !== 'true';
$('handoff').onclick = async () => {
  if (!current) return;
  try {
    const run = current;
    const result = await api(`/api/runs/${run.id}/handoff`, 'POST', {
      runId: run.id,
      projectRevision: run.revision,
      blueprintVersion: run.blueprints.at(-1).version,
    });
    $('handoff-status').textContent = result.eligible
      ? '已满足单文件小游戏的交接条件，可将此版本的蓝图交给生成端。本次检查未启动游戏生成。'
      : '暂不满足生成交接条件。完整项目的蓝图接收尚未接入；本次检查未启动游戏生成。';
    $('handoff-blockers').replaceChildren(
      ...handoffReasons(result.blockers).map((reason) => {
        const li = document.createElement('li');
        li.textContent = reason;
        return li;
      }),
    );
  } catch (e) {
    error(e);
  }
};
try {
  const cases = await api('/api/cases');
  $('case').replaceChildren(
    ...cases.map(
      (c) =>
        new Option(`${c.title} · ${label(c.engine)} · ${c.checks.length} 项验收要求`, c.caseId),
    ),
  );
  await list();
  const saved = localStorage.getItem('webmcp-last-run');
  if (saved && [...$('runs').options].some((o) => o.value === saved)) {
    $('runs').value = saved;
    await refresh();
  }
  const registration = await registerResearchTools(document, api, await api('/api/contract'));
  $('webmcp').textContent = registration.supported
    ? '浏览器 WebMCP 工具已就绪，可由助手直接发起研究、追问和读取蓝图。'
    : '此浏览器暂不支持原生 WebMCP；仍可使用页面按钮完成相同操作。';
  addEventListener('pagehide', () => registration.dispose(), { once: true });
} catch (e) {
  error(e);
  $('webmcp').textContent = '浏览器工具初始化未完成，请检查服务连接或访问设置。';
}
setInterval(() => {
  if (current && !busy) {
    localStorage.setItem('webmcp-last-run', current.id);
    if (['queued', 'running', 'waiting_input'].includes(current.status)) refresh().catch(error);
  }
}, 750);
