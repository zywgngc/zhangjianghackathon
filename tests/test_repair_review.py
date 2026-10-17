"""Source review controls use mock decisions, never official game evidence."""
import hashlib

import pytest

from game_harness.project_runtime import ProjectWorkspace
from game_harness.store import Store
from scripts.review_repairs import review_repairs, verified_inputs


@pytest.fixture
def repair(settings):
    store = Store(settings.data_dir)
    rid = store.create({'mode': 'project', 'engine': 'phaser', 'description': 'source review control'})['id']
    store.update(rid, status='failed')
    workspace = ProjectWorkspace(store.directory(rid) / 'project', 'phaser')
    workspace.initialize()
    old = workspace.read_file('src/style.css')
    digest = hashlib.sha256(old.encode()).hexdigest()
    evidence_sha = store.write(rid, 'independent-observation.json', {'observed_damage': 4, 'official_acceptance': False})
    proposal = {'run_id': rid, 'source_baseline_sha256': workspace.snapshot()['sha256'],
        'patches': [{'id': 'fix-style', 'path': 'src/style.css', 'old': old, 'new': old + '\nbody {color: white;}\n',
                     'expected_sha256': digest}],
        'evidence': [{'path': 'independent-observation.json', 'sha256': evidence_sha}]}
    store.write(rid, 'proposed-repairs.json', proposal)
    return store, workspace, rid, proposal


@pytest.mark.parametrize('defect', ['active', 'evidence_hash', 'patch', 'baseline', 'escape'])
async def test_invalid_proposal_never_spends_a_model_call_or_changes_source(settings, repair, defect):
    store, workspace, rid, proposal = repair
    before = workspace.snapshot()
    if defect == 'active':
        store.update(rid, status='running')
    elif defect == 'evidence_hash':
        store.write(rid, 'independent-observation.json', {'observed_damage': 400})
    elif defect == 'patch':
        proposal['patches'][0]['old'] = 'not in current source'
    elif defect == 'baseline':
        proposal['source_baseline_sha256'] = '0' * 64
    else:
        proposal['evidence'][0]['path'] = '../.env'
    store.write(rid, 'proposed-repairs.json', proposal)
    def forbidden(*args):
        pytest.fail('Invalid preflight must not construct a model client')
    with pytest.raises((ValueError, KeyError)):
        await review_repairs(settings, rid, apply=True, agents_factory=forbidden)
    assert workspace.snapshot() == before and store.get(rid)['calls'] == 0
    assert not store.path(rid, 'reviewed-repair-decision.json').exists()


@pytest.mark.parametrize('verdict', ['approve', 'reject'])
async def test_read_only_review_receives_actual_verified_observations_without_changing_acceptance(settings, repair, verdict):
    store, workspace, rid, _ = repair
    before = workspace.snapshot()
    calls = []
    class Reviewer:
        def __init__(self, settings, store):
            pass
        async def close(self):
            pass
        async def structured(self, run_id, stage, role, instruction, payload, schema, **kwargs):
            assert payload['verified_evidence'][0]['observations']['observed_damage'] == 4
            assert role == 'qa' and payload['official_acceptance'] is False
            calls.append(payload)
            return schema.model_validate({'decision': verdict, 'proposal_sha256': payload['proposal_sha256'],
                'source_baseline_sha256': payload['current_source_sha256'],
                'rationale': 'Control decision based on the exact source diff and verified observation; no game acceptance.',
                'remaining_risks': ['Mock reviewer, not actual game QA.']})
    result = await review_repairs(settings, rid, agents_factory=Reviewer)
    assert result['decision'] == verdict and result['verified_evidence_count'] == 1
    assert result['official_acceptance'] is False and len(calls) == 1
    assert workspace.snapshot() == before and store.get(rid)['status'] == 'failed'
    assert not store.path(rid, 'manifest.json').exists()
    assert store.read_json(rid, 'reviewed-repair-decision.json')['proposal_sha256'] == calls[0]['proposal_sha256']
