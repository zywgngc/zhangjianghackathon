"""Read-only, source-bound runtime-module reachability audit.

The trusted TypeScript compiler parses/transpiles source as data in memory; no
project JavaScript, Vite plugin, package script or model output is executed.
Static reachability never establishes invocation, engine use or game acceptance.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import PurePosixPath
import signal
from typing import Any
from urllib.parse import unquote, urlsplit

from .project_runtime import MAX_SOURCE_BYTES

SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
OWNED_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")


class _ModuleEntries(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries, self.unknown = [], []
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and attrs.get("href", "") not in {"", ".", "./", "/"}:
            self.unknown.append({"reason": "unsupported_html_base", "value": attrs["href"]})
        if tag != "script":
            return
        if attrs.get("type", "").strip().lower() != "module":
            if attrs.get("src") or attrs.get("type", "").strip().lower() not in {"application/json", "application/ld+json", "importmap"}:
                self.unknown.append({"reason": "non_module_script_not_analysed", "line": self.getpos()[0]})
            if attrs.get("type", "").strip().lower() == "importmap":
                self.unknown.append({"reason": "html_importmap_not_analysed", "line": self.getpos()[0]})
            return
        row = {"line": self.getpos()[0], "kind": "html_module"}
        if attrs.get("src"):
            row["specifier"] = attrs["src"]
            self.entries.append(row)
        else:
            row.update(kind="inline_module", text="")
            self.entries.append(row)
            self.capture = row

    def handle_data(self, data):
        if self.capture is not None:
            self.capture["text"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = None


# Kept in the trusted host module, not installed into any generated workspace.
# The only executable dependency is that workspace's already installed, pinned TS.
NODE_ANALYZER = r"""
const ts = require(process.argv[1]);
const path = require('node:path');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
if(ts.version !== input.expectedVersion) throw Error('Unexpected TypeScript compiler version');
const root = path.resolve(input.root), files = new Map(), canonical = new Map();
const key = p => {p=path.normalize(p);return ts.sys.useCaseSensitiveFileNames?p:p.toLowerCase();};
const resources = new Set(input.resources.map(name=>key(path.resolve(root,name))));
for(const [name,text] of Object.entries(input.files)) {const full=path.resolve(root,name);files.set(key(full),text);canonical.set(key(full),name);}
const directories = new Set([key(root)]);
for(const name of canonical.values()) {let p=path.dirname(path.resolve(root,name));while(p.startsWith(root)){directories.add(key(p));if(p===root)break;p=path.dirname(p);}}
const host = {fileExists:p=>files.has(key(p)),readFile:p=>files.get(key(p)),directoryExists:p=>directories.has(key(p)),realpath:p=>p,getCurrentDirectory:()=>root};
const converted=ts.convertCompilerOptionsFromJson(input.compilerOptions,root);
const options={...converted.options,noEmit:false,noEmitOnError:false,noResolve:false,allowJs:true,sourceMap:false,inlineSourceMap:false,declaration:false,emitDeclarationOnly:false};
const nodes={},edges=[],unknown=[],erasedImports=[];
const location=(sf,node)=>{const p=sf.getLineAndCharacterOfPosition(node.getStart(sf));return {line:p.line+1,column:p.character+1};};
const literal=node=>node && (ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))?node.text:null;
const marker=node=>ts.isEmptyStatement(node)||(ts.isExportDeclaration(node)&&!node.moduleSpecifier&&node.exportClause&&ts.isNamedExports(node.exportClause)&&!node.exportClause.elements.length);
const typeStatement=node=>ts.isInterfaceDeclaration(node)||ts.isTypeAliasDeclaration(node)||marker(node)||
  !!node.modifiers?.some(m=>m.kind===ts.SyntaxKind.DeclareKeyword)||
  (ts.isImportDeclaration(node)&&!!node.importClause&&(node.importClause.isTypeOnly||
   (!node.importClause.name&&node.importClause.namedBindings&&ts.isNamedImports(node.importClause.namedBindings)&&node.importClause.namedBindings.elements.length>0&&node.importClause.namedBindings.elements.every(e=>e.isTypeOnly))))||
  (ts.isExportDeclaration(node)&&(node.isTypeOnly||(node.exportClause&&ts.isNamedExports(node.exportClause)&&node.exportClause.elements.length>0&&node.exportClause.elements.every(e=>e.isTypeOnly))));
function resolve(specifier,from) {
  const [clean,query]=specifier.split('?',2);
  if(query && /^(raw|url)(?:[=&]|$)/.test(query))return {kind:'asset',specifier};
  if(query)return {kind:'unknown',reason:'unsupported_import_query',specifier};
  if(/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(clean))return {kind:'unknown',reason:'nonlocal_module',specifier};
  let request=clean;
  if(clean.startsWith('/'))request=path.resolve(root,'.'+clean);
  const resolved=ts.resolveModuleName(request,path.resolve(root,from),options,host).resolvedModule;
  if(resolved){const name=canonical.get(key(resolved.resolvedFileName));if(name)return /\.(?:[cm]?[jt]s|[jt]sx)$/.test(name)?{kind:'local',to:name}:{kind:'asset',specifier};}
  const candidate=clean.startsWith('/')?path.resolve(root,'.'+clean):path.resolve(root,path.dirname(from),clean);
  // JSON, styles and media are resources, not executable TS modules.
  if(resources.has(key(candidate)))return {kind:'asset',specifier};
  if(!clean.startsWith('.')&&!clean.startsWith('/'))return input.dependencies.some(name=>clean===name||clean.startsWith(name+'/'))?
    {kind:'external',specifier}:{kind:'unknown',reason:'unresolved_bare_module',specifier};
  return {kind:'unknown',reason:'unresolved_local_import',specifier};
}
function add(from,specifier,kind,at) {
  const target=resolve(specifier,from);
  const row={from,kind,specifier,...at,...target};
  // Preserve the syntactic edge type in addition to resolution category.
  row.resolution=target.kind;row.kind=kind;
  edges.push(row);if(target.kind==='unknown')unknown.push(row);
}
for(const [name,text] of Object.entries(input.files)) {
  if(!/\.(?:[cm]?[jt]s|[jt]sx)$/.test(name))continue;
  const sf=ts.createSourceFile(name,text,ts.ScriptTarget.Latest,true,/\.[jt]sx$/.test(name)?ts.ScriptKind.TSX:/\.[cm]?js$/.test(name)?ts.ScriptKind.JS:ts.ScriptKind.TS);
  const declarations=[];
  for(const statement of sf.statements){
    let kind=null,names=[];
    if(ts.isFunctionDeclaration(statement)){kind='function';if(statement.name)names=[statement.name.text];}
    else if(ts.isClassDeclaration(statement)){kind='class';names=[statement.name?.text||'default'];}
    else if(ts.isVariableStatement(statement)){kind='variable';names=statement.declarationList.declarations.map(d=>d.name.getText(sf));}
    else if(ts.isEnumDeclaration(statement)){kind='enum';names=[statement.name.text];}
    else if(ts.isModuleDeclaration(statement)){kind='namespace';names=[statement.name.getText(sf)];}
    if(kind&&!statement.modifiers?.some(m=>m.kind===ts.SyntaxKind.DeclareKeyword))for(const value of names)declarations.push({kind,name:value,exported:!!statement.modifiers?.some(m=>m.kind===ts.SyntaxKind.ExportKeyword),...location(sf,statement)});
  }
  const diagnostics=sf.parseDiagnostics.map(d=>({code:d.code,message:ts.flattenDiagnosticMessageText(d.messageText,' ')}));
  if(name.endsWith('.d.ts')||name.endsWith('.d.mts')||name.endsWith('.d.cts')){nodes[name]={classification:'type_only',declarations:[],diagnostics};continue;}
  const output=ts.transpileModule(text,{fileName:name,compilerOptions:options,reportDiagnostics:true});
  const emitted=ts.createSourceFile(name+'.emitted.js',output.outputText,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
  const runtime=emitted.statements.some(n=>!marker(n));
  const substantive=sf.statements.filter(n=>!marker(n));
  nodes[name]={classification:diagnostics.length?'unknown':runtime?'runtime':!substantive.length?'empty':substantive.every(typeStatement)?'type_only':'erased',declarations,diagnostics};
  const originals=[];
  function sourceWalk(node){
    if(ts.isImportDeclaration(node)||ts.isExportDeclaration(node)){const spec=literal(node.moduleSpecifier);if(spec!==null)originals.push({specifier:spec,kind:ts.isImportDeclaration(node)?'import':'reexport',at:location(sf,node)});}
    if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword){const spec=literal(node.arguments[0]);if(spec!==null)originals.push({specifier:spec,kind:'dynamic_import',at:location(sf,node)});}
    ts.forEachChild(node,sourceWalk);
  }sourceWalk(sf);
  const emittedSpecifiers=new Set();
  function emittedWalk(node){
    if(ts.isImportDeclaration(node)||ts.isExportDeclaration(node)){
      const spec=literal(node.moduleSpecifier);if(spec!==null){const kind=ts.isImportDeclaration(node)?'import':'reexport';const at=originals.find(r=>r.specifier===spec&&r.kind===kind)?.at||{line:null,column:null};add(name,spec,kind,at);emittedSpecifiers.add(spec);}
    }
    if(ts.isCallExpression(node)){
      if(node.expression.kind===ts.SyntaxKind.ImportKeyword){const spec=literal(node.arguments[0]);if(spec===null)unknown.push({from:name,kind:'dynamic_import',reason:'nonliteral_dynamic_import',...location(sf,sf)});else{add(name,spec,'dynamic_import',originals.find(r=>r.specifier===spec&&r.kind==='dynamic_import')?.at||{});emittedSpecifiers.add(spec);}}
      else if(ts.isIdentifier(node.expression)&&node.expression.text==='require')unknown.push({from:name,kind:'require',reason:'commonjs_require_not_analysed'});
      else if(ts.isPropertyAccessExpression(node.expression)&&ts.isMetaProperty(node.expression.expression)&&['glob','globEager'].includes(node.expression.name.text))unknown.push({from:name,kind:'import_meta_glob',reason:'vite_glob_not_analysed'});
    }
    ts.forEachChild(node,emittedWalk);
  }emittedWalk(emitted);
  for(const item of originals)if(item.kind==='import'&&!emittedSpecifiers.has(item.specifier))erasedImports.push({from:name,...item});
}
const entries=[],entryUnknown=[];
for(const item of input.entries){
  if(item.inline){entries.push({from:'index.html',to:item.inline,kind:'inline_module',resolution:'local',line:item.line});continue;}
  const specifier=item.specifier;
  const target=resolve(specifier,'index.html');
  if(target.kind==='unknown' || target.kind==='external')entryUnknown.push({from:'index.html',...item,...target,reason:target.reason||'html_entry_not_local'});
  else if(target.kind==='local')entries.push({from:'index.html',...item,...target,kind:'html_module',resolution:'local'});
  else entryUnknown.push({from:'index.html',...item,reason:'html_entry_not_executable_source'});
}
const adjacency=new Map();for(const edge of [...entries,...edges])if(edge.resolution==='local'){if(!adjacency.has(edge.from))adjacency.set(edge.from,[]);adjacency.get(edge.from).push(edge);}
const reachable=new Set(),witness={};const queue=['index.html'];
while(queue.length){const current=queue.shift();for(const edge of adjacency.get(current)||[]){if(reachable.has(edge.to))continue;reachable.add(edge.to);witness[edge.to]=[...(witness[current]||[]),{from:edge.from,to:edge.to,kind:edge.kind,line:edge.line??null}];queue.push(edge.to);}}
const relevantUnknown=[...entryUnknown,...unknown.filter(row=>reachable.has(row.from))];
for(const name of reachable)if(nodes[name]?.classification==='unknown')relevantUnknown.push({from:name,reason:'reachable_source_parse_error'});
process.stdout.write(JSON.stringify({compiler_version:ts.version,nodes,edges,entries,entry_unknown:entryUnknown,reachable:[...reachable].sort(),witness,unknown,relevant_unknown:relevantUnknown,erased_imports:erasedImports,compiler_option_diagnostics:converted.errors.map(d=>({code:d.code,message:ts.flattenDiagnosticMessageText(d.messageText,' ')}))}));
"""


def _plan_data(plan):
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if not isinstance(plan, dict):
        raise ValueError("Implementation plan must be an object")
    return plan.get("plan", plan)


def _owned_modules(plan: dict) -> tuple[dict[str, list[str]], list[dict]]:
    modules, skipped = {}, []
    for feature in plan.get("features", []):
        owner = str(feature.get("id", "unknown-owner"))
        for path in feature.get("owned_paths", []):
            if not isinstance(path, str):
                skipped.append({"owner": owner, "path": repr(path), "reason": "invalid_ownership_path"})
            elif PurePosixPath(path).is_absolute() or "\\" in path or any(part in {".", ".."} or part.startswith(".") for part in PurePosixPath(path).parts):
                skipped.append({"owner": owner, "path": path, "reason": "invalid_ownership_path"})
            elif any(character in path for character in "*?["):
                skipped.append({"owner": owner, "path": path, "reason": "ownership_pattern_not_expanded"})
            elif not path.endswith(OWNED_SUFFIXES):
                skipped.append({"owner": owner, "path": path, "reason": "not_a_concrete_typescript_module"})
            else:
                modules.setdefault(path, []).append(owner)
    return modules, skipped


def _environment():
    # Unlike workspace._environment(), this creates no directories or metadata.
    return {"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "CI": "1"}


async def audit_project_integration(workspace, plan, *, timeout: float = 30) -> dict[str, Any]:
    """Report exact declared runtime modules missing from the resolved entry graph.

    Ownership globs are reported, not interpreted as requirements to wire every
    source file. Unknown dynamic imports and unsupported entries prevent a claim
    of definite runtime-module disconnection. No workspace file is written.
    """
    before = workspace.snapshot()
    data = _plan_data(plan)
    owned, skipped = _owned_modules(data)
    report = {"version": 1, "kind": "static_integration_audit", "source_sha256": before["sha256"],
        "project_sha256": before["sha256"], "plan_sha256": hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "analyzer_sha256": hashlib.sha256(NODE_ANALYZER.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "unknown", "source_unchanged": True,
        "modules": [], "gaps": [], "unknown": [], "skipped_owned_paths": skipped,
        "scope": "Concrete plan-owned TypeScript modules reachable through emitted local ESM from index.html module entries; no game acceptance result.",
        "limitations": ["Static reachable means a possible import path, not a function call, branch execution or rendered game mechanism.",
            "Dynamic literal imports are possible edges even inside functions that were never called; tree-shaking and runtime execution are not proven.",
            "External pinned dependencies and non-module browser script/resource loading are outside this local ESM graph.",
            "Only the installed pinned TypeScript compiler is executed. Source parsing/transpilation occurs in memory; no model, package script or project JavaScript is executed."]}
    parsed = None
    try:
        manifest = {row["path"]: row for row in before["files"]}
        files = {}
        total = 0
        for relative, row in manifest.items():
            if relative.endswith(SOURCE_SUFFIXES) or relative in {"index.html", "tsconfig.json", "package.json"}:
                path = workspace._inside(relative)
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                    raise ValueError("Source changed before AST capture: " + relative)
                total += len(raw)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("Source exceeds the read-only audit input quota")
                files[relative] = raw.decode("utf-8")
        html = _ModuleEntries()
        html.feed(files.get("index.html", ""))
        entries = []
        for index, entry in enumerate(html.entries):
            row = dict(entry)
            if row["kind"] == "inline_module":
                name = f"__harness_inline_entry_{index}.mjs"
                if name in files:
                    raise ValueError("Virtual HTML entry collides with a source file")
                files[name] = row.pop("text")
                row["inline"] = name
            else:
                value = urlsplit(row["specifier"])
                if value.scheme or value.netloc or value.fragment or value.query:
                    html.unknown.append({"reason": "unsupported_html_module_url", "specifier": row["specifier"]})
                    continue
                row["specifier"] = unquote(value.path)
                if not row["specifier"].startswith(("/", "./", "../")):
                    row["specifier"] = "./" + row["specifier"]
            entries.append(row)
        if any(row["reason"] == "unsupported_html_base" for row in html.unknown):
            entries = []
        compiler = workspace.root / "node_modules/typescript/lib/typescript.js"
        modules_root = workspace.root / "node_modules"
        if modules_root.is_symlink() or not compiler.is_file() or not compiler.resolve().is_relative_to(modules_root.resolve()):
            raise ValueError("Installed pinned TypeScript compiler is unavailable or redirects outside node_modules")
        expected = json.loads((workspace.template_dir / "package.json").read_text())["devDependencies"]["typescript"]
        declared = json.loads(files["package.json"]).get("devDependencies", {}).get("typescript")
        if declared != expected:
            raise ValueError("Project TypeScript version differs from its trusted template")
        report["compiler"] = {"path": "node_modules/typescript/lib/typescript.js", "expected_version": expected,
            "sha256": hashlib.sha256(compiler.read_bytes()).hexdigest()}
        payload = {"root": str(workspace.root), "files": files, "resources": list(manifest), "entries": entries,
            "compilerOptions": json.loads(files["tsconfig.json"]).get("compilerOptions", {}), "expectedVersion": expected,
            "dependencies": list({**json.loads(files["package.json"]).get("dependencies", {}),
                **json.loads(files["package.json"]).get("devDependencies", {})})}
        process = await asyncio.create_subprocess_exec("node", "--input-type=commonjs", "-e", NODE_ANALYZER,
            str(compiler), cwd=workspace.root, env=_environment(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.communicate()
            raise
        if process.returncode != 0:
            raise ValueError("TypeScript AST audit failed: " + stderr.decode(errors="replace")[-1500:])
        if len(stdout) > 8_000_000:
            raise ValueError("AST graph exceeded the bounded audit response size")
        parsed = json.loads(stdout)
        if hashlib.sha256(compiler.read_bytes()).hexdigest() != report["compiler"]["sha256"]:
            raise ValueError("TypeScript compiler changed during audit")
        report.update(compiler={**report["compiler"], "version": parsed["compiler_version"]},
            entries=parsed["entries"], edges=parsed["edges"], erased_imports=parsed["erased_imports"],
            reachable_modules=parsed["reachable"], unknown=html.unknown + parsed["relevant_unknown"],
            compiler_option_diagnostics=parsed["compiler_option_diagnostics"])
        if not html.entries:
            report["unknown"].append({"reason": "no_html_module_entry"})
        if not owned:
            report["unknown"].append({"reason": "no_concrete_plan_owned_typescript_modules"})
        if parsed["compiler_option_diagnostics"]:
            report["unknown"].append({"reason": "compiler_options_not_resolved"})
        reachable = set(parsed["reachable"])
        for path, owners in sorted(owned.items()):
            row = {"path": path, "owners": owners, "sha256": manifest.get(path, {}).get("sha256")}
            node = parsed["nodes"].get(path)
            if path not in manifest:
                row.update(status="missing", reason="declared_owned_file_is_absent")
            elif node is None:
                row.update(status="unknown", reason="source_was_not_parsed")
            else:
                row.update(classification=node["classification"], runtime_declarations=node["declarations"], diagnostics=node["diagnostics"])
                classification = node["classification"]
                if classification in {"type_only", "empty"}:
                    row.update(status=classification, reason="no_runtime_module_content")
                elif classification in {"unknown", "erased"}:
                    row.update(status="unknown", reason="source_parse_or_emit_requires_review")
                elif path in reachable:
                    row.update(status="reachable", witness=parsed["witness"][path])
                elif report["unknown"]:
                    row.update(status="unknown", reason="unresolved_reachable_loading_may_reference_this_module")
                else:
                    row.update(status="unreachable", reason="no_local_runtime_import_path_from_html_module_entries")
                    report["gaps"].append({"path": path, "owners": owners, "sha256": row["sha256"],
                        "reason": row["reason"], "runtime_declarations": node["declarations"]})
            report["modules"].append(row)
        report["status"] = "gaps_observed" if report["gaps"] else "unknown" if report["unknown"] or any(row["status"] in {"unknown", "missing"} for row in report["modules"]) else "no_static_gap_observed"
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError) as exc:
        report["unknown"].append({"reason": "audit_unavailable", "error": type(exc).__name__ + ": " + str(exc)[:2000]})
        report["status"] = "unknown"
        if not report["modules"]:
            report["modules"] = [{"path": path, "owners": owners, "status": "unknown", "reason": "compiler_audit_unavailable"} for path, owners in sorted(owned.items())]
    finally:
        try:
            after = workspace.snapshot()
            report["source_unchanged"] = after == before
        except (OSError, ValueError) as exc:
            after = {}
            report["source_unchanged"] = False
            report["unknown"].append({"reason": "source_snapshot_unavailable_after_audit", "error": str(exc)[:1000]})
        if not report["source_unchanged"]:
            report["status"] = "unknown"
            report["gaps"] = []
            report["unknown"].append({"reason": "source_changed_during_audit", "observed_source_sha256": after.get("sha256")})
            for row in report["modules"]:
                row.update(status="unknown", reason="source_changed_during_audit")
    report["counts"] = {status: sum(row["status"] == status for row in report["modules"])
        for status in ("reachable", "unreachable", "type_only", "empty", "missing", "unknown")}
    return report
