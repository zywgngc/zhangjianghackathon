import { registerResearchTools } from './webmcp.mjs';
import { label, sourceText, errorMessage, handoffReasons } from './ui-zh.mjs';
const $ = (id) => document.getElementById(id);
let current = null;
let busy = false;
let questionKey = '';
let proposalKey = '';
let repositoryKey = '';
const runTitle = (run) =>
  run.input.caseId ? label(run.input.caseId) : run.input.brief?.slice(0, 60) || '图片 / 特征创意';
function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}
function sourceLink(text, uri) {
  const link = node('a', text);
  try {
    const url = new URL(uri);
    if (
      url.protocol === 'https:' &&
      url.hostname === 'github.com' &&
      !url.username &&
      !url.password
    ) {
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
  } catch {}
  return link;
}
function renderRepositories(run) {
  const job = run.jobs
    .filter((j) => j.request.purpose === 'archetype' && j.request.projectRevision === run.revision)
    .at(-1);
  const key = `${run.id}:${run.revision}:${job?.state}:${job?.result?.retrievedAt}`;
  if (key === repositoryKey) return;
  repositoryKey = key;
  const root = $('repositories');
  root.replaceChildren(node('h3', '相似游戏仓库 · 原型检索'));
  const result = job?.result;
  if (!result) {
    root.append(
      node('p', job?.error || '正在检索仓库并读取源码，完成后显示可核查的证据。', 'muted hint'),
    );
    return;
  }
  if (result.status === 'failed') {
    root.append(node('p', `检索未完成：${result.error.message}`, 'notice'));
    root.append(node('p', '下面仍可使用规则默认提案；它不代表已经找到相似仓库。', 'muted hint'));
    return;
  }
  root.append(
    node(
      'p',
      `${result.isMock ? '模拟测试数据' : '真实检索结果'} · ${result.candidates.length} 个候选 · ${result.provider}`,
      'muted hint',
    ),
  );
  for (const candidate of result.candidates) {
    const card = node('article', '', 'repository');
    const title = node('h3', '');
    title.append(sourceLink(candidate.title, candidate.source.uri));
    card.append(title, node('p', candidate.summary, 'hint'));
    card.append(
      node(
        'p',
        `固定版本：${candidate.source.revision?.slice(0, 12) || '未知'} · 许可证标识：${candidate.license?.identifier || '未知'} · 玩法未验证`,
        'muted hint',
      ),
    );
    if (candidate.coverageHints.length)
      card.append(node('p', `匹配线索：${candidate.coverageHints.join('；')}`, 'hint'));
    const detail = document.createElement('details');
    detail.append(
      node('summary', `查看 ${candidate.source.paths.length} 个已读取文件、证据与限制`),
    );
    detail.append(node('p', `文件：${candidate.source.paths.join('、') || '尚无'}`, 'hint'));
    const evidence = document.createElement('ul');
    for (const item of candidate.evidence) {
      const li = node('li', '');
      li.append(sourceLink(item.description, item.sourceUri));
      evidence.append(li);
    }
    detail.append(evidence);
    for (const limitation of candidate.limitations)
      detail.append(node('p', limitation, 'muted hint'));
    card.append(detail);
    root.append(card);
  }
  const gaps = document.createElement('details');
  gaps.append(node('summary', '检索范围与候选缺口'));
  for (const item of result.limitations) gaps.append(node('p', item, 'muted hint'));
  root.append(gaps);
}
function renderProposal(run) {
  const key = `${run.id}:${run.revision}:${run.stage}:${run.proposal?.basis}:${run.mainUsed}`;
  if (key === proposalKey) return;
  proposalKey = key;
  const root = $('proposal');
  root.replaceChildren();
  if (!run.proposal) return;
  const initial = run.stage === 'decisions';
  root.append(
    node('h3', '你提供的信息'),
    node('p', run.proposal.known.join('；') || '仅提供了图片引用，尚无文字特征。'),
  );
  if (run.input.images?.length)
    root.append(
      node('p', `${run.input.images.length} 张参考图片已记录；内容尚未识别。`, 'muted hint'),
    );
  const warning = document.createElement('details');
  warning.append(node('summary', '提案依据与待确认内容'));
  for (const message of run.proposal.warnings) warning.append(node('p', message, 'muted hint'));
  root.append(warning);
  if (run.stage === 'archetypes') {
    root.append(node('p', '正在查询相似原型，随后给出补全选项。'));
    return;
  }
  const form = document.createElement('form');
  form.className = 'proposal-form';
  form.append(
    node('h3', initial ? '补全提案 · 只改你在意的选项' : '调整补全方案'),
    node(
      'p',
      initial
        ? '未改动的项保留原来的来源；采用默认不等于你逐项确认了需求。'
        : '批量修改后提交一次，消耗 1 次开发者追问额度。',
      'muted hint',
    ),
  );
  const fields = [];
  for (const q of run.decisionBatch.questions) {
    const d = run.decisions.find((d) => d.id === q.questionId);
    const field = document.createElement('select');
    field.id = `choice-${q.questionId}`;
    field.append(...q.options.map((o) => new Option(o.label, o.optionId)));
    field.value = d?.value ?? q.defaultOptionId;
    const title = node('label', q.prompt);
    title.htmlFor = field.id;
    const from = node('p', `当前来源：${label(d?.source ?? 'system_default')}`, 'muted hint');
    field.addEventListener('change', () => {
      from.textContent =
        field.value === d?.value ? `当前来源：${label(d?.source)}` : '本次修改：开发者选择';
    });
    form.append(title, field, from);
    fields.push({ q, field, value: field.value });
  }
  form.append(
    node(
      'p',
      '切换玩法原型后，该原型专属的机制会使用推荐默认值，可在下一版继续调整。',
      'muted hint',
    ),
  );
  const submit = node('button', initial ? '按以上方案继续研究' : '应用修改并重新研究');
  submit.disabled = ['cancelled', 'failed'].includes(run.status) || (!initial && run.mainUsed >= 3);
  form.append(submit);
  const defaults = node('button', '忽略修改，按原方案继续');
  defaults.type = 'button';
  defaults.className = 'secondary';
  defaults.disabled = submit.disabled;
  if (initial) form.append(defaults);
  async function apply(useCurrent) {
    if (busy) return;
    busy = true;
    submit.disabled = defaults.disabled = true;
    try {
      const answers = useCurrent
        ? []
        : fields
            .filter((f) => f.field.value !== f.value)
            .map((f) => ({
              questionId: f.q.questionId,
              optionId: f.field.value,
              text: null,
              source: 'developer_choice',
            }));
      if (initial)
        await api(`/api/runs/${run.id}/decisions`, 'POST', {
          requestId: crypto.randomUUID(),
          expectedProjectRevision: run.revision,
          clarificationId: run.decisionBatch.clarificationId,
          answers,
        });
      else {
        if (!answers.length) throw new Error('请先修改至少一个选项，再提交调整。');
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
              message: '请按本次选择更新机制和验收草案。',
              clarificationId: run.decisionBatch.clarificationId,
              answers,
            },
          },
        });
      }
      await refresh();
    } catch (e) {
      error(e);
      submit.disabled = defaults.disabled = false;
    } finally {
      busy = false;
    }
  }
  form.onsubmit = (event) => {
    event.preventDefault();
    apply(false);
  };
  defaults.onclick = () => apply(true);
  if (initial) root.append(form);
  else {
    const details = document.createElement('details');
    details.append(node('summary', '查看生效方案 / 调整选项'), form);
    root.append(details);
  }
}
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
    title.textContent =
      job.request.purpose === 'archetype' ? '原型研究需要补充信息' : '素材门户需要补充信息';
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
    button.disabled =
      run.mainUsed >= 3 ||
      (job.request.purpose === 'archetype' ? (run.archetypeUsed ?? 0) : run.materialUsed) >= 3 ||
      run.status === 'cancelled';
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
      (r) =>
        new Option(
          `${r.caseId ? label(r.caseId) : r.title} · ${label(r.status)} · ${r.id.slice(0, 8)}`,
          r.id,
        ),
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
    `${runTitle(run)} · ${label(run.status)} · ${run.stage === 'decisions' ? '等待第一轮选择' : label(run.stage)} · 第 ${run.revision} 版`;
  const option = [...$('runs').options].find((o) => o.value === run.id);
  if (option)
    option.textContent = `${runTitle(run)} · ${label(run.status)} · ${run.id.slice(0, 8)}`;
  $('counters').replaceChildren(
    ...[
      `开发者追问：${run.mainUsed}/3`,
      `素材门户追问：${run.materialUsed}/3`,
      `原型重查：${run.archetypeUsed ?? 0}/3`,
      `研究调用次数：${run.portalAttempts}/${run.maxPortalAttempts}`,
    ].map((text) => {
      const span = document.createElement('span');
      span.textContent = text;
      return span;
    }),
  );
  $('decisions').replaceChildren(
    ...run.decisions.map((d) => {
      const li = document.createElement('li');
      const q = run.decisionBatch.questions.find((q) => q.questionId === d.id);
      const value = run.proposal
        ? (q?.options.find((o) => o.optionId === d.value)?.label ?? label(d.value))
        : label(d.value);
      li.textContent = `${run.proposal ? d.rationale : label(d.id)}：${value} · ${label(d.source)}${d.locked ? ' · 已锁定' : ''}`;
      return li;
    }),
  );
  $('decisions').hidden = !!run.proposal;
  const last = run.blueprints.at(-1);
  const blueprint = last?.projectRevision === run.revision ? last : null;
  $('send').disabled =
    run.stage === 'decisions' || run.mainUsed >= 3 || ['cancelled', 'failed'].includes(run.status);
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
  renderRepositories(run);
  renderProposal(run);
  $('mechanics').replaceChildren(
    ...mechanics.map((m) => {
      const card = document.createElement('article');
      const title = document.createElement('h3');
      title.textContent = sourceText(m.title);
      const body = document.createElement('p');
      body.textContent = m.behavior.map(sourceText).join(' ');
      const detail = document.createElement('p');
      detail.textContent = `${m.checks.length} 项${run.proposal ? '派生验收草案' : '验收要求'} · ${blueprint?.candidates.filter((c) => c.mechanicId === m.id).length ?? 0} 个候选方案 · 行为尚未验证`;
      card.append(title, body, detail);
      const materialJob = run.jobs
        .filter(
          (j) =>
            j.request.purpose === 'mechanic' &&
            j.request.mechanicId === m.id &&
            j.request.projectRevision === run.revision,
        )
        .at(-1);
      if (materialJob?.result?.status === 'failed')
        card.append(node('p', `候选缺口：${materialJob.result.error.message}`, 'notice'));
      if (run.proposal) {
        const acceptance = document.createElement('details');
        acceptance.append(node('summary', '查看验收草案及来源'));
        const list = document.createElement('ul');
        for (const c of (blueprint?.acceptance ?? run.plan.acceptanceDraft ?? []).filter((c) =>
          m.checks.includes(c.id),
        )) {
          const sources = (c.derivedFrom ?? []).map((id) => {
            const d = run.decisions.find((d) => d.id === id);
            return `${label(d?.source)}（${run.decisionBatch.questions.find((q) => q.questionId === id)?.options.find((o) => o.optionId === d?.value)?.label ?? id}）`;
          });
          list.append(
            node('li', `${c.requirement} 系统派生草案；依据：${sources.join('、')}。尚未执行。`),
          );
        }
        acceptance.append(list);
        card.append(acceptance);
      }
      return card;
    }),
  );
}
async function refresh() {
  const id = $('runs').value;
  if (id) return render(await api(`/api/runs/${encodeURIComponent(id)}`));
  current = null;
  questionKey = '';
  proposalKey = '';
  repositoryKey = '';
  $('status').textContent = '请选择历史任务，或开始新的研究。';
  $('state').textContent = '尚未选择任务。';
  $('handoff-status').textContent = '';
  for (const name of [
    'counters',
    'decisions',
    'mechanics',
    'clarifications',
    'handoff-blockers',
    'proposal',
    'repositories',
  ])
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
      mode: $('mode').value,
      brief: $('brief').value,
    };
    if (input.mode === 'benchmark') input.caseId = $('case').value;
    else {
      input.knownFeatures = $('features')
        .value.split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      input.decisionMode = $('decision-mode').value;
      if ($('image-uri').value.trim())
        input.images = [
          {
            uri: $('image-uri').value.trim(),
            mimeType: $('image-type').value,
            description: $('image-description').value.trim() || '参考图片（未提供文字描述）',
          },
        ];
      if (!input.brief.trim() && !input.knownFeatures.length && !input.images?.length)
        throw new Error('请提供一句创意、一项核心特征或一个图片引用，即可开始。');
    }
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
    a.download = `${data.caseId ?? 'idea'}-blueprint-v${data.version}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error(e);
  }
};
$('runs').onchange = () => refresh().catch(error);
$('mode').onchange = () => {
  $('benchmark-fields').hidden = $('mode').value !== 'benchmark';
  $('idea-fields').hidden = $('mode').value !== 'idea';
};
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
  const providers = await api('/api/providers');
  $('provider-status').textContent = [
    providers.archetypes === 'github-archetype-research'
      ? '原型：WebMCP 直接检索 GitHub，并读取固定版本的源码。'
      : `原型 provider：${providers.archetypes}（测试配置请核对结果中的模拟标识）。`,
    providers.materials === 'material-portal-not-configured'
      ? '素材门户：尚未接入，机制候选会明确标记缺口。'
      : `素材门户：${providers.materials}。`,
    '提案仍使用规则模板；尚无图片理解，源码未执行，玩法与验收均待验证。',
  ].join(' ');
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
