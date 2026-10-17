import { registerResearchTools } from '/research-assets/webmcp.mjs';

async function researchApi(path, method = 'GET', body) {
  let token = '';
  try { token = sessionStorage.getItem('harness-token') ?? ''; } catch {}
  const response = await fetch('/api/research' + path.slice('/api'.length), {
    method,
    headers: { ...(body ? {'Content-Type': 'application/json'} : {}), ...(token ? {Authorization: `Bearer ${token}`} : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? String(result.detail ?? '研究请求失败'));
  return result;
}
let registration;
let registering = false;
async function connect() {
  if (registration || registering) return;
  registering = true;
  try {
    const config = await researchApi('/api/config');
    if (!config.configured) return;
    registration = await registerResearchTools(document, researchApi, await researchApi('/api/contract'));
    document.getElementById('research-link').title = registration.supported ? '浏览器 WebMCP 工具已就绪' : '打开研究工作台';
  } catch {
    document.getElementById('research-link').title = '研究服务暂不可用，请检查访问设置与服务配置';
  } finally { registering = false; }
}
document.getElementById('save-token').addEventListener('click', () => { void connect(); });
addEventListener('pagehide', () => registration?.dispose(), {once: true});
void connect();
