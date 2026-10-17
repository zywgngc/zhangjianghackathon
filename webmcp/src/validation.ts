import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { Ajv2020 } from 'ajv/dist/2020.js';
import { startRequestSchema, proposalSelectionSchema } from '../public/request-schema.mjs';
import type { ClarificationQuestion, DiscussionAnswer } from '../discussion.ts';
import type { MaterialSearchRequest, MaterialSearchResult } from '../material-portal.ts';
import type { StartRequest, ProposalSelection } from './model.ts';
import { AppError } from './model.ts';

const ajv = new Ajv2020({ strict: true, allErrors: true });
const addFormats = createRequire(import.meta.url)('ajv-formats') as (instance: Ajv2020) => void;
addFormats(ajv);
const schema = JSON.parse(
  readFileSync(new URL('../material-portal.schema.json', import.meta.url), 'utf8'),
);
ajv.addSchema(schema);
const validators = Object.fromEntries(
  ['MaterialSearchRequest', 'MaterialSearchResult', 'MainFollowUpRequest'].map((name) => [
    name,
    ajv.compile({ $ref: `${schema.$id}#/$defs/${name}` }),
  ]),
);
export function validateContract(name: keyof typeof validators, value: unknown): void {
  const check = validators[name];
  if (!check || !check(value))
    throw new AppError('INVALID_INPUT', `Invalid ${name}: ${ajv.errorsText(check?.errors)}`, 400);
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object')
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical((value as Record<string, unknown>)[k])}`)
      .join(',')}}`;
  return JSON.stringify(value);
}
export const hash = (value: unknown): string =>
  createHash('sha256').update(canonical(value)).digest('hex');
const startValidator = ajv.compile(startRequestSchema(schema));
const selectionValidator = ajv.compile(proposalSelectionSchema(schema));
export function validateSelection(value: unknown): asserts value is ProposalSelection {
  if (!selectionValidator(value))
    throw new AppError('INVALID_INPUT', ajv.errorsText(selectionValidator.errors), 400);
}
export function validateStart(value: unknown): asserts value is StartRequest {
  if (!startValidator(value))
    throw new AppError('INVALID_INPUT', ajv.errorsText(startValidator.errors), 400);
}
export function validateQuestions(questions: ClarificationQuestion[]): void {
  if (new Set(questions.map((q) => q.questionId)).size !== questions.length)
    throw new AppError('INVALID_PORTAL_RESULT', 'Duplicate question IDs.', 502);
  for (const q of questions) {
    const ids = q.options.map((o) => o.optionId);
    if (new Set(ids).size !== ids.length || (!q.required && !ids.includes(q.defaultOptionId)))
      throw new AppError('INVALID_PORTAL_RESULT', 'Invalid question options/default.', 502);
  }
}
export function validateAnswers(
  questions: ClarificationQuestion[],
  answers: DiscussionAnswer[],
): void {
  if (new Set(answers.map((a) => a.questionId)).size !== answers.length)
    throw new AppError('INVALID_INPUT', 'Duplicate answers.', 400);
  for (const answer of answers) {
    const question = questions.find((q) => q.questionId === answer.questionId);
    if (
      !question ||
      (answer.optionId !== null && !question.options.some((o) => o.optionId === answer.optionId))
    )
      throw new AppError('INVALID_INPUT', 'Unknown question or option.', 400);
    if (
      answer.source === 'system_default' &&
      (question.required || answer.optionId !== question.defaultOptionId)
    )
      throw new AppError('INVALID_INPUT', 'Invalid default provenance.', 400);
    if (!question.required && answer.optionId === null)
      throw new AppError(
        'INVALID_INPUT',
        'Optional questions require one of their defined choices.',
        400,
      );
  }
  if (questions.some((q) => q.required && !answers.some((a) => a.questionId === q.questionId)))
    throw new AppError('INVALID_INPUT', 'Required answers are missing.', 400);
}
export function validateResult(
  request: MaterialSearchRequest,
  value: unknown,
): asserts value is MaterialSearchResult {
  try {
    validateContract('MaterialSearchResult', value);
  } catch (error) {
    throw new AppError('INVALID_PORTAL_RESULT', (error as Error).message, 502);
  }
  const result = value as MaterialSearchResult;
  if (
    result.requestId !== request.requestId ||
    result.contractVersion !== request.contractVersion ||
    ['runId', 'threadId', 'round'].some(
      (k) =>
        result.discussion[k as keyof typeof result.discussion] !==
        request.discussion[k as keyof typeof result.discussion],
    )
  )
    throw new AppError('INVALID_PORTAL_RESULT', 'Response does not match its request.', 502);
  if ('candidates' in result) {
    if (
      result.candidates.length > request.limit ||
      new Set(result.candidates.map((c) => c.candidateId)).size !== result.candidates.length ||
      result.candidates.some((c) => !request.materialKinds.includes(c.kind))
    )
      throw new AppError(
        'INVALID_PORTAL_RESULT',
        'Invalid candidate count, IDs, or material types.',
        502,
      );
  }
  if (result.status === 'needs_clarification') validateQuestions(result.questions);
}
