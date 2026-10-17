"""Real workspace integration safeguards; none of these fixtures are game evidence."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

import pytest

from game_harness.coding_agent import CodingAgent
from game_harness.project_runtime import ProjectWorkspace
from game_harness.reviewed_repairs import apply_reviewed_repairs, digest
import game_harness.reviewed_repairs as repairs


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
    assert journal['version'] == 2 and journal['status'] == 'complete' and journal['source_after_expected'] == workspace.snapshot()['sha256']
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


def add_creation(proposal, *, path='src/nested/new-module.ts', content='export const created = 1;\n'):
    patch={'id':'create-module', 'operation':'create', 'path':path, 'expected_sha256':None,
           'old':'', 'new':content, 'after_sha256':digest(content)}
    proposal['patches'].insert(0,patch)
    return patch


def test_create_and_subsequent_replace_replay_sorted_manifest_without_acceptance(integration):
    store, workspace, rid, proposal, originals=integration
    created=add_creation(proposal)
    new=created['new'].replace('= 1','= 2')
    proposal['patches'].append({'id':'refine-created-module','path':created['path'],
        'expected_sha256':created['after_sha256'],'old':'= 1','new':'= 2','after_sha256':digest(new)})
    expected=repairs._expected_after(workspace.snapshot(),repairs._plan(proposal,lambda p:workspace.resolve(p,writable=True).read_text()))
    proposal['expected_patched_source_sha256']=expected
    state_before=store.get(rid)
    validation=store.write(rid,'project-validation.json',{'passed':False,'tests':[{'id':'T1','status':'not_run'}]})
    sha=approve(store,rid,proposal)
    result=apply_reviewed_repairs(store,workspace,rid,sha,actor='attributed_module_author')
    assert result['source_after']==workspace.snapshot()['sha256']==expected
    assert workspace.read_file(created['path'])==new
    journal=store.read_json(rid,result['journal'])
    assert journal['version']==3 and journal['files'][0]['before'] is None
    assert journal['files'][0]['before_sha256'] is None and journal['files'][0]['after_sha256']==digest(new)
    assert [r['path'] for r in workspace.snapshot()['files']]==sorted(r['path'] for r in workspace.snapshot()['files'])
    assert apply_reviewed_repairs(store,workspace,rid,sha,actor='attributed_module_author')['status']=='already_applied'
    assert {k:v for k,v in store.get(rid).items() if k!='artifacts'}=={k:v for k,v in state_before.items() if k!='artifacts'}
    assert hashlib.sha256(store.path(rid,'project-validation.json').read_bytes()).hexdigest()==validation
    assert len([e for e in store.events(rid) if e['kind']=='reviewed_repairs_applied'])==1
    assert not list((workspace.root/'.harness').glob('reviewed-create-*.tmp'))


@pytest.mark.parametrize('defect',['expected_hash','missing_expected','old','empty_new','missing_after','after_hash','unknown_operation','operation_object','existing_target','second_create'])
def test_malformed_creation_prevents_every_source_write(integration,defect):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal)
    if defect=='expected_hash':c['expected_sha256']=digest('')
    elif defect=='missing_expected':del c['expected_sha256']
    elif defect=='old':c['old']='existing text'
    elif defect=='empty_new':c['new']='';c['after_sha256']=digest('')
    elif defect=='missing_after':del c['after_sha256']
    elif defect=='after_hash':c['after_sha256']='0'*64
    elif defect=='unknown_operation':c['operation']='delete'
    elif defect=='operation_object':c['operation']=['create']
    elif defect=='existing_target':c['path']='src/alpha.ts'
    else:proposal['patches'].append({**c,'id':'duplicate-creation'})
    sha=approve(store,rid,proposal)
    with pytest.raises(ValueError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==originals
    assert not list(store.directory(rid).glob('reviewed-repair-*.journal.json'))


@pytest.mark.parametrize('path',['../new.ts','/tmp/new.ts','package.json','package-lock.json','tsconfig.json','vite.config.mjs','src/.hidden/new.ts','src/new.sh','node_modules/new.ts'])
def test_creation_obeys_workspace_source_and_toolchain_boundaries(integration,path):
    store,workspace,rid,proposal,originals=integration
    add_creation(proposal,path=path)
    sha=approve(store,rid,proposal)
    with pytest.raises(ValueError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==originals


@pytest.mark.parametrize('kind',['file','parent'])
def test_creation_cannot_follow_existing_symlinks(integration,tmp_path,kind):
    store,workspace,rid,proposal,originals=integration
    outside=tmp_path/'outside';outside.mkdir();marker=outside/'marker.ts';marker.write_text('outside original')
    if kind=='file':
        target=workspace.root/'src/new.ts';target.symlink_to(marker);path='src/new.ts'
    else:
        target=workspace.root/'src/alias';target.symlink_to(outside,target_is_directory=True);path='src/alias/new.ts'
    add_creation(proposal,path=path)
    sha=approve(store,rid,proposal)
    with pytest.raises(ValueError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert marker.read_text()=='outside original' and not (outside/'new.ts').exists()
    assert workspace.resolve('src/alpha.ts').read_bytes()==originals['src/alpha.ts']


@pytest.mark.parametrize('defect',['wrong_final_manifest','file_quota','project_file_quota','project_byte_quota'])
def test_create_preflights_entire_final_manifest_and_quotas(integration,monkeypatch,defect):
    store,workspace,rid,proposal,originals=integration
    add_creation(proposal,content='x'*(repairs.MAX_FILE_BYTES+1) if defect=='file_quota' else 'export const n=1;')
    if defect=='wrong_final_manifest':proposal['expected_patched_source_sha256']='0'*64
    elif defect=='project_file_quota':monkeypatch.setattr(repairs,'MAX_FILES',len(workspace.list_files()))
    elif defect=='project_byte_quota':monkeypatch.setattr(repairs,'MAX_SOURCE_BYTES',sum(row['bytes'] for row in workspace.snapshot()['files']))
    sha=approve(store,rid,proposal)
    with pytest.raises(ValueError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==originals
    assert not list(store.directory(rid).glob('reviewed-repair-*.journal.json'))


def test_create_refuses_existing_directory_before_any_source_write(integration):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);workspace.resolve(c['path'],writable=True).mkdir(parents=True)
    sha=approve(store,rid,proposal)
    with pytest.raises(IsADirectoryError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==originals
    assert not list(store.directory(rid).glob('reviewed-repair-*.journal.json'))


@pytest.mark.parametrize('timing',['before','after'])
def test_crash_at_new_file_is_durable_and_replays_without_duplicate_creation(integration,monkeypatch,timing):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal)
    real=repairs._create_file;attempts=[];crashed=False
    def interrupt(w,path,content):
        nonlocal crashed
        if not crashed and timing=='before':crashed=True;raise RuntimeError('simulated create crash')
        real(w,path,content);attempts.append(path)
        if not crashed:crashed=True;raise RuntimeError('simulated create crash')
    monkeypatch.setattr(repairs,'_create_file',interrupt)
    with pytest.raises(RuntimeError,match='create crash'):apply_reviewed_repairs(store,workspace,rid,sha)
    intent=store.read_json(rid,'reviewed-repair-'+sha[:20]+'.journal.json')
    assert intent['status']=='pending' and intent['version']==3
    assert intent['files'][0]['operation']=='create' and intent['files'][0]['before'] is None
    assert workspace.resolve('src/alpha.ts').read_bytes()==originals['src/alpha.ts']
    assert apply_reviewed_repairs(store,workspace,rid,sha)['status']=='applied'
    assert attempts==[c['path']]
    assert workspace.read_file('src/alpha.ts')=='export const alpha = 3;\n'


def test_external_create_before_initial_intent_is_rejected_even_if_bytes_match(integration):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal)
    workspace.write_file(c['path'],c['new'])
    before=source_bytes(workspace)
    with pytest.raises(ValueError,match='source baseline'):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==before
    assert not list(store.directory(rid).glob('reviewed-repair-*.journal.json'))


@pytest.mark.parametrize('drift',['created_modified','created_removed','unrelated_added','protected_modified'])
def test_complete_new_file_replay_rejects_all_source_drift(integration,drift):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal)
    apply_reviewed_repairs(store,workspace,rid,sha)
    if drift=='created_modified':workspace.write_file(c['path'],'// external edit')
    elif drift=='created_removed':workspace.resolve(c['path']).unlink()
    elif drift=='unrelated_added':workspace.write_file('src/unreviewed.ts','// external module')
    else:(workspace.root/'package.json').write_text('{"external":true}')
    before=source_bytes(workspace)
    with pytest.raises(ValueError,match='drift'):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==before


def test_pending_created_file_drift_blocks_remaining_replacements(integration,monkeypatch):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal)
    real=repairs._create_file
    def stop(w,path,content):real(w,path,content);raise RuntimeError('after created file')
    monkeypatch.setattr(repairs,'_create_file',stop)
    with pytest.raises(RuntimeError):apply_reviewed_repairs(store,workspace,rid,sha)
    workspace.write_file(c['path'],'// conflicting external file')
    with pytest.raises(ValueError,match='Created source drift'):apply_reviewed_repairs(store,workspace,rid,sha)
    assert workspace.resolve('src/alpha.ts').read_bytes()==originals['src/alpha.ts']


def test_atomic_create_refuses_racing_external_file_without_overwrite(integration,monkeypatch):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal);link=repairs.os.link
    def external_race(source,target,**kwargs):
        workspace.write_file(c['path'],'// racing external content')
        return link(source,target,**kwargs)
    monkeypatch.setattr(repairs.os,'link',external_race)
    with pytest.raises(ValueError,match='appeared before atomic'):apply_reviewed_repairs(store,workspace,rid,sha)
    assert workspace.read_file(c['path'])=='// racing external content'
    assert workspace.resolve('src/alpha.ts').read_bytes()==originals['src/alpha.ts']
    assert not list((workspace.root/'.harness').glob('reviewed-create-*.tmp'))


def test_atomic_creation_rejects_parent_symlink_race(integration,monkeypatch,tmp_path):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal)
    outside=tmp_path/'outside-source';outside.mkdir();real_mkdir=repairs.os.mkdir
    def race(path,*args,**kwargs):
        if path=='nested' and kwargs.get('dir_fd') is not None:
            (workspace.root/'src/nested').symlink_to(outside,target_is_directory=True)
            raise FileExistsError('racing parent alias')
        return real_mkdir(path,*args,**kwargs)
    monkeypatch.setattr(repairs.os,'mkdir',race)
    with pytest.raises(OSError):apply_reviewed_repairs(store,workspace,rid,sha)
    assert not list(outside.iterdir())
    assert workspace.resolve('src/alpha.ts').read_bytes()==originals['src/alpha.ts']
    assert not list((workspace.root/'.harness').glob('reviewed-create-*.tmp'))


def test_creation_keeps_sqlite_resume_fence_during_atomic_publication(integration,monkeypatch):
    store,workspace,rid,proposal,originals=integration
    c=add_creation(proposal);sha=approve(store,rid,proposal);link=repairs.os.link;observed=[]
    def attempt_resume(source,target,**kwargs):
        con=sqlite3.connect(store.db,timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                con.execute("UPDATE runs SET status='queued' WHERE id=?",(rid,))
            observed.append(target)
        finally:con.close()
        return link(source,target,**kwargs)
    monkeypatch.setattr(repairs.os,'link',attempt_resume)
    assert apply_reviewed_repairs(store,workspace,rid,sha)['status']=='applied'
    assert observed==['new-module.ts'] and store.get(rid)['status']=='failed'


@pytest.mark.parametrize('status',['running','queued'])
def test_creation_still_requires_stopped_project_and_coding_flock(integration,status):
    store,workspace,rid,proposal,originals=integration
    add_creation(proposal);sha=approve(store,rid,proposal)
    store.update(rid,status=status)
    with pytest.raises(RuntimeError,match='stopped'):apply_reviewed_repairs(store,workspace,rid,sha)
    store.update(rid,status='failed')
    with CodingAgent(None,store,workspace)._workspace_lock(rid,'active-owner','author'):
        with pytest.raises(RuntimeError,match='coding owner'):apply_reviewed_repairs(store,workspace,rid,sha)
    assert source_bytes(workspace)==originals
