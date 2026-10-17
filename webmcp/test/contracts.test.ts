import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import {
  validateContract,
  validateResult,
  validateQuestions,
  validateAnswers,
} from '../src/validation.ts';
import { MockMaterialPortal } from '../src/portal.ts';
import type { MaterialSearchRequest } from '../material-portal.ts';
import { hasCode, setup } from './helpers.ts';
const root = new URL('../examples/', import.meta.url);
for (const name of readdirSync(root).filter((n) => n.endsWith('.json')))
  test(`contract fixture ${name}`, () => {
    const payload = JSON.parse(readFileSync(new URL(name, root), 'utf8'));
    validateContract(
      name === 'main-follow-up.json'
        ? 'MainFollowUpRequest'
        : payload.purpose
          ? 'MaterialSearchRequest'
          : 'MaterialSearchResult',
      payload,
    );
  });
test('invalid rounds, missing context, and malformed answers are rejected', () => {
  const request = JSON.parse(readFileSync(new URL('portal-follow-up-request.json', root), 'utf8'));
  for (const round of [-1, 1.5, 4]) {
    const copy = structuredClone(request);
    copy.discussion.round = round;
    assert.throws(() => validateContract('MaterialSearchRequest', copy), hasCode('INVALID_INPUT'));
  }
  const copy = structuredClone(request);
  delete copy.discussion;
  assert.throws(() => validateContract('MaterialSearchRequest', copy), hasCode('INVALID_INPUT'));
  request.discussion.followUp.answers[0].text = 'both forms';
  assert.throws(() => validateContract('MaterialSearchRequest', request), hasCode('INVALID_INPUT'));
});
test('question defaults, duplicate IDs, and required replies are checked semantically', () => {
  const q = {
    questionId: 'x',
    prompt: 'Choose',
    options: [
      { optionId: 'a', label: 'A' },
      { optionId: 'b', label: 'B' },
    ],
    required: false as const,
    defaultOptionId: 'missing',
  };
  assert.throws(() => validateQuestions([q]), hasCode('INVALID_PORTAL_RESULT'));
  q.defaultOptionId = 'a';
  assert.throws(() => validateQuestions([q, q]), hasCode('INVALID_PORTAL_RESULT'));
  assert.throws(
    () =>
      validateAnswers(
        [q],
        [{ questionId: 'x', optionId: 'b', text: null, source: 'system_default' }],
      ),
    hasCode('INVALID_INPUT'),
  );
  assert.throws(
    () => validateAnswers([{ ...q, required: true, defaultOptionId: null }], []),
    hasCode('INVALID_INPUT'),
  );
});
test('result validation enforces request candidate limit, types, uniqueness and round', async () => {
  const { engine, store, id } = setup();
  try {
    const req = engine.get(id).jobs[0].request as MaterialSearchRequest;
    const result = await new MockMaterialPortal().search(req);
    if (!('candidates' in result)) throw new Error('Fixture result expected.');
    for (const mutate of [
      (r: any) => r.candidates.push(r.candidates[0]),
      (r: any) => (r.candidates[1].candidateId = r.candidates[0].candidateId),
      (r: any) => (r.candidates[0].kind = 'source_code'),
      (r: any) => (r.discussion.round = 1),
    ]) {
      const changed = structuredClone(result);
      mutate(changed);
      assert.throws(() => validateResult(req, changed), hasCode('INVALID_PORTAL_RESULT'));
    }
  } finally {
    store.close();
  }
});
