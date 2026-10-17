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
      '列出支持的参考案例和已有研究任务。',
      { type: 'object', properties: {}, additionalProperties: false },
      true,
      async () => ({ cases: await api('/api/cases'), runs: await api('/api/runs') }),
    ),
    tool(
      'start_research',
      '为参考案例创建可恢复的研究任务。本地演示使用模拟素材门户。',
      startRequestSchema(contract),
      false,
      (input) => api('/api/runs', 'POST', input),
    ),
    tool(
      'get_research_state',
      '读取任务状态、默认选择、待回答问题、历史记录和剩余追问额度。',
      runSchema,
      true,
      ({ runId }) => api(`/api/runs/${encodeURIComponent(runId)}`),
    ),
    tool(
      'follow_up_research',
      '细化研究或回答一批问题。消耗 3 次开发者追问额度中的 1 次；须使用当前修订和父请求。',
      { $defs: contract.$defs, $ref: '#/$defs/MainFollowUpRequest' },
      false,
      (input) =>
        api(`/api/runs/${encodeURIComponent(input.discussion.runId)}/follow-ups`, 'POST', input),
    ),
    tool(
      'get_implementation_blueprint',
      '读取最新蓝图，包括来源证据和未执行的验收要求。部分完成不代表已经可以生成游戏。',
      runSchema,
      true,
      ({ runId }) => api(`/api/runs/${encodeURIComponent(runId)}/blueprint`),
    ),
    tool(
      'cancel_research',
      '明确取消研究任务，并停止接收后续工作结果。',
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
