import { registerResearchTools } from './webmcp.mjs';
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
  if (!response.ok)
    throw new Error(
      data.error
        ? `${data.error.code}: ${data.error.message}`
        : String(data.detail ?? `Request failed: ${response.status}`),
    );
  return data;
}
function error(err) {
  $('error').textContent = err.message;
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
    title.textContent = 'Material portal clarification';
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
              ? 'Choose an answer'
              : `Use default: ${q.options.find((o) => o.optionId === q.defaultOptionId)?.label}`,
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
    button.textContent = 'Submit answers';
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
              message: 'Answer the pending material portal clarification using these selections.',
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
    new Option('Select a run', ''),
    ...runs.map((r) => new Option(`${r.caseId} · ${r.status} · ${r.id.slice(0, 8)}`, r.id)),
  );
  $('runs').value = selected;
}
function render(run) {
  if (current?.id !== run.id || current?.revision !== run.revision)
    $('handoff-status').textContent = '';
  current = run;
  $('status').textContent =
    `${run.input.caseId} · ${run.status} · ${run.stage} · revision ${run.revision}`;
  $('counters').replaceChildren(
    ...[
      `Main follow-ups: ${run.mainUsed}/3`,
      `Material follow-ups: ${run.materialUsed}/3`,
      `Portal attempts: ${run.portalAttempts}/${run.maxPortalAttempts}`,
    ].map((text) => {
      const span = document.createElement('span');
      span.textContent = text;
      return span;
    }),
  );
  $('decisions').replaceChildren(
    ...run.decisions.map((d) => {
      const li = document.createElement('li');
      li.textContent = `${d.id}: ${d.value} · ${d.source}${d.locked ? ' · locked' : ''}`;
      return li;
    }),
  );
  const last = run.blueprints.at(-1);
  const blueprint = last?.projectRevision === run.revision ? last : null;
  $('send').disabled = run.mainUsed >= 3 || ['cancelled', 'failed'].includes(run.status);
  $('export').disabled = !blueprint;
  $('handoff').disabled = !blueprint;
  $('state').textContent = JSON.stringify(run, null, 2);
  const mechanics = run.plan?.mechanics ?? [];
  renderQuestions(run);
  $('mechanics').replaceChildren(
    ...mechanics.map((m) => {
      const card = document.createElement('article');
      const title = document.createElement('h3');
      title.textContent = m.title;
      const body = document.createElement('p');
      body.textContent = m.behavior.join(' ');
      const detail = document.createElement('p');
      detail.textContent = `${m.checks.length} acceptance checks · ${blueprint?.candidates.filter((c) => c.mechanicId === m.id).length ?? 0} discovered alternatives · behavior unverified`;
      card.append(title, body, detail);
      return card;
    }),
  );
}
async function refresh() {
  const id = $('runs').value;
  if (id) render(await api(`/api/runs/${encodeURIComponent(id)}`));
}
$('start').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  $('error').textContent = '';
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
      ? 'Compatible with Builder. The pinned reference can be supplied as research in POST /api/runs; this check does not start generation.'
      : `Builder handoff blocked: ${result.blockers.join('; ')}. No generation was started.`;
  } catch (e) {
    error(e);
  }
};
try {
  const cases = await api('/api/cases');
  $('case').replaceChildren(
    ...cases.map(
      (c) => new Option(`${c.title} · ${c.engine} · ${c.checks.length} checks`, c.caseId),
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
    ? 'Browser WebMCP tools registered.'
    : 'Native WebMCP is unavailable in this browser. The studio uses the same API directly.';
  addEventListener('pagehide', () => registration.dispose(), { once: true });
} catch (e) {
  error(e);
  $('webmcp').textContent =
    'Browser tool registration could not complete; the API and studio controls remain available.';
}
setInterval(() => {
  if (current && !busy) {
    localStorage.setItem('webmcp-last-run', current.id);
    if (['queued', 'running', 'waiting_input'].includes(current.status)) refresh().catch(error);
  }
}, 750);
