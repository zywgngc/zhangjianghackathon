import type { ArchetypeResearchPort } from './archetypes.ts';
import { setTimeout as delay } from 'node:timers/promises';
import { GitHubNetworkStore, GitHubFailure } from './github-network.ts';
import type {
  MaterialCandidate,
  MaterialSearchRequest,
  MaterialSearchResult,
} from '../material-portal.ts';

type Fetch = typeof globalThis.fetch;
type Repo = {
  full_name: string;
  default_branch: string;
  description: string | null;
  language: string | null;
  license: { spdx_id: string } | null;
  private?: boolean;
  archived?: boolean;
  fork?: boolean;
};
type Entry = { path: string; type: string; mode: string; sha: string; size?: number };
const shaPattern = /^[a-f0-9]{40}$/i;
const repoPattern = /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/i;
const skipPath =
  /(^|\/)(node_modules|vendor|dist|build|coverage|\.git|third.party|test[s]?|__tests__)(\/|$)|\.min\.|bundle\./i;
const sourcePattern = /\.(?:[cm]?[jt]sx?|html)$/i;
const patterns = [
  { tag: '移动 / 输入', re: /velocity|moveSpeed|keydown|pointerdown|joystick|setVelocity/i },
  { tag: '战斗 / 碰撞', re: /damage|collision|collider|overlap|hitbox|projectile/i },
  { tag: '刷怪 / 生存', re: /spawnEnemy|spawnEnemies|enemySpawn|waveNumber|spawnWave/i },
  { tag: '升级 / 成长', re: /levelUp|upgrade|experience|skillTree/i },
  { tag: '跑酷 / 跳跃', re: /runner|jump|platform|grounded/i },
  { tag: '棋盘 / 解谜', re: /puzzle|merge|tile|grid|matchThree/i },
];
type Access = { signal?: AbortSignal; runId: string; cachedTimes: Set<number> };

/** Bounded, public-only, read-only repository exploration. Never clones, installs or executes code. */
export class GitHubArchetypeResearcher implements ArchetypeResearchPort {
  readonly provider = 'github-archetype-research';
  readonly idempotentRequests = true;
  private fetcher: Fetch;
  private token?: string;
  readonly automaticRetries = false;
  readonly networkEnabled: boolean;
  readonly network: GitHubNetworkStore;
  private wait: (ms: number, signal?: AbortSignal) => Promise<void>;
  constructor(
    options: {
      fetch?: Fetch;
      token?: string;
      networkEnabled?: boolean;
      network?: GitHubNetworkStore;
      wait?: (ms: number, signal?: AbortSignal) => Promise<void>;
    } = {},
  ) {
    this.fetcher = options.fetch ?? globalThis.fetch;
    this.token = options.token;
    this.networkEnabled = options.networkEnabled === true;
    this.network = options.network ?? new GitHubNetworkStore();
    this.wait =
      options.wait ??
      (async (ms, signal) => {
        await delay(ms, undefined, { signal });
      });
  }
  networkStatus(runId?: string) {
    return { enabled: this.networkEnabled, ...this.network.status(runId) };
  }
  close() {
    this.network.close();
  }
  private async get(
    path: string,
    context: Access,
    maxBytes = 2_000_000,
    ttl = 86400000,
  ): Promise<any> {
    const { signal } = context;
    signal?.throwIfAborted();
    const cached = this.network.cached(path);
    if (cached) {
      context.cachedTimes.add(cached.savedAt);
      return cached.value;
    }
    if (!this.networkEnabled)
      throw new GitHubFailure(
        'UNAVAILABLE',
        'GitHub 联网已关闭，仅使用本地缓存；此查询暂无缓存，未发出网络请求。',
      );
    // Only locally constructed API paths, never URLs obtained from repository content.
    if (!path.startsWith('/') || path.startsWith('//'))
      throw new GitHubFailure('INVALID_INPUT', 'Invalid GitHub API path.');
    const url = new URL(path, 'https://api.github.com');
    if (url.origin !== 'https://api.github.com')
      throw new GitHubFailure('INVALID_INPUT', 'Unexpected API origin.');
    let ticket;
    do {
      signal?.throwIfAborted();
      ticket = this.network.reserve(context.runId, path.startsWith('/search/') ? 'search' : 'core');
      if (!ticket.owner) await this.wait(ticket.waitMs, signal);
    } while (!ticket.owner);
    try {
      signal?.throwIfAborted();
      const timeout = AbortSignal.timeout(10000);
      let response: Response;
      try {
        response = await this.fetcher(url, {
          headers: {
            Accept: 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
            'User-Agent': 'WebMCP-Archetype-Research',
            ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
          },
          redirect: 'error',
          signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        });
      } catch (error) {
        this.network.pause(this.network.now() + 60000, '网络异常或取消；暂停联网，不自动重试。');
        throw error;
      }
      this.network.observe(response);
      if (!response.ok) {
        await response.body?.cancel();
        if (response.status === 429 || response.status === 403) {
          throw new GitHubFailure(
            'RATE_LIMITED',
            'GitHub 返回限流或拒绝访问；共享冷却已保存，停止联网且不自动重试。',
          );
        }
        throw new GitHubFailure(
          response.status === 401 || response.status === 403 ? 'AUTH_REQUIRED' : 'UNAVAILABLE',
          `GitHub 返回 HTTP ${response.status}；未使用模拟结果替代。`,
        );
      }
      const reader = response.body?.getReader();
      if (!reader) throw new GitHubFailure('UNAVAILABLE', 'GitHub returned an empty body.');
      let length = 0;
      const parts: Uint8Array[] = [];
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > maxBytes)
            throw new GitHubFailure(
              'UNAVAILABLE',
              'GitHub response exceeded the bounded inspection size.',
            );
          parts.push(value);
        }
      } finally {
        await reader.cancel().catch(() => {});
      }
      let value: unknown;
      try {
        value = JSON.parse(Buffer.concat(parts).toString('utf8'));
      } catch {
        throw new GitHubFailure('UNAVAILABLE', 'GitHub returned invalid JSON.');
      }
      this.network.cache(path, value, ttl);
      return value;
    } finally {
      this.network.release(ticket.owner);
    }
  }
  private async readBlob(repo: string, file: Entry, context: Access): Promise<string> {
    if (file.mode !== '100644' && file.mode !== '100755')
      throw new GitHubFailure('UNAVAILABLE', 'Symlinks/submodules are not inspected.');
    if (!shaPattern.test(file.sha) || (file.size ?? Infinity) > 96000)
      throw new GitHubFailure(
        'UNAVAILABLE',
        'File is too large or has no immutable blob identity.',
      );
    const blob = await this.get(`/repos/${repo}/git/blobs/${file.sha}`, context, 150000, 86400000);
    if (
      blob.sha !== file.sha ||
      blob.encoding !== 'base64' ||
      typeof blob.content !== 'string' ||
      blob.content.length > 140000
    )
      throw new GitHubFailure('UNAVAILABLE', 'Unexpected GitHub blob response.');
    const bytes = Buffer.from(blob.content, 'base64');
    if (bytes.length > 96000 || bytes.includes(0))
      throw new GitHubFailure('UNAVAILABLE', 'Oversized or binary file was skipped.');
    return bytes.toString('utf8');
  }
  private async inspect(repo: Repo, query: string, context: Access): Promise<MaterialCandidate> {
    const root = `https://github.com/${repo.full_name}`;
    const ref = await this.get(
      `/repos/${repo.full_name}/git/ref/heads/${encodeURIComponent(repo.default_branch)}`,
      context,
    );
    const revision = ref.object?.sha;
    if (ref.object?.type !== 'commit' || !shaPattern.test(revision))
      throw new GitHubFailure('UNAVAILABLE', 'Repository branch could not be pinned.');
    // Read commit metadata without downloading its potentially huge diff.
    const commit = await this.get(
      `/repos/${repo.full_name}/git/commits/${revision}`,
      context,
      2_000_000,
      86400000,
    );
    const treeSha = commit.tree?.sha;
    if (commit.sha !== revision || !shaPattern.test(revision) || !shaPattern.test(treeSha))
      throw new GitHubFailure('UNAVAILABLE', 'Repository revision could not be pinned.');
    const tree = await this.get(
      `/repos/${repo.full_name}/git/trees/${treeSha}`,
      context,
      2_000_000,
      86400000,
    );
    if (!Array.isArray(tree.tree))
      throw new GitHubFailure('UNAVAILABLE', 'Repository tree is unavailable.');
    // Bound traversal to code directories; large asset/dependency trees are not requested.
    const treeLimits: string[] = [];
    const allEntries: Entry[] = [...tree.tree];
    const codeDirs: Entry[] = tree.tree
      .filter(
        (e: Entry) =>
          e?.type === 'tree' &&
          shaPattern.test(e.sha) &&
          /^(src|source|game|scripts|js|app|lib|client)$/i.test(e.path),
      )
      .sort((a: Entry, b: Entry) => Number(b.path === 'src') - Number(a.path === 'src'))
      .slice(0, 1);
    for (const dir of codeDirs) {
      try {
        const child = await this.get(
          `/repos/${repo.full_name}/git/trees/${dir.sha}?recursive=1`,
          context,
          2_000_000,
          86400000,
        );
        if (!Array.isArray(child.tree))
          throw new GitHubFailure('UNAVAILABLE', 'Invalid source subtree.');
        allEntries.push(...child.tree.map((e: Entry) => ({ ...e, path: `${dir.path}/${e.path}` })));
        if (child.truncated) treeLimits.push(`${dir.path} 目录树被截断。`);
      } catch (error) {
        if (
          context.signal?.aborted ||
          (error instanceof GitHubFailure && ['RATE_LIMITED', 'AUTH_REQUIRED'].includes(error.code))
        )
          throw error;
        treeLimits.push(`${dir.path}：源码目录未完整读取。`);
      }
    }
    const entries: Entry[] = allEntries.filter(
      (e: Entry) =>
        e &&
        typeof e.path === 'string' &&
        e.type === 'blob' &&
        ['100644', '100755'].includes(e.mode) &&
        shaPattern.test(e.sha) &&
        (e.size ?? Infinity) <= 96000 &&
        !skipPath.test(e.path),
    );
    const readme = entries.find((e) => /^readme(?:\.(?:md|txt|rst))?$/i.test(e.path));
    const license = entries.find((e) => /^(?:license|licence|copying)(?:\.[a-z]+)?$/i.test(e.path));
    const packageFile = entries.find((e) => e.path === 'package.json');
    const score = (e: Entry) =>
      (/player|enemy|combat|game|scene|world/i.test(e.path) ? 5 : 0) +
      (/main|index|update/i.test(e.path) ? 2 : 0) -
      e.path.split('/').length / 10;
    const sources = entries
      .filter(
        (e) =>
          sourcePattern.test(e.path) && !/\.d\.ts$|config\.|lock\.|vite\.|webpack\./i.test(e.path),
      )
      .sort((a, b) => score(b) - score(a))
      .slice(0, 3);
    const files = [
      ...new Map(
        [readme, license, packageFile, ...sources]
          .filter((e): e is Entry => !!e)
          .map((e) => [e.path, e]),
      ).values(),
    ];
    const evidence: MaterialCandidate['evidence'] = [
      {
        description: `仓库检索查询：${query}。这是发现依据，相关性仍需人工或模型核对。`,
        sourceUri: `https://github.com/search?q=${encodeURIComponent(query)}&type=repositories`,
      },
    ];
    const limitations = [
      '仅进行静态、有范围限制的源码读取；未安装依赖、未运行游戏、未验证行为或组合兼容性。',
      '源码许可证与图片、音频、品牌等素材的授权范围需分别核对。',
      '目录探索只覆盖根目录和最多一个代码目录；其他目录未搜索。',
      ...treeLimits,
    ];
    if (tree.truncated) limitations.push('GitHub 目录树被截断，文件探索范围不完整。');
    const paths: string[] = [],
      hints = new Set<string>(),
      symbols = new Set<string>();
    let readSources = 0,
      readLicense = false,
      detectedEngine = '未识别';
    for (const file of files) {
      let text: string;
      try {
        text = await this.readBlob(repo.full_name, file, context);
      } catch (error) {
        if (context.signal?.aborted) throw error;
        if (
          error instanceof GitHubFailure &&
          ['RATE_LIMITED', 'AUTH_REQUIRED'].includes(error.code)
        )
          throw error;
        limitations.push(`${file.path}：未能读取，未计入证据。`);
        continue;
      }
      paths.push(file.path);
      const url = `${root}/blob/${revision}/${file.path.split('/').map(encodeURIComponent).join('/')}`;
      evidence.push({
        description: `已读取 ${file.path}（${Buffer.byteLength(text)} 字节，blob ${file.sha}）。`,
        sourceUri: url,
      });
      if (file === license) readLicense = true;
      if (file === packageFile) {
        try {
          const pkg = JSON.parse(text),
            deps = { ...pkg.dependencies, ...pkg.devDependencies };
          const engines = Object.keys(deps).filter((k) =>
            /^(phaser|three|pixi\.js|@babylonjs\/core|babylonjs|kaboom|excalibur)$/.test(k),
          );
          if (engines.length)
            detectedEngine = engines.map((k) => `${k}@${String(deps[k]).slice(0, 60)}`).join(', ');
        } catch {
          limitations.push('package.json 无法解析，依赖识别不完整。');
        }
      }
      if (sources.includes(file)) {
        readSources++;
        const lines = text.split(/\r?\n/);
        for (const p of patterns) {
          const line = lines.findIndex((s) => p.re.test(s));
          if (line >= 0) {
            hints.add(p.tag);
            evidence.push({
              description: `${p.tag}关键词线索：${file.path}:${line + 1}。词法命中不是行为证明。`,
              sourceUri: `${url}#L${line + 1}`,
            });
          }
        }
        for (const match of text.matchAll(/(?:class|function)\s+([A-Za-z_$][\w$]{0,60})/g)) {
          if (symbols.size < 12) symbols.add(match[1]);
        }
      }
      if (file === readme) {
        for (const [tag, regex] of [
          ['survivor', /survivor|survival|幸存|生存/i],
          ['runner', /runner|跑酷/i],
          ['puzzle', /puzzle|解谜/i],
          ['platformer', /platformer|平台跳跃/i],
        ] as const)
          if (regex.test(text)) hints.add(`README 提及 ${tag}，待核对。`);
      }
    }
    if (!readSources || ![...hints].some((h) => !h.startsWith('README')))
      throw new GitHubFailure(
        'UNSUPPORTED_INPUT',
        '未读到带玩法线索的源码，暂不作为游戏原型候选。',
      );
    if (!readLicense) limitations.push('GitHub 返回许可证标识，但本次未读到根目录许可证文件。');
    return {
      candidateId: `github:${repo.full_name}@${revision}`,
      kind: 'game_repository',
      title: repo.full_name,
      summary: `${repo.description?.slice(0, 400) ?? '仓库无简介'}\n识别到的依赖：${detectedEngine}；已读取 ${paths.length} 个文件，其中 ${readSources} 个源码文件。`,
      source: {
        uri: `${root}/tree/${revision}`,
        repositoryUrl: root,
        revision,
        paths,
        symbols: [...symbols],
      },
      coverageHints: [...hints],
      license: {
        identifier: repo.license!.spdx_id,
        sourceUri: readLicense
          ? `${root}/blob/${revision}/${license!.path.split('/').map(encodeURIComponent).join('/')}`
          : root,
        scope: 'unknown',
      },
      evidence: evidence.slice(0, 30),
      limitations,
    };
  }
  async search(
    request: MaterialSearchRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<MaterialSearchResult> {
    const meta = {
      contractVersion: '0.2' as const,
      requestId: request.requestId,
      discussion: {
        runId: request.discussion.runId,
        threadId: request.discussion.threadId,
        round: request.discussion.round,
      },
      provider: this.provider,
      retrievedAt: new Date().toISOString(),
      isMock: false,
    };
    if (request.purpose !== 'archetype')
      return {
        ...meta,
        status: 'failed',
        error: {
          code: 'UNSUPPORTED_INPUT',
          message:
            'GitHub archetype research only accepts repository discovery, not material portal work.',
          retryable: false,
          retryAfterMs: null,
        },
      };
    const candidates: MaterialCandidate[] = [],
      limitations: string[] = [];
    const context: Access = {
      signal: options.signal,
      runId: request.discussion.runId,
      cachedTimes: new Set(),
    };
    try {
      const queries = githubQueries(request);
      const repos = new Map<string, { repo: Repo; query: string }>();
      const descriptions = new Set<string>();
      let skippedLicense = 0;
      for (const query of queries) {
        const result = await this.get(
          `/search/repositories?${new URLSearchParams({ q: query, per_page: '30' })}`,
          context,
        );
        if (!Array.isArray(result.items))
          throw new GitHubFailure('UNAVAILABLE', 'Malformed GitHub repository search response.');
        if (result.incomplete_results) limitations.push('GitHub 搜索返回不完整结果。');
        for (const repo of result.items as Repo[]) {
          if (
            !repo ||
            !repoPattern.test(repo.full_name) ||
            typeof repo.default_branch !== 'string' ||
            repo.private ||
            repo.archived ||
            repo.fork
          )
            continue;
          if (!repo.license?.spdx_id || repo.license.spdx_id === 'NOASSERTION') {
            skippedLicense++;
            continue;
          }
          if (
            /template|awesome|framework|collection|locali[sz]ation|translation|cheat|trainer|save.?game.?editor|save.editor|wiki|calculator|mod.manager|vortex|electron|(?:^|-)tools(?:-|$)/i.test(
              repo.full_name,
            )
          )
            continue;
          // These are different game archetypes, despite matching "survivor" by name.
          if (
            /archetype=survivor/.test(request.description) &&
            /social elimination|spectator game|LLM agents|reality (?:show|tv)/i.test(
              repo.description ?? '',
            )
          )
            continue;
          const descriptionKey = repo.description?.trim().toLowerCase();
          if (
            !repos.has(repo.full_name) &&
            (!descriptionKey || !descriptions.has(descriptionKey))
          ) {
            repos.set(repo.full_name, { repo, query });
            if (descriptionKey) descriptions.add(descriptionKey);
          }
        }
      }
      if (skippedLicense)
        limitations.push(
          `略过 ${skippedLicense} 条未提供明确许可证标识的搜索结果；公开可读不等于可复用。`,
        );
      // Several metadata candidates; bounded source inspection for only the leading repository.
      const selected = [...repos.values()].slice(0, Math.min(request.limit, 3));
      for (const { repo, query } of selected)
        candidates.push({
          candidateId: `github:${repo.full_name}:discovery`,
          kind: 'game_repository',
          title: repo.full_name,
          summary: repo.description?.slice(0, 400) || '仓库暂无简介',
          source: {
            uri: `https://github.com/${repo.full_name}`,
            repositoryUrl: `https://github.com/${repo.full_name}`,
            revision: null,
            paths: [],
            symbols: [],
          },
          coverageHints: [],
          license: {
            identifier: repo.license!.spdx_id,
            sourceUri: `https://github.com/${repo.full_name}`,
            scope: 'unknown',
          },
          evidence: [
            {
              description: `GitHub 仓库搜索返回；查询：${query}。仅为候选线索。`,
              sourceUri: `https://github.com/${repo.full_name}`,
            },
          ],
          limitations: ['仅获取仓库搜索元数据，尚未读取源码、固定 commit 或验证玩法。'],
        });
      if (selected.length) {
        try {
          candidates[0] = await this.inspect(selected[0].repo, selected[0].query, context);
        } catch (error) {
          if (options.signal?.aborted) throw error;
          limitations.push(
            `首选仓库源码读取未完成：${error instanceof GitHubFailure ? error.message : '网络错误或超时'}。保留元数据候选，不扩大检索。`,
          );
        }
      }
      if (context.cachedTimes.size)
        limitations.push(
          `使用本地缓存，最早获取于 ${new Date(Math.min(...context.cachedTimes)).toISOString()}；不代表刚刚访问了 GitHub。`,
        );
      limitations.push(
        '每个创意最多 15 次 HTTP 请求（含失败及追问）；通常为 2 次搜索 + 首选仓库最多 10 次读取。',
      );
      if (candidates.length < request.limit)
        limitations.push(`仅找到 ${candidates.length}/${request.limit} 个候选，不以占位数据补足。`);
      limitations.push(
        '检索使用英文玩法关键词与 JS/TS 范围；不是全网召回，候选相似性仅有文字/词法依据。图片未被解释。',
      );
      return { ...meta, status: 'partial', candidates, limitations };
    } catch (error) {
      if (options.signal?.aborted) throw error;
      const failure =
        error instanceof GitHubFailure
          ? error
          : new GitHubFailure('UNAVAILABLE', 'GitHub 网络读取失败或超时；未使用模拟结果替代。');
      if (candidates.length)
        return {
          ...meta,
          status: 'partial',
          candidates,
          limitations: [...limitations, failure.message],
        };
      return {
        ...meta,
        status: 'failed',
        error: {
          code: failure.code,
          message: failure.message,
          retryable: false,
          retryAfterMs: null,
        },
      };
    }
  }
}

export function githubQueries(request: MaterialSearchRequest): string[] {
  const family =
    /archetype=(runner|survivor|puzzle|platformer)/.exec(request.description)?.[1] ??
    (/幸存|生存|surviv/i.test(request.description)
      ? 'survivor'
      : /解谜|puzzle|战力吞噬/i.test(request.description)
        ? 'puzzle'
        : 'runner');
  const terms: Record<string, string> = {
    runner: 'runner game',
    survivor: 'survivor game',
    puzzle: 'puzzle game',
    platformer: 'platformer game',
  };
  const engine =
    request.constraints.engine === 'phaser'
      ? ' phaser'
      : request.constraints.engine === 'babylon'
        ? ' babylon'
        : '';
  // A focused engine query plus a broader alternative helps compare architecture tradeoffs.
  return [
    `${terms[family]}${engine} language:TypeScript fork:false archived:false is:public`,
    `${terms[family]} language:JavaScript fork:false archived:false is:public`,
  ];
}
