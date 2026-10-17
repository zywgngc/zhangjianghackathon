/** One start payload schema for browser tools and server validation. */
export function startRequestSchema(contract) {
  return {
    type: 'object',
    additionalProperties: false,
    required: ['requestId', 'projectId', 'caseId'],
    properties: {
      requestId: { type: 'string', minLength: 1, maxLength: 200 },
      projectId: { type: 'string', minLength: 1, maxLength: 200 },
      caseId: { enum: ['GAME-TEST-001', 'GAME-TEST-002', 'GAME-TEST-003'] },
      brief: { type: 'string', maxLength: 8000 },
      images: { type: 'array', maxItems: 8, items: contract.$defs.ImageReference },
      preferences: {
        type: 'object',
        additionalProperties: false,
        properties: {
          implementationStyle: { enum: ['engine_native', 'portable'] },
          candidateCount: { type: 'integer', minimum: 1, maximum: 3 },
          engine: { enum: ['html', 'phaser', 'babylon'] },
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
