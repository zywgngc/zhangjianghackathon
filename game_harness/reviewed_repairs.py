"""Apply independently reviewed repairs to a stopped project, with durable replay.

This is attributed integration work, never acceptance evidence. Pending replay
admits only its original before/after files; all other source stays unchanged.
"""
import hashlib
import json
from pathlib import Path
import re
import time

from .coding_agent import CodingAgent


STOPPED = {'failed', 'needs_attention', 'cancelled'}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _sha(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value))


def _read(workspace, path):
    # Preserve exact UTF-8 bytes, including line endings, in durable intent.
    return workspace.resolve(path, writable=True).read_bytes().decode('utf-8')


def _manifest_sha(manifest):
    return hashlib.sha256(json.dumps({'engine': manifest['engine'], 'files': manifest['files']},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _plan(proposal, read):
    before, after, ids = {}, {}, set()
    for patch in proposal['patches']:
        if (not isinstance(patch, dict) or not isinstance(patch.get('id'), str) or not patch['id']
                or patch['id'] in ids or not isinstance(patch.get('path'), str)
                or not _sha(patch.get('expected_sha256')) or not isinstance(patch.get('old'), str)
                or not patch['old'] or not isinstance(patch.get('new'), str) or patch['old'] == patch['new']):
            raise ValueError('Malformed or duplicate repair patch')
        ids.add(patch['id'])
        path = patch['path']
        if path not in before:
            before[path] = read(path)
            after[path] = before[path]
        if digest(after[path]) != patch['expected_sha256']:
            raise ValueError('Stale repair precondition: ' + patch['id'])
        if after[path].count(patch['old']) != 1:
            raise ValueError('Repair old text is not unique: ' + patch['id'])
        after[path] = after[path].replace(patch['old'], patch['new'], 1)
        if patch.get('after_sha256') is not None and digest(after[path]) != patch['after_sha256']:
            raise ValueError('Repair output hash mismatch: ' + patch['id'])
    return [{'path': path, 'before': before[path], 'after': after[path],
             'before_sha256': digest(before[path]), 'after_sha256': digest(after[path])} for path in before]


def _expected_after(baseline, files):
    changed = {item['path']: item for item in files}
    rows = [{**row, **({'sha256': changed[row['path']]['after_sha256'],
                       'bytes': len(changed[row['path']]['after'].encode())} if row['path'] in changed else {})}
            for row in baseline['files']]
    if not changed.keys() <= {row['path'] for row in baseline['files']}:
        raise ValueError('Repair refers to source outside the baseline manifest')
    return _manifest_sha({'engine': baseline['engine'], 'files': rows})


def _replay_preflight(workspace, journal):
    baseline = journal['baseline_manifest']
    current = workspace.snapshot()
    original = {item['path']: item for item in baseline['files']}
    present = {item['path']: item for item in current['files']}
    changed = {item['path']: item for item in journal['files']}
    if current['engine'] != baseline['engine'] or original.keys() != present.keys():
        raise ValueError('Project file set or engine drift during reviewed repair replay')
    for path, row in original.items():
        if path in changed:
            workspace.resolve(path, writable=True)
            item = changed[path]
            allowed = {item['after_sha256']} if journal['status'] == 'complete' else {
                item['before_sha256'], item['after_sha256']}
            if present[path]['sha256'] not in allowed:
                raise ValueError('Source drift during reviewed repair replay: ' + path)
        elif present[path] != row:
            raise ValueError('Unchanged source drift during reviewed repair replay: ' + path)
    if journal['status'] == 'complete' and current['sha256'] != journal['source_after_expected']:
        raise ValueError('Completed reviewed repair source drift')


def _apply_locked(store, workspace, rid, proposal_sha256, actor):
    raw = store.path(rid, 'proposed-repairs.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != proposal_sha256:
        raise ValueError('Repair proposal changed after review')
    proposal = json.loads(raw)
    approval_path = store.path(rid, 'reviewed-repair-decision.json')
    approval_raw = approval_path.read_bytes() if approval_path.is_file() else b'{}'
    approval = json.loads(approval_raw)
    approval_sha = hashlib.sha256(approval_raw).hexdigest()
    if (not isinstance(proposal, dict) or proposal.get('run_id') != rid
            or not _sha(proposal.get('source_baseline_sha256'))
            or not isinstance(proposal.get('patches'), list) or not proposal['patches']):
        raise ValueError('Repair proposal belongs to a different project or is malformed')
    if (not isinstance(approval, dict) or approval.get('decision') != 'approve' or not approval.get('model')
            or approval.get('proposal_sha256') != proposal_sha256
            or approval.get('source_baseline_sha256') != proposal['source_baseline_sha256']):
        raise ValueError('Missing source-bound independent model review')
    name = 'reviewed-repair-' + proposal_sha256[:20] + '.journal.json'
    workspace_path = str(Path(workspace.root).resolve())
    journal = store.read_json(rid, name)
    if journal is None:
        baseline = workspace.snapshot()
        if baseline['sha256'] != proposal['source_baseline_sha256']:
            raise ValueError('Project changed since the reviewed source baseline')
        files = _plan(proposal, lambda path: _read(workspace, path))
        journal = {'version': 2, 'run_id': rid, 'workspace': workspace_path, 'actor': actor,
            'created': time.time(), 'status': 'pending', 'proposal_sha256': proposal_sha256,
            'approval_sha256': approval_sha, 'source_before': baseline['sha256'],
            'baseline_manifest': baseline, 'source_after_expected': _expected_after(baseline, files),
            'reviewer_model': approval['model'], 'official_acceptance': False, 'files': files}
        store.write(rid, name, journal)
    else:
        if (journal.get('version') != 2 or journal.get('run_id') != rid or journal.get('workspace') != workspace_path
                or journal.get('actor') != actor or journal.get('proposal_sha256') != proposal_sha256
                or journal.get('approval_sha256') != approval_sha or journal.get('reviewer_model') != approval['model']
                or journal.get('source_before') != proposal['source_baseline_sha256']
                or journal.get('status') not in {'pending', 'complete'} or journal.get('official_acceptance') is not False):
            raise ValueError('Reviewed repair journal binding changed')
        baseline = journal.get('baseline_manifest') or {}
        if _manifest_sha(baseline) != proposal['source_baseline_sha256']:
            raise ValueError('Reviewed repair baseline manifest changed')
        saved = {item['path']: item['before'] for item in journal['files']}
        planned = _plan(proposal, lambda path: saved[path])
        if planned != journal['files'] or _expected_after(baseline, planned) != journal.get('source_after_expected'):
            raise ValueError('Reviewed repair journal content changed')
    # Preflight every source file, including untouched/protected configuration,
    # before changing even one remaining target.
    _replay_preflight(workspace, journal)
    if journal['status'] == 'complete':
        if journal.get('source_after') != journal['source_after_expected']:
            raise ValueError('Completed reviewed repair hash changed')
        return {'status': 'already_applied', 'journal': name, 'official_acceptance': False,
                'source_after': journal['source_after'], 'actor': journal['actor']}
    for item in journal['files']:
        current = digest(_read(workspace, item['path']))
        if current == item['before_sha256']:
            workspace.write_file(item['path'], item['after'])
        elif current != item['after_sha256']:
            raise ValueError('Source changed immediately before reviewed write: ' + item['path'])
        if digest(_read(workspace, item['path'])) != item['after_sha256']:
            raise ValueError('Reviewed write did not produce its recorded source hash: ' + item['path'])
    after = workspace.snapshot()['sha256']
    if after != journal['source_after_expected']:
        raise ValueError('Project drift during reviewed repair application')
    journal.update(status='complete', completed=time.time(), source_after=after)
    store.write(rid, name, journal)
    result = {key: journal[key] for key in ('actor', 'proposal_sha256', 'source_before', 'source_after',
                                          'reviewer_model', 'official_acceptance')}
    return {**result, 'status': 'applied', 'journal': name, 'patch_count': len(proposal['patches'])}


def apply_reviewed_repairs(store, workspace, rid, proposal_sha256, *, actor='integration_coordinator'):
    if not _sha(proposal_sha256) or not isinstance(actor, str) or not actor.strip():
        raise ValueError('Reviewed integration needs a proposal SHA and attributed actor')
    run = store.get(rid)
    if not run or run['request'].get('mode') != 'project' or run['status'] not in STOPPED:
        raise RuntimeError('Reviewed integration requires a stopped project')
    with CodingAgent(None, store, workspace)._workspace_lock(rid, 'reviewed-integration', actor):
        # A short write transaction excludes API resume while files are changed.
        # It never changes task, feature, budget or acceptance state.
        with store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT status FROM runs WHERE id=?', (rid,)).fetchone()
            if not current or current['status'] not in STOPPED:
                raise RuntimeError('Reviewed integration requires a stopped project')
            result = _apply_locked(store, workspace, rid, proposal_sha256, actor)
    if result['status'] == 'applied':
        store.event(rid, 'reviewed_integration', 'reviewed_repairs_applied', result)
    return result
