/** One start payload schema for browser tools and server validation. */
export function startRequestSchema(contract) {
  return {
    type: 'object',
    additionalProperties: false,
    required: ['requestId', 'projectId'],
    oneOf: [
      { required: ['caseId'], properties: { caseId: {}, mode: { const: 'benchmark' } } },
      {
        not: { required: ['caseId'], properties: { caseId: {} } },
        properties: { mode: { const: 'idea' } },
        anyOf: [
          { required: ['brief'], properties: { brief: { type: 'string', pattern: '\\S' } } },
          { required: ['images'], properties: { images: { type: 'array', minItems: 1 } } },
          {
            required: ['knownFeatures'],
            properties: { knownFeatures: { type: 'array', minItems: 1 } },
          },
        ],
      },
    ],
    properties: {
      requestId: { type: 'string', minLength: 1, maxLength: 200 },
      projectId: { type: 'string', minLength: 1, maxLength: 200 },
      mode: { enum: ['idea', 'benchmark'] },
      decisionMode: { enum: ['ask', 'auto'] },
      caseId: { enum: ['GAME-TEST-001', 'GAME-TEST-002', 'GAME-TEST-003'] },
      brief: { type: 'string', maxLength: 8000 },
      knownFeatures: {
        type: 'array',
        maxItems: 20,
        items: { type: 'string', pattern: '\\S', maxLength: 500 },
      },
      images: { type: 'array', maxItems: 8, items: contract.$defs.ImageReference },
      preferences: {
        type: 'object',
        additionalProperties: false,
        properties: {
          implementationStyle: { enum: ['engine_native', 'portable'] },
          candidateCount: { type: 'integer', minimum: 1, maximum: 3 },
          engine: { enum: ['html', 'phaser', 'babylon'] },
          archetype: { enum: ['runner', 'survivor', 'puzzle', 'platformer'] },
          difficulty: { enum: ['easy', 'normal', 'hard'] },
          visualStyle: { enum: ['minimal', 'pixel', 'illustrated'] },
        },
      },
      limits: {
        type: 'object',
        additionalProperties: false,
        properties: {
          maxPortalAttempts: { type: 'integer', minimum: 1, maximum: 100 },
          maxRunMs: { type: 'integer', minimum: 100, maximum: 3600000 },
        },
      },
    },
  };
}

/** Initial proposal resolution is separate from the three later main follow-ups. */
export function proposalSelectionSchema(contract) {
  return {
    type: 'object',
    additionalProperties: false,
    required: ['requestId', 'expectedProjectRevision', 'clarificationId', 'answers'],
    properties: {
      requestId: { type: 'string', minLength: 1, maxLength: 200 },
      expectedProjectRevision: { type: 'integer', minimum: 1 },
      clarificationId: { type: 'string', minLength: 1 },
      answers: { type: 'array', maxItems: 20, items: contract.$defs.DiscussionAnswer },
    },
  };
}
