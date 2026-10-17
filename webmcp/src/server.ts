import { createServer } from 'node:http';
import type { IncomingMessage } from 'node:http';
import { readFileSync } from 'node:fs';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { benchmarks } from './agents.ts';
import { AppError } from './model.ts';
import { ResearchOrchestrator } from './orchestrator.ts';
import { MockMaterialPortal, UnconfiguredMaterialPortal } from './portal.ts';
import { GitHubArchetypeResearcher } from './github-archetypes.ts';
import { GitHubNetworkStore } from './github-network.ts';
import { RunStore } from './store.ts';

const assets: Record<string, [string, string]> = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/app.mjs': ['app.mjs', 'text/javascript; charset=utf-8'],
  '/ui-zh.mjs': ['ui-zh.mjs', 'text/javascript; charset=utf-8'],
  '/webmcp.mjs': ['webmcp.mjs', 'text/javascript; charset=utf-8'],
  '/request-schema.mjs': ['request-schema.mjs', 'text/javascript; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8'],
};
async function body(req: IncomingMessage): Promise<any> {
  if (!req.headers['content-type']?.startsWith('application/json'))
    throw new AppError('INVALID_INPUT', 'Content-Type must be application/json.', 415);
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 65536) throw new AppError('INVALID_INPUT', 'Request exceeds 64 KiB.', 413);
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new AppError('INVALID_INPUT', 'Malformed JSON.', 400);
  }
}
function equal(a: string, b: string): boolean {
  const x = Buffer.from(a),
    y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
export async function createResearchServer(options: {
  engine: ResearchOrchestrator;
  port?: number;
  token?: string;
  worker?: boolean;
}) {
  if (options.token !== undefined && options.token.length < 16)
    throw new Error('WEBMCP_TOKEN must contain at least 16 characters when configured.');
  const engine = options.engine;
  const token = options.token ?? randomBytes(32).toString('hex');
  let origin = '';
  let stopped = false;
  let active: Promise<unknown> | null = null;
  const server = createServer(async (req, res) => {
    const json = (status: number, data: unknown) => {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(JSON.stringify(data));
    };
    try {
      const host = req.headers.host ?? '';
      const expected = new URL(origin).host;
      if (host !== expected) throw new AppError('FORBIDDEN', 'Unrecognized Host header.', 403);
      if (req.headers.origin && req.headers.origin !== origin)
        throw new AppError('FORBIDDEN', 'Cross-origin requests are not accepted.', 403);
      const url = new URL(req.url ?? '/', origin);
      const path = url.pathname;
      if (req.method === 'GET' && assets[path]) {
        if (req.headers['sec-fetch-site'] === 'cross-site')
          throw new AppError('FORBIDDEN', 'Open the local application directly.', 403);
        const [file, type] = assets[path];
        res.writeHead(200, {
          'Content-Type': type,
          'Cache-Control': 'no-store',
          'X-Content-Type-Options': 'nosniff',
          'Content-Security-Policy':
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
          ...(path === '/'
            ? { 'Set-Cookie': `webmcp_session=${token}; HttpOnly; SameSite=Strict; Path=/` }
            : {}),
        });
        res.end(readFileSync(new URL(`../public/${file}`, import.meta.url)));
        return;
      }
      if (req.method === 'GET' && path === '/health') {
        json(200, { ok: true, provider: engine.portal.provider });
        return;
      }
      const bearer = req.headers.authorization?.replace(/^Bearer /, '') ?? '';
      const cookie =
        req.headers.cookie
          ?.split(';')
          .map((s) => s.trim())
          .find((s) => s.startsWith('webmcp_session='))
          ?.slice('webmcp_session='.length) ?? '';
      if (!equal(bearer, token) && !equal(cookie, token))
        throw new AppError('UNAUTHORIZED', 'A local session or bearer token is required.', 401);
      if (req.method === 'GET' && path === '/api/cases') {
        json(200, benchmarks);
        return;
      }
      if (req.method === 'GET' && path === '/api/providers') {
        json(200, {
          archetypes: engine.archetypes.provider,
          materials: engine.portal.provider,
          ...(engine.archetypes.networkStatus ? { github: engine.archetypes.networkStatus() } : {}),
        });
        return;
      }
      if (req.method === 'GET' && path === '/api/contract') {
        json(
          200,
          JSON.parse(
            readFileSync(new URL('../material-portal.schema.json', import.meta.url), 'utf8'),
          ),
        );
        return;
      }
      if (req.method === 'GET' && path === '/api/runs') {
        json(
          200,
          engine.store.list().map((r) => ({
            id: r.id,
            caseId: r.input.caseId,
            title: r.input.brief?.slice(0, 60) || (r.input.caseId ? undefined : '图片 / 特征创意'),
            revision: r.revision,
            status: r.status,
            createdAt: r.createdAt,
          })),
        );
        return;
      }
      if (req.method === 'POST' && path === '/api/runs') {
        const run = engine.start(await body(req));
        json(202, { runId: run.id, projectRevision: run.revision, status: run.status });
        return;
      }
      const match =
        /^\/api\/runs\/([^/]+)(?:\/(blueprint|decisions|follow-ups|material-follow-ups|cancel))?$/.exec(
          path,
        );
      if (match) {
        const id = decodeURIComponent(match[1]);
        const action = match[2];
        if (req.method === 'GET' && !action) {
          json(200, {
            ...engine.get(id),
            ...(engine.archetypes.networkStatus
              ? { githubNetwork: engine.archetypes.networkStatus(id) }
              : {}),
          });
          return;
        }
        if (req.method === 'GET' && action === 'blueprint') {
          const run = engine.get(id);
          const blueprint = run.blueprints.at(-1);
          if (!blueprint || blueprint.projectRevision !== run.revision)
            throw new AppError(
              'NOT_READY',
              'No reviewed blueprint exists for the current revision yet.',
            );
          json(200, blueprint);
          return;
        }
        if (req.method === 'POST' && action === 'follow-ups') {
          const command = await body(req);
          if (command?.discussion?.runId !== id)
            throw new AppError('INVALID_INPUT', 'Route and command run IDs differ.', 400);
          json(202, engine.mainFollowUp(command));
          return;
        }
        if (req.method === 'POST' && action === 'decisions') {
          json(202, engine.selectProposal(id, await body(req)));
          return;
        }
        if (req.method === 'POST' && action === 'material-follow-ups') {
          const job = engine.materialFollowUp(id, await body(req));
          json(202, {
            runId: id,
            requestId: job.request.requestId,
            round: job.request.discussion.round,
            status: job.state,
          });
          return;
        }
        if (req.method === 'POST' && action === 'cancel') {
          await body(req);
          const run = engine.cancel(id);
          json(200, { runId: run.id, status: run.status });
          return;
        }
      }
      throw new AppError('NOT_FOUND', 'Unknown endpoint.', 404);
    } catch (error) {
      if (error instanceof AppError)
        json(error.status, { error: { code: error.code, message: error.message } });
      else {
        console.error('Request failed:', (error as Error).message);
        json(500, {
          error: { code: 'INTERNAL_ERROR', message: 'The request could not be completed.' },
        });
      }
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(options.port ?? 4174, '127.0.0.1', () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No server address.');
  origin = `http://127.0.0.1:${address.port}`;
  const timer = setInterval(() => {
    if (!stopped && !active && options.worker !== false) {
      active = engine
        .tick()
        .catch((e) => console.error('Worker error:', e.message))
        .finally(() => {
          active = null;
        });
    }
  }, 50);
  return {
    origin,
    close: async () => {
      stopped = true;
      clearInterval(timer);
      await active;
      await new Promise<void>((resolve, reject) =>
        server.close((e) => (e ? reject(e) : resolve())),
      );
    },
  };
}
async function main(): Promise<void> {
  const store = new RunStore(resolve(process.env.WEBMCP_DB ?? '.data/research.sqlite'));
  const fixtures = process.env.WEBMCP_RESEARCH_MODE === 'fixtures';
  const portal = fixtures ? new MockMaterialPortal() : new UnconfiguredMaterialPortal();
  const engine = new ResearchOrchestrator(store, portal, {
    archetypes: fixtures
      ? new MockMaterialPortal()
      : new GitHubArchetypeResearcher({
          token: process.env.WEBMCP_GITHUB_TOKEN,
          networkEnabled: process.env.WEBMCP_GITHUB_NETWORK === 'bounded',
          network: new GitHubNetworkStore(
            process.env.WEBMCP_GITHUB_STATE ? resolve(process.env.WEBMCP_GITHUB_STATE) : undefined,
          ),
        }),
  });
  const service = await createResearchServer({
    engine,
    port: Number(process.env.PORT ?? 4174),
    token: process.env.WEBMCP_TOKEN,
  });
  console.log(
    `Research studio: ${service.origin}\nArchetypes: ${engine.archetypes.provider}; materials: ${portal.provider}.`,
  );
  const close = async () => {
    await service.close();
    store.close();
    process.exit(0);
  };
  process.once('SIGINT', close);
  process.once('SIGTERM', close);
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  await main();
