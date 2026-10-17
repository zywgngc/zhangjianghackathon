import { startRequestSchema } from './request-schema.mjs';
const runSchema = {
  type: 'object',
  properties: { runId: { type: 'string', minLength: 1 } },
  required: ['runId'],
  additionalProperties: false,
};
export function researchTools(api, contract) {
  const tool = (name, description, inputSchema, readOnly, execute) => ({
    name,
    description,
    inputSchema,
    annotations: { readOnlyHint: readOnly, untrustedContentHint: true },
    execute: async (input) => ({
      content: [{ type: 'text', text: JSON.stringify(await execute(input)) }],
    }),
  });
  return [
    tool(
      'get_research_context',
      'List supported reference cases and existing research runs.',
      { type: 'object', properties: {}, additionalProperties: false },
      true,
      async () => ({ cases: await api('/api/cases'), runs: await api('/api/runs') }),
    ),
    tool(
      'start_research',
      'Start a durable research run for a reference case. Uses a synthetic portal in the local demo.',
      startRequestSchema(contract),
      false,
      (input) => api('/api/runs', 'POST', input),
    ),
    tool(
      'get_research_state',
      'Read run state, defaults, questions, history, and remaining follow-up allowances.',
      runSchema,
      true,
      ({ runId }) => api(`/api/runs/${encodeURIComponent(runId)}`),
    ),
    tool(
      'follow_up_research',
      'Refine research or answer a question batch. Consumes one of three main follow-ups; use the current revision and parent request.',
      { $defs: contract.$defs, $ref: '#/$defs/MainFollowUpRequest' },
      false,
      (input) =>
        api(`/api/runs/${encodeURIComponent(input.discussion.runId)}/follow-ups`, 'POST', input),
    ),
    tool(
      'get_implementation_blueprint',
      'Read the latest blueprint with source evidence and unexecuted acceptance checks. Partial does not mean ready to build.',
      runSchema,
      true,
      ({ runId }) => api(`/api/runs/${encodeURIComponent(runId)}/blueprint`),
    ),
    tool(
      'cancel_research',
      'Explicitly cancel a research run and stop accepting worker results.',
      runSchema,
      false,
      ({ runId }) => api(`/api/runs/${encodeURIComponent(runId)}/cancel`, 'POST', {}),
    ),
  ];
}
export async function registerResearchTools(document, api, contract) {
  if (typeof document.modelContext?.registerTool !== 'function')
    return { supported: false, dispose() {} };
  const controller = new AbortController();
  try {
    for (const tool of researchTools(api, contract))
      await document.modelContext.registerTool(tool, { signal: controller.signal });
  } catch (error) {
    controller.abort();
    throw error;
  }
  return {
    supported: true,
    dispose() {
      controller.abort();
    },
  };
}
