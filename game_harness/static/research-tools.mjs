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
  if (!response.ok) throw new Error(result.error?.message ?? String(result.detail ?? 'Research request failed'));
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
    document.getElementById('research-link').title = registration.supported ? 'Browser WebMCP tools registered' : 'Open the research workspace';
  } catch {
    document.getElementById('research-link').title = 'Research unavailable: check access settings and service configuration';
  } finally { registering = false; }
}
document.getElementById('save-token').addEventListener('click', () => { void connect(); });
addEventListener('pagehide', () => registration?.dispose(), {once: true});
void connect();
