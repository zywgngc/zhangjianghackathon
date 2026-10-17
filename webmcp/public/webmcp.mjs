import { startRequestSchema, proposalSelectionSchema } from './request-schema.mjs';
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
      '列出已有研究任务与可选的测试案例。普通创意无需选择测试案例。',
      { type: 'object', properties: {}, additionalProperties: false },
      true,
      async () => ({ cases: await api('/api/cases'), runs: await api('/api/runs') }),
    ),
    tool(
      'start_research',
      '用图片引用或稀疏创意创建研究任务；省略 caseId。decisionMode=ask 先检索相似开源仓库再给选项，auto 自动采用默认。原型由 WebMCP 研究：默认只读本地缓存，显式开启有限联网后检索 GitHub 并静态读取少量源码；素材门户独立接入。当前使用规则提案，图片尚未识别，行为尚未验证。测试模式可显式使用模拟数据。',
      startRequestSchema(contract),
      false,
      (input) => api('/api/runs', 'POST', input),
    ),
    tool(
      'resolve_research_proposal',
      '提交第一轮提案选择，不消耗追问额度。answers=[] 采用全部当前默认；仅提交开发者实际修改的选项。后续调整使用 follow_up_research。',
      {
        type: 'object',
        properties: {
          runId: { type: 'string', minLength: 1 },
          selection: proposalSelectionSchema(contract),
        },
        required: ['runId', 'selection'],
        additionalProperties: false,
      },
      false,
      ({ runId, selection }) =>
        api(`/api/runs/${encodeURIComponent(runId)}/decisions`, 'POST', selection),
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
