"""Real workspace integration safeguards; none of these fixtures are game evidence."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

import pytest

from game_harness.coding_agent import CodingAgent
from game_harness.project_runtime import ProjectWorkspace
from game_harness.reviewed_repairs import apply_reviewed_repairs, digest


@pytest.fixture
def integration(store, tmp_path):
    rid = store.create({'mode': 'project', 'engine': 'phaser', 'description': 'reviewed integration test fixture'})['id']
    store.update(rid, status='failed', stage='fixture_stopped', error='fixture failure')
    workspace = ProjectWorkspace(tmp_path / 'project', 'phaser')
    workspace.initialize()  # No npm or network calls.
    workspace.write_file('src/alpha.ts', 'export const alpha = 1;\n')
    workspace.write_file('src/beta.ts', 'export const beta = 1;\n')
    workspace.write_file('src/unrelated.ts', '// 未修改模块\n')
    originals = {path: workspace.resolve(path).read_bytes() for path in workspace.list_files()}
    patches, current = [], {path: raw.decode() for path, raw in originals.items()}
    for number, (path, old, new) in enumerate([
            ('src/alpha.ts', 'alpha = 1', 'alpha = 2'),
            ('src/beta.ts', 'beta = 1', 'beta = 2'),
            ('src/alpha.ts', 'alpha = 2', 'alpha = 3')], 1):
        after = current[path].replace(old, new, 1)
        patches.append({'id': f'P{number}', 'path': path, 'expected_sha256': digest(current[path]),
                        'old': old, 'new': new, 'after_sha256': digest(after)})
        current[path] = after
    proposal = {'run_id': rid, 'source_baseline_sha256': workspace.snapshot()['sha256'], 'patches': patches}
    return store, workspace, rid, proposal, originals


def approve(store, rid, proposal):
    sha = store.write(rid, 'proposed-repairs.json', proposal)
    store.write(rid, 'reviewed-repair-decision.json', {'decision': 'approve', 'model': 'qwen3.8-max',
        'proposal_sha256': sha, 'source_baseline_sha256': proposal['source_baseline_sha256']})
    return sha


def source_bytes(workspace):
    return {path: workspace.resolve(path).read_bytes() for path in workspace.list_files()}


def test_complete_hash_chain_is_idempotent_and_never_changes_task_or_acceptance(integration):
    store, workspace, rid, proposal, originals = integration
    store.write(rid, 'project-validation.json', {'passed': False, 'tests': [{'id': 'T01', 'status': 'not_run'}]})
    evidence = store.path(rid, 'project-validation.json').read_bytes()
    before = store.get(rid)
    sha = approve(store, rid, proposal)
    result = apply_reviewed_repairs(store, workspace, rid, sha, actor='codex_integration')
    assert result['status'] == 'applied' and result['official_acceptance'] is False
    assert result['reviewer_model'] == 'qwen3.8-max' and result['actor'] == 'codex_integration'
    assert workspace.read_file('src/alpha.ts') == 'export const alpha = 3;\n'
    assert workspace.read_file('src/beta.ts') == 'export const beta = 2;\n'
    after = store.get(rid)
    assert {key: before[key] for key in ['status', 'stage', 'error', 'calls', 'tokens', 'approved_version']} == {
        key: after[key] for key in ['status', 'stage', 'error', 'calls', 'tokens', 'approved_version']}
    assert store.path(rid, 'project-validation.json').read_bytes() == evidence
    journal = store.read_json(rid, result['journal'])
    assert journal['status'] == 'complete' and journal['source_after_expected'] == workspace.snapshot()['sha256']
    assert journal['baseline_manifest']['sha256'] == proposal['source_baseline_sha256']
    again = apply_reviewed_repairs(store, workspace, rid, sha, actor='codex_integration')
    assert again['status'] == 'already_applied' and again['official_acceptance'] is False
    assert len([event for event in store.events(rid) if event['kind'] == 'reviewed_repairs_applied']) == 1


@pytest.mark.parametrize('defect', ['stale_second', 'wrong_output', 'nonunique', 'duplicate_id'])
def test_any_invalid_patch_prevents_all_source_mutation(integration, defect):
    store, workspace, rid, proposal, originals = integration
    if defect == 'stale_second':
        proposal['patches'][1]['expected_sha256'] = '0' * 64
    elif defect == 'wrong_output':
        proposal['patches'][1]['after_sha256'] = '0' * 64
    elif defect == 'nonunique':
        proposal['patches'][1]['old'] = 'not in source'
    else:
        proposal['patches'][1]['id'] = proposal['patches'][0]['id']
    sha = approve(store, rid, proposal)
    with pytest.raises(ValueError):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals
    assert not list(store.directory(rid).glob('reviewed-repair-*.journal.json'))


@pytest.mark.parametrize('field', ['proposal_sha256', 'source_baseline_sha256', 'decision', 'model'])
def test_wrong_approval_binding_is_rejected(integration, field):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    approval = store.read_json(rid, 'reviewed-repair-decision.json')
    approval[field] = '' if field == 'model' else 'reject' if field == 'decision' else '0' * 64
    store.write(rid, 'reviewed-repair-decision.json', approval)
    with pytest.raises(ValueError, match='independent model review'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals


@pytest.mark.parametrize('path', ['../outside.ts', '/tmp/outside.ts', 'package.json', 'src/../../outside.ts'])
def test_escape_or_protected_path_is_rejected_before_first_valid_patch(integration, path):
    store, workspace, rid, proposal, originals = integration
    proposal['patches'][1]['path'] = path
    sha = approve(store, rid, proposal)
    with pytest.raises(ValueError):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals


@pytest.mark.parametrize('status', ['queued', 'running', 'passed'])
def test_active_or_completed_task_is_not_mutated(integration, status):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    store.update(rid, status=status)
    with pytest.raises(RuntimeError, match='stopped project'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals


def test_same_flock_as_coding_agent_prevents_another_source_owner(integration):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    with CodingAgent(None, store, workspace)._workspace_lock(rid, 'other-feature', 'other-owner'):
        with pytest.raises(RuntimeError, match='already has a coding owner'):
            apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals


@pytest.mark.parametrize('timing', ['before', 'after'])
def test_second_file_crash_replays_only_remaining_before_or_after_states(integration, monkeypatch, timing):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    real_write = workspace.write_file
    crashed, writes = False, []

    def interrupt(path, content):
        nonlocal crashed
        if path == 'src/beta.ts' and not crashed and timing == 'before':
            crashed = True
            raise RuntimeError('simulated crash before second atomic write')
        real_write(path, content)
        writes.append(path)
        if path == 'src/beta.ts' and not crashed:
            crashed = True
            raise RuntimeError('simulated crash after second atomic write')

    monkeypatch.setattr(workspace, 'write_file', interrupt)
    with pytest.raises(RuntimeError, match='simulated crash'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    journal_name = 'reviewed-repair-' + sha[:20] + '.journal.json'
    intent = store.read_json(rid, journal_name)
    assert intent['status'] == 'pending'
    assert intent['files'][1]['before'] == originals['src/beta.ts'].decode()
    assert intent['files'][1]['after'] == 'export const beta = 2;\n'
    result = apply_reviewed_repairs(store, workspace, rid, sha)
    assert result['status'] == 'applied'
    assert writes.count('src/alpha.ts') == writes.count('src/beta.ts') == 1


@pytest.mark.parametrize('path', ['src/alpha.ts', 'src/unrelated.ts', 'src/new-file.ts'])
def test_complete_replay_rejects_modified_or_added_source_anywhere(integration, path):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    apply_reviewed_repairs(store, workspace, rid, sha)
    workspace.write_file(path, 'export const externallyModified = true;')
    before = source_bytes(workspace)
    with pytest.raises(ValueError, match='drift'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == before


def test_pending_replay_checks_untouched_files_before_writing_remaining_target(integration, monkeypatch):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    real_write = workspace.write_file

    def interrupt(path, content):
        if path == 'src/beta.ts':
            raise RuntimeError('before second file')
        real_write(path, content)

    monkeypatch.setattr(workspace, 'write_file', interrupt)
    with pytest.raises(RuntimeError):
        apply_reviewed_repairs(store, workspace, rid, sha)
    monkeypatch.setattr(workspace, 'write_file', real_write)
    workspace.write_file('src/unrelated.ts', '// external source drift')
    with pytest.raises(ValueError, match='Unchanged source drift'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert workspace.resolve('src/beta.ts').read_bytes() == originals['src/beta.ts']


def test_resume_race_is_rechecked_inside_workspace_lock(integration, monkeypatch):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    real_lock = CodingAgent._workspace_lock

    @contextmanager
    def resume_before_acquired(self, *args):
        with real_lock(self, *args):
            store.update(rid, status='queued')
            yield

    monkeypatch.setattr(CodingAgent, '_workspace_lock', resume_before_acquired)
    with pytest.raises(RuntimeError, match='stopped project'):
        apply_reviewed_repairs(store, workspace, rid, sha)
    assert source_bytes(workspace) == originals


def test_sqlite_fence_blocks_resume_during_atomic_source_writes(integration, monkeypatch):
    store, workspace, rid, proposal, originals = integration
    sha = approve(store, rid, proposal)
    real_write = workspace.write_file
    blocked = []

    def attempt_resume(path, content):
        con = sqlite3.connect(store.db, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                con.execute("UPDATE runs SET status='queued' WHERE id=?", (rid,))
            blocked.append(path)
        finally:
            con.close()
        real_write(path, content)

    monkeypatch.setattr(workspace, 'write_file', attempt_resume)
    assert apply_reviewed_repairs(store, workspace, rid, sha)['status'] == 'applied'
    assert blocked == ['src/alpha.ts', 'src/beta.ts']
    assert store.get(rid)['status'] == 'failed'
