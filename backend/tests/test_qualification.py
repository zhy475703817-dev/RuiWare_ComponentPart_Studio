"""Real CAD, isolated DB: automatic execution, atomic repair, and hard publish gate."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.config import Settings
from backend.app.components.models import ComponentAction, ComponentDocument
from backend.app.components.qualification_policy import QualificationConfig, continuation_dimensions, digest, scenario_plan
from backend.app.components.resize_solver import ResizeFailure
from backend.app.components.repository import DraftRepository
from backend.tests.test_geometry_resize import BoxProvider, document as geometry_document


def document(members=None):
    """Geometry qualification fixture with explicitly completed hardware declarations."""
    data = geometry_document(members)
    for member in data['assembly']['members']:
        for item in member['interfaces']:
            item['hardware'] = {'mode': 'none', 'scenarioId': 'plane-contact', 'items': [],
                                'note': '测试夹具仅作几何接触，无额外五金'}
    return data


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(Settings(draft_db=Path(self.temp)/'test.sqlite'), dist_dir=None)
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8020', client=('127.0.0.1', 12345)))
        self.app.state.qualification_jobs.debounce = .08
        self.enterContext(patch('backend.app.components.resize_jobs.CadProvider', side_effect=lambda _: BoxProvider()))
        self.enterContext(patch('backend.app.reconstruction._read_template', new=AsyncMock(return_value=(SimpleNamespace(templateId='box', version=1), {}, {}))))

    def create(self, doc=None):
        result = self.client.post('/api/component-drafts', json={'document': doc or document()})
        self.assertEqual(result.status_code, 200, result.text)
        draft = result.json(); self.base = '/api/component-drafts/'+draft['draftId']
        return draft

    def wait(self, terminal=True):
        end = time.monotonic()+25
        while time.monotonic() < end:
            result = self.client.get(self.base+'/qualification').json()
            if result['status'] not in ('queued', 'pending', 'running', 'stale') or not terminal and result['status'] == 'running': return result
            time.sleep(.03)
        self.fail('qualification timeout: '+str(result))

    def test_creation_automatically_qualifies_and_preserves_nominal_size(self):
        original = self.create()
        result = self.wait()
        self.assertTrue(result['deliveryReady'], result)
        self.assertEqual(result['axes'], [2])
        self.assertEqual(len(result['cases']), 3)
        current = self.client.get(self.base).json()
        self.assertEqual(current['document']['revision'], 2)
        self.assertAlmostEqual(current['document']['assembly']['members'][0]['parameterValues']['length'], 100, delta=.001)
        self.assertEqual(original['document']['assembly']['members'][0]['parameterValues']['length'], 100)
        self.assertTrue(current['document']['geometryDesign']['verified'])
        snapshot = self.app.state.drafts.prepare_publication(original['draftId'], 2)
        self.assertEqual(snapshot['document'], current['document'])
        restored = DraftRepository(self.app.state.drafts.path).qualification(original['draftId'])
        self.assertTrue(restored['deliveryReady'])

    def test_missing_or_imported_verification_cannot_bypass_publish(self):
        self.app.state.qualification_jobs.debounce = 100
        source = document(); source['geometryDesign'] = {'dimensions': [10, 10, 100], 'verified': True}
        draft = self.create(source)
        response = self.client.post(self.base+'/publish', json={'expectedRevision': 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'AUTHORING_QUALIFICATION_REQUIRED')
        with self.assertRaisesRegex(Exception, 'AUTHORING_QUALIFICATION_REQUIRED'):
            self.app.state.drafts.prepare_publication(draft['draftId'], 1)

    def test_changed_verification_policy_requires_new_certificate(self):
        self.create()
        self.assertTrue(self.wait()['deliveryReady'])
        with patch('backend.app.components.qualification_policy.QUALIFICATION_VERSION', 'future-policy'):
            current = self.client.get(self.base+'/qualification').json()
            self.assertEqual(current['status'], 'stale')
            self.assertFalse(current['deliveryReady'])
            response = self.client.post(self.base+'/publish', json={'expectedRevision': current['revision']})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'AUTHORING_QUALIFICATION_REQUIRED')

    def test_edit_and_range_change_immediately_invalidate_certificate(self):
        self.create(); result = self.wait(); self.assertTrue(result['deliveryReady'], result)
        self.app.state.qualification_jobs.debounce = 100
        current = self.client.get(self.base).json()
        edited = self.client.post(self.base+'/actions', json={'expectedRevision': current['document']['revision'], 'action': {'type': 'definition.patch', 'patch': {'name': 'Edited'}}})
        self.assertEqual(edited.status_code, 200)
        self.assertFalse(self.client.get(self.base+'/qualification').json()['deliveryReady'])
        self.assertEqual(self.client.post(self.base+'/publish', json={'expectedRevision': edited.json()['document']['revision']}).status_code, 409)
        configured = self.client.put(self.base+'/qualification/config', json={'expectedRevision': edited.json()['document']['revision'], 'config': {'axes': [2], 'targets': [[10, 10, 150]]}})
        self.assertEqual(configured.status_code, 200)
        self.assertFalse(configured.json()['deliveryReady'])

    def test_unreachable_contract_fails_without_saving_trial_or_dropping_case(self):
        self.app.state.qualification_jobs.debounce = .3
        original = self.create()
        configured = self.client.put(self.base+'/qualification/config', json={'expectedRevision': 1, 'config': {'axes': [2], 'minimumDimensions': [10, 10, 90], 'maximumDimensions': [10, 10, 250]}})
        self.assertEqual(configured.status_code, 200)
        result = self.wait()
        self.assertEqual(result['status'], 'failed', result)
        self.assertTrue(any(c['status'] == 'failed' and c['dimensions'][2] == 250 for c in result['cases']))
        self.assertEqual(self.client.get(self.base).json()['document'], original['document'])
        self.assertEqual(self.client.post(self.base+'/publish', json={'expectedRevision': 1}).status_code, 409)
        rejected = self.client.post(self.base+'/resize-jobs', json={'expectedRevision': 1, 'dimensions': [10, 10, 300]}).json()
        self.assertEqual(rejected['error']['code'], 'RESIZE_OUTSIDE_CONTRACT')

    def test_midpoint_retry_repairs_case_without_unlocking_or_overwriting_nominal(self):
        from backend.app.components.resize_solver import ResizeSolver
        solve = ResizeSolver.solve
        def needs_continuation(solver):
            target = solver.request.get('dimensions')
            length = solver.document['assembly']['members'][0]['parameterValues']['length']
            if target and abs(target[2]-length) > 6:
                raise ResizeFailure('需要分步求解', 'RESIZE_NO_SOLUTION')
            return solve(solver)
        with patch('backend.app.components.qualification.ResizeSolver.solve', needs_continuation):
            self.create(); result = self.wait()
        self.assertTrue(result['deliveryReady'], result)
        self.assertEqual(result['automaticRecoveries'], 2)
        self.assertTrue(all(c.get('attempts') == 3 for c in result['cases'][1:]))
        self.assertAlmostEqual(self.client.get(self.base).json()['document']['assembly']['members'][0]['parameterValues']['length'], 100, delta=.001)

    def test_automatic_recovery_selects_legal_grid_before_running_cad(self):
        from backend.app.components.resize_solver import ResizeSolver
        solve = ResizeSolver.solve
        targets = []
        def constrained_continuation(solver):
            target = solver.request.get('dimensions')
            length = solver.document['assembly']['members'][0]['parameterValues']['length']
            if target:
                targets.append(target[2])
                if abs(target[2] / 2 - round(target[2] / 2)) > 1e-6:
                    raise ResizeFailure('中间尺寸不能完整分格', 'BRACE_RULE_REMAINDER')
                if abs(target[2] - length) > 6:
                    raise ResizeFailure('需要分步求解', 'RESIZE_NO_SOLUTION')
            return solve(solver)
        # Inject a two-mm height contract while retaining real CAD execution,
        # background jobs, independent trials and atomic nominal persistence.
        with patch('backend.app.components.qualification_policy.brace_height_grids', return_value=[(0, 2)]), \
             patch('backend.app.components.qualification.ResizeSolver.solve', constrained_continuation):
            self.create()
            result = self.wait()
        self.assertTrue(result['deliveryReady'], result)
        self.assertEqual(result['automaticRecoveries'], 2)
        self.assertTrue(all(abs(z / 2 - round(z / 2)) < 1e-6 for z in targets))
        self.assertAlmostEqual(self.client.get(self.base).json()['document']['assembly']['members'][0]['parameterValues']['length'], 100, delta=.001)

    def test_edit_during_solver_cannot_commit_old_candidate(self):
        from backend.app.components.resize_solver import ResizeSolver
        entered, release = threading.Event(), threading.Event()
        solve = ResizeSolver.solve
        def slow(solver):
            entered.set(); release.wait(5)
            return solve(solver)
        with patch('backend.app.components.qualification.ResizeSolver.solve', slow):
            self.create(); self.assertTrue(entered.wait(5))
            self.app.state.qualification_jobs.debounce = 100
            response = self.client.post(self.base+'/actions', json={'expectedRevision': 1, 'action': {'type': 'definition.patch', 'patch': {'name': 'User edit wins'}}})
            release.set(); self.assertEqual(response.status_code, 200)
            time.sleep(.15)
        current = self.client.get(self.base).json()['document']
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['definition']['name'], 'User edit wins')
        self.assertFalse(self.client.get(self.base+'/qualification').json()['deliveryReady'])

    def test_cancel_does_not_save_or_certify_and_retry_can_complete(self):
        self.app.state.qualification_jobs.debounce = 100
        original = self.create()
        stopped = self.client.delete(self.base+'/qualification').json()
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertEqual(self.client.get(self.base).json()['document'], original['document'])
        self.client.post(self.base+'/qualification', json={'expectedRevision': 1, 'retry': True})
        self.assertTrue(self.wait()['deliveryReady'])

    def test_same_failed_target_is_prechecked_but_retry_and_changed_design_are_allowed(self):
        from backend.app.components.qualification_policy import design_key
        self.app.state.qualification_jobs.debounce = 100
        draft = self.create()
        repo = self.app.state.drafts
        target = [10., 10., 150.]
        repo.record_resize_failure(draft['draftId'], 1, design_key(draft['document']), target,
                                   {'code': 'RESIZE_NO_SOLUTION', 'message': '此前失败', 'details': []})
        failed = self.client.post(self.base+'/resize-jobs', json={'expectedRevision': 1, 'dimensions': target}).json()
        self.assertEqual(failed['error']['code'], 'RESIZE_KNOWN_FAILURE')
        self.assertIsNone(repo.check_resize_target(draft['draftId'], draft['document'], target, {'locks': [{'path': ['a']}], 'relations': []}))
        retry = self.client.post(self.base+'/resize-jobs', json={'expectedRevision': 1, 'dimensions': target, 'retryFailedTarget': True}).json()
        deadline = time.monotonic()+10
        while retry['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.03); retry = self.client.get(self.base+'/resize-jobs/'+retry['jobId']).json()
        self.assertEqual(retry['status'], 'ready', retry)
        self.assertIsNone(repo.check_resize_target(draft['draftId'], draft['document'], target))

    def test_locked_length_is_never_released_to_pass_declared_range(self):
        self.app.state.qualification_jobs.debounce = .3
        source = document()
        source['geometryDesign'] = {'dimensions': [10, 10, 100], 'locks': [{'path': ['a']}], 'relations': []}
        original = self.create(source)
        self.client.put(self.base+'/qualification/config', json={'expectedRevision': 1, 'config': {'axes': [2]}})
        result = self.wait()
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(result['deliveryReady'])
        self.assertEqual(self.client.get(self.base).json()['document'], original['document'])

    def test_interface_hardware_and_member_metadata_survive_automatic_rebuild(self):
        source = document()
        hardware = {'mode': 'manual', 'items': []}
        source['assembly']['members'][0]['interfaces'][0]['hardware'] = hardware
        source['assembly']['members'][0]['planarMovement'] = True
        self.create(source)
        result = self.wait()
        self.assertEqual(result['status'], 'passed', result)
        # This fixture has a reserved port only, with no connection to require a kit.
        self.assertTrue(result['deliveryReady'])
        self.assertTrue(result['hardwareSelection']['complete'])
        saved = self.client.get(self.base).json()['document']['assembly']['members'][0]
        self.assertEqual(saved['interfaces'][0]['hardware'], hardware)
        self.assertTrue(saved['planarMovement'])

    def test_automatic_qualification_preserves_undo_of_user_resize(self):
        self.app.state.qualification_jobs.debounce = 100
        original = self.create()
        job = self.client.post(self.base+'/resize-jobs', json={'expectedRevision': 1, 'dimensions': [10, 10, 150]}).json()
        deadline = time.monotonic()+10
        while job['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.03); job = self.client.get(self.base+'/resize-jobs/'+job['jobId']).json()
        self.assertEqual(job['status'], 'ready', job)
        self.client.delete(self.base+'/qualification')
        self.app.state.qualification_jobs.debounce = .08
        applied = self.client.post(self.base+'/resize-apply', json={'expectedRevision': 1, 'jobId': job['jobId'], 'checksum': job['result']['checksum']})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertTrue(self.wait()['deliveryReady'])
        undo = self.client.get(self.base+'/resize-history').json()['undo']
        restored = self.client.post(self.base+'/resize-undo', json={'expectedRevision': undo['revision'], 'jobId': undo['jobId'], 'checksum': undo['checksum']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['document']['assembly'], original['document']['assembly'])

    def test_changed_contract_cannot_receive_old_running_certificate(self):
        from backend.app.components.resize_solver import ResizeSolver
        entered, release = threading.Event(), threading.Event()
        solve = ResizeSolver.solve
        def slow(solver):
            entered.set(); release.wait(5)
            return solve(solver)
        with patch('backend.app.components.qualification.ResizeSolver.solve', slow):
            self.create(); self.assertTrue(entered.wait(5))
            self.app.state.qualification_jobs.debounce = 100
            changed = self.client.put(self.base+'/qualification/config', json={
                'expectedRevision': 1, 'config': {'axes': [2], 'targets': [[10, 10, 250]]}})
            self.assertEqual(changed.status_code, 200)
            release.set(); time.sleep(.15)
        self.assertEqual(self.client.get(self.base).json()['document']['revision'], 1)
        self.assertFalse(self.client.get(self.base+'/qualification').json()['deliveryReady'])

    def test_restart_resumes_persisted_interrupted_job(self):
        self.app.state.qualification_jobs.debounce = 100
        draft = self.create()
        queued = self.client.get(self.base+'/qualification').json()
        self.assertEqual(queued['status'], 'queued')
        self.client.__exit__(None, None, None)
        resumed = create_app(Settings(draft_db=Path(self.temp)/'test.sqlite'), dist_dir=None)
        with TestClient(resumed) as client:
            old_client, self.client = self.client, client
            try:
                result = self.wait()
                self.assertTrue(result['deliveryReady'], result)
                self.assertNotEqual(queued['jobId'], result['jobId'])
                self.assertEqual(client.get(self.base).json()['document']['revision'], 2)
            finally: self.client = old_client

    def test_mcp_finish_configure_and_status_share_server_certificate(self):
        self.app.state.qualification_jobs.debounce = 100
        draft = self.create()
        def call(name, arguments):
            response = self.client.post('/mcp', headers={'Accept': 'application/json, text/event-stream', 'Host': '127.0.0.1:8020'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['result']
        configured = call('component_configure_authoring', {'draftId': draft['draftId'], 'expectedRevision': 1, 'config': {'axes': [2]}})
        self.assertFalse(configured.get('isError'), configured)
        finish = call('component_finish_authoring', {'draftId': draft['draftId'], 'expectedRevision': 1, 'retry': True})
        self.assertFalse(finish.get('isError'), finish)
        # An already queued job remains debounced; explicitly cancel then retry to run now.
        self.client.delete(self.base+'/qualification')
        call('component_finish_authoring', {'draftId': draft['draftId'], 'expectedRevision': 1, 'retry': True})
        self.assertTrue(self.wait()['deliveryReady'])
        status = call('component_authoring_status', {'draftId': draft['draftId']})['structuredContent']
        self.assertTrue(status['deliveryReady'])
        stale = call('component_finish_authoring', {'draftId': draft['draftId'], 'expectedRevision': 1})
        self.assertEqual(stale['structuredContent']['error']['code'], 'REVISION_CONFLICT')


class ScenarioPlanTests(unittest.TestCase):
    @staticmethod
    def brace_document(steps=(150,), policy='reject'):
        return {
            'parameters': [{'id': 'bottom', 'value': 100}, {'id': 'top', 'value': 200},
                           *[{'id': f'pitch{i}', 'value': step} for i, step in enumerate(steps)]],
            'braceGenerationRules': [
                {'bottomOffsetParameterId': 'bottom', 'topClearanceParameterId': 'top',
                 'pitchParameterId': f'pitch{i}', 'remainderPolicy': policy}
                for i in range(len(steps))],
        }

    def test_automatic_brace_trials_use_whole_bays_in_both_directions(self):
        cases, lower, upper = scenario_plan([1216, 114, 2400], [2], QualificationConfig().model_dump(), self.brace_document())
        self.assertEqual(lower[2], 2100)
        self.assertEqual(upper[2], 2700)
        self.assertEqual([c['dimensions'][2] for c in cases], [2100, 2700])

    def test_continuation_uses_common_legal_grid_and_keeps_final_request(self):
        source, target = [1216, 114, 3000], [1216, 114, 1950]
        midpoint = continuation_dimensions(source, target, self.brace_document())
        self.assertEqual(midpoint, [1216, 114, 2400])
        self.assertEqual(target, [1216, 114, 1950])
        self.assertEqual(continuation_dimensions(target, source, self.brace_document()), midpoint)
        self.assertEqual(continuation_dimensions([100, 50, 3300], [200, 50, 1500],
                         self.brace_document((150, 200))), [150, 50, 2100])
        self.assertEqual(continuation_dimensions(source, target, self.brace_document(policy='top-clearance')),
                         [1216, 114, 2475])

    def test_adjacent_grid_heights_keep_original_failure_or_advance_other_axes(self):
        document = self.brace_document()
        self.assertIsNone(continuation_dimensions([100, 50, 2400], [100, 50, 2250], document))
        self.assertEqual(continuation_dimensions([100, 50, 2400], [200, 50, 2250], document),
                         [150, 50, 2400])
        self.assertIsNone(continuation_dimensions([100, 50, 2400], [100, 50, 2249], document))
        self.assertIsNone(continuation_dimensions([100, 50, 2475], [100, 50, 2250], document))
        self.assertIsNone(continuation_dimensions([100, 50, 2400], [100, 50, 2400], document))
        self.assertEqual(continuation_dimensions([100, 50, 2400], [200, 50, 2400], document),
                         [150, 50, 2400])

    def test_brace_trials_respect_all_rule_grids_and_combination_cases(self):
        cases, _, _ = scenario_plan([100, 50, 2100], [0, 2], QualificationConfig().model_dump(), self.brace_document((150, 200)))
        self.assertEqual({c['dimensions'][2] for c in cases}, {1500, 2100, 2700})
        self.assertEqual(len(cases), 8)

    def test_explicit_unsupported_brace_height_is_never_rewritten(self):
        config = QualificationConfig(minimumDimensions=[100, 50, 2160], maximumDimensions=[100, 50, 2640], targets=[[100, 50, 2500]])
        cases, lower, upper = scenario_plan([100, 50, 2400], [2], config.model_dump(), self.brace_document())
        self.assertEqual((lower[2], upper[2]), (2160, 2640))
        self.assertEqual([c['dimensions'][2] for c in cases], [2160, 2640, 2500])

    def test_brace_remainder_policy_keeps_continuous_height_samples(self):
        _, lower, upper = scenario_plan([100, 50, 2400], [2], QualificationConfig().model_dump(), self.brace_document(policy='top-clearance'))
        self.assertEqual((lower[2], upper[2]), (2160, 2640))

    def test_boundary_probes_skip_only_heights_forbidden_by_brace_rule(self):
        from backend.app.components.qualification import QualificationJobs, np
        member = {'id': 'left', 'template': {}, 'parameterValues': {}}
        geometry = SimpleNamespace(bounds=np.array([[0, 0, 0], [10, 10, 2400]]), resolved=[])
        provider = SimpleNamespace(evaluate=lambda *_: geometry,
                                   generation_margins=lambda *_: {'hole': [0, 200]})
        solver = SimpleNamespace(document=self.brace_document(),
                                 leaves=[{'member': member, 'path': ('left',), 'world': np.eye(4)}])
        baseline = {'relations': [], 'dimensions': [100, 50, 2400]}
        cases = []
        QualificationJobs.add_boundary_cases(cases, baseline, solver, provider, [2], [100, 50, 2100], [100, 50, 2700])
        self.assertEqual(cases, [])
        solver.document = self.brace_document(policy='top-clearance')
        QualificationJobs.add_boundary_cases(cases, baseline, solver, provider, [2], [100, 50, 2100], [100, 50, 2700])
        self.assertEqual(len(cases), 2)

    def test_extra_targets_must_match_explicit_contract(self):
        with self.assertRaises(ValueError):
            QualificationConfig(minimumDimensions=[10, 10, 90], maximumDimensions=[10, 10, 110], targets=[[10, 10, 150]])

    def test_all_axes_and_mixed_corners_are_mandatory(self):
        cases, lower, upper = scenario_plan([100, 50, 200], [0, 1, 2], QualificationConfig().model_dump())
        self.assertEqual(len(cases), 14)
        self.assertEqual(lower, [90, 45, 180])
        self.assertIn([90, 55.00000000000001, 180], [c['dimensions'] for c in cases])

    def test_nominal_outside_declared_range_is_not_silently_reduced(self):
        with self.assertRaises(ValueError):
            scenario_plan([100, 50, 200], [2], QualificationConfig(minimumDimensions=[100, 50, 210], maximumDimensions=[100, 50, 250]).model_dump())


if __name__ == '__main__': unittest.main()
