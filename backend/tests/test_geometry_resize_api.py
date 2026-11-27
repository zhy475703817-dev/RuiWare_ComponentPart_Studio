"""Exercise the actual HTTP/job/CAS boundary against an isolated repository."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.components.models import ComponentDocument
from backend.app.components.resize_jobs import ResizeJobs
from backend.tests.test_geometry_resize import BoxProvider, document


class ResizeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(draft_db=Path(self.temp.name)/'test.sqlite', mcp_token='resize-test'), dist_dir=None)
        self.client = self.enterContext(TestClient(self.app))
        self.enterContext(patch('backend.app.components.resize_jobs.CadProvider', side_effect=lambda _: BoxProvider()))
        self.enterContext(patch('backend.app.reconstruction._read_template', new=AsyncMock(return_value=(SimpleNamespace(templateId='box', version=1), {}, {}))))
        self.draft = self.app.state.drafts.create(document=ComponentDocument.model_validate(document()))
        self.base = '/api/component-drafts/' + self.draft['draftId']

    def start(self, dimensions=None, **kwargs):
        body = {'expectedRevision': self.draft['document']['revision'], 'dimensions': dimensions or [10, 10, 150], **kwargs}
        response = self.client.post(self.base + '/resize-jobs', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait(self, job):
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            job = self.client.get(self.base+'/resize-jobs/'+job['jobId']).json()
            if job['status'] != 'running': return job
            time.sleep(.03)
        self.fail('resize job timed out')

    def body(self, job):
        return {'expectedRevision': job['expectedRevision'], 'jobId': job['jobId'], 'checksum': job['result']['checksum']}

    def test_inspect_preview_atomic_apply_persistent_undo_and_export(self):
        baseline = copy.deepcopy(self.draft['document'])
        inspected = self.wait(self.start(inspectOnly=True))
        self.assertEqual(inspected['status'], 'ready', inspected)
        self.assertEqual(inspected['result']['dimensions'], [10, 10, 100])
        job = self.wait(self.start())
        self.assertEqual(job['status'], 'ready', job)
        self.assertEqual(self.client.get(self.base).json()['document'], baseline)
        bad = self.client.post(self.base+'/resize-apply', json={**self.body(job), 'checksum': 'bad'})
        self.assertEqual(bad.status_code, 409)
        applied = self.client.post(self.base+'/resize-apply', json=self.body(job))
        self.assertEqual(applied.status_code, 200, applied.text)
        revision = applied.json()['document']['revision']
        self.assertEqual(revision, baseline['revision']+1)
        self.assertEqual(self.client.post(self.base+'/resize-apply', json=self.body(job)).status_code, 409)
        exported = self.client.get(self.base+'/export').json()
        self.assertTrue(exported['geometryDesign']['verified'])
        # A single resize preview is no longer sufficient for publication.
        with self.assertRaisesRegex(Exception, 'AUTHORING_QUALIFICATION_REQUIRED'):
            self.app.state.drafts.prepare_publication(self.draft['draftId'], revision)
        # Simulate losing process-local jobs. Undo must use the committed snapshot.
        self.app.state.resize_jobs = ResizeJobs(self.app)
        saved = self.client.get(self.base+'/resize-history').json()['undo']
        self.assertEqual(saved['revision'], revision)
        undone = self.client.post(self.base+'/resize-undo', json={**self.body(job), 'expectedRevision': revision})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()['document']['assembly'], baseline['assembly'])
        self.assertIsNone(self.client.get(self.base+'/resize-history').json()['undo'])

    def test_new_preview_invalidates_old_ready_result(self):
        old = self.wait(self.start())
        new = self.wait(self.start([10, 10, 175]))
        self.assertEqual(new['status'], 'ready', new)
        self.assertEqual(self.client.get(self.base+'/resize-jobs/'+old['jobId']).json()['status'], 'stale')
        self.assertEqual(self.client.post(self.base+'/resize-apply', json=self.body(old)).status_code, 409)

    def test_repeat_inspection_reuses_verified_measurement_without_cad_or_queue(self):
        initial = self.wait(self.start(inspectOnly=True))
        self.assertEqual(initial['status'], 'ready', initial)
        with patch('backend.app.components.resize_jobs.load_geometry', side_effect=AssertionError('must use measured result')):
            repeated = self.start(inspectOnly=True)
        self.assertEqual(repeated['status'], 'ready', repeated)
        self.assertEqual(repeated['result'], initial['result'])
        self.assertNotIn('document', repeated['result'])
        self.assertEqual(self.client.get(self.base).json()['document'], self.draft['document'])
        # Cached responses must not expose the stored result to mutation.
        self.app.state.resize_jobs.jobs[repeated['jobId']]['result']['dimensions'][0] = 999
        self.assertEqual(self.start(inspectOnly=True)['result']['dimensions'], [10, 10, 100])

    def test_hardware_edit_keeps_measurement_but_parameter_and_position_edits_remeasure(self):
        initial = self.wait(self.start(inspectOnly=True))
        self.draft = self.client.post(self.base+'/actions', json={'expectedRevision': 1, 'action': {
            'type': 'interface.hardware.set', 'memberId': 'a', 'interfaceId': 'start',
            'hardware': {'mode': 'manual', 'items': [{'name': '螺栓', 'specification': 'M8', 'quantity': 2}]}}}).json()
        with patch('backend.app.components.resize_jobs.load_geometry', side_effect=AssertionError('hardware must not rebuild CAD')):
            self.assertEqual(self.start(inspectOnly=True)['result'], initial['result'])
        self.draft = self.client.post(self.base+'/actions', json={'expectedRevision': self.draft['document']['revision'],
            'action': {'type': 'member.patch', 'id': 'a', 'patch': {'parameterValues': {'length': 120}}}}).json()
        remeasured = self.wait(self.start(inspectOnly=True))
        self.assertEqual(remeasured['result']['dimensions'], [10, 10, 120])
        self.draft = self.client.post(self.base+'/actions', json={'expectedRevision': self.draft['document']['revision'],
            'action': {'type': 'member.patch', 'id': 'a', 'patch': {'rotation': [0, 1.5707963267948966, 0]}}}).json()
        rotated = self.wait(self.start(inspectOnly=True))
        self.assertAlmostEqual(rotated['result']['dimensions'][0], 120)
        self.assertAlmostEqual(rotated['result']['dimensions'][2], 10)

    def test_inspection_does_not_make_resize_preview_applicable(self):
        self.wait(self.start(inspectOnly=True))
        cached = self.start(inspectOnly=True)
        rejected = self.client.post(self.base+'/resize-apply', json={
            'expectedRevision': cached['expectedRevision'], 'jobId': cached['jobId'], 'checksum': 'inspection'})
        self.assertEqual(rejected.status_code, 409)
        preview = self.wait(self.start([10, 10, 140]))
        self.assertEqual(preview['status'], 'ready', preview)
        self.assertAlmostEqual(preview['result']['dimensions'][2], 140, delta=.1)

    def test_preview_uses_authoring_recovery_and_reports_full_change(self):
        from backend.app.components.resize_solver import ResizeSolver, ResizeFailure
        solve = ResizeSolver.solve
        def needs_continuation(solver):
            target = solver.request.get('dimensions')
            length = solver.document['assembly']['members'][0]['parameterValues']['length']
            if target and not solver.request.get('inspectOnly') and abs(target[2]-length) > 26:
                raise ResizeFailure('需要分步求解', 'RESIZE_NO_SOLUTION')
            return solve(solver)
        with patch('backend.app.components.resize_jobs.ResizeSolver.solve', needs_continuation):
            job = self.wait(self.start())
        self.assertEqual(job['status'], 'ready', job)
        self.assertTrue(job['result']['automaticRecovery'])
        self.assertEqual(job['result']['changes'][0]['before']['parameterValues']['length'], 100)
        self.assertAlmostEqual(job['result']['changes'][0]['after']['parameterValues']['length'], 150, delta=.1)
        self.assertEqual(self.client.get(self.base).json()['document'], self.draft['document'])

    def test_brace_resize_preserves_source_dimensions_and_change_origins(self):
        from backend.app.components.resize_jobs import solve_with_recovery

        source = copy.deepcopy(self.draft['document'])
        source['braceGenerationRules'] = [{'id': 'brace-grid'}]
        original_member = source['assembly']['members'][0]
        removed_member = {**copy.deepcopy(original_member), 'id': 'old-brace', 'name': '旧撑杆'}
        unchanged_member = {**copy.deepcopy(original_member), 'id': 'unchanged', 'name': '未调整撑杆'}
        source['assembly']['members'].extend([removed_member, unchanged_member])

        class TargetAwareSolver:
            def __init__(self, document, provider, publications, request, *args):
                self.document = document
                self.request = request

            def solve(self):
                if self.request.get('inspectOnly'):
                    return {'dimensions': [10, 10, 100], 'changes': []}
                target_document = copy.deepcopy(self.document)
                target_document['assembly']['members'][0]['parameterValues']['length'] = 150
                added = {**copy.deepcopy(original_member), 'id': 'new-brace', 'name': '新增撑杆'}
                rounding_only = copy.deepcopy(unchanged_member)
                rounding_only['position'][0] += 1e-10
                target_document['assembly']['members'] = [target_document['assembly']['members'][0], rounding_only, added]
                return {
                    'document': target_document,
                    'dimensions': self.request['dimensions'],
                    'originalDimensions': [10, 10, 150],
                    'changes': [{'path': ['old-brace']}, {'path': ['new-brace']}, {'path': ['unchanged']}],
                }

        with patch('backend.app.components.resize_jobs.ResizeSolver', TargetAwareSolver):
            result = solve_with_recovery(source, None, {}, {'dimensions': [10, 10, 150]}, lambda: False, lambda _: None)

        self.assertEqual(result['originalDimensions'], [10, 10, 100])
        self.assertEqual(result['changes'][0]['path'], [original_member['id']])
        self.assertEqual(result['changes'][0]['before']['parameterValues'], original_member['parameterValues'])
        self.assertEqual(result['changes'][0]['after']['parameterValues']['length'], 150)
        self.assertEqual(len(result['changes']), 1)
        self.assertEqual(result['structureChanges'], {
            'added': [{'path': ['new-brace'], 'name': '新增撑杆'}],
            'removed': [{'path': ['old-brace'], 'name': '旧撑杆'}],
        })
        self.assertEqual(source['assembly']['members'][0]['parameterValues'], original_member['parameterValues'])
        self.assertEqual(len(source['assembly']['members']), 3)

    def test_adjacent_brace_heights_preserve_original_error_without_invalid_retry(self):
        from backend.app.components.resize_jobs import solve_with_recovery
        from backend.app.components.resize_solver import ResizeFailure
        from backend.tests.test_qualification import ScenarioPlanTests

        source = copy.deepcopy(self.draft['document'])
        source.update(ScenarioPlanTests.brace_document())
        failure = ResizeFailure('明确连接与目标尺寸冲突', 'RESIZE_CONTACT_LOST', [{'relationId': 'user-contact'}])
        requests = []
        class UnreachableSolver:
            def __init__(self, document, provider, publications, request, *args):
                self.request = request
            def solve(self):
                if self.request.get('inspectOnly'):
                    return {'dimensions': [10, 10, 2400]}
                requests.append(self.request)
                raise failure
        body = {'dimensions': [10, 10, 2250], 'design': {'locks': [{'path': ['a']}]}}
        before = copy.deepcopy(source)
        with patch('backend.app.components.resize_jobs.ResizeSolver', UnreachableSolver):
            with self.assertRaises(ResizeFailure) as caught:
                solve_with_recovery(source, None, {}, body, lambda: False, lambda _: None)
        self.assertIs(caught.exception, failure)
        self.assertEqual(requests, [body])
        self.assertEqual(source, before)

    def test_authoring_accepts_only_actual_applied_geometry_for_current_revision(self):
        from backend.app.mcp.authoring import authoring_report

        self.assertFalse(authoring_report(self.draft)['geometryVerified'])
        job = self.wait(self.start())
        self.assertEqual(job['status'], 'ready', job)
        self.assertFalse(authoring_report(self.client.get(self.base).json())['geometryVerified'])
        applied = self.client.post(self.base+'/resize-apply', json=self.body(job)).json()
        history = self.app.state.drafts.resize_history(self.draft['draftId'])
        report = authoring_report(applied, history)
        self.assertTrue(report['geometryVerified'], report)
        self.assertEqual(report['status'], 'geometry_verified', report)
        updated = self.client.post(self.base+'/actions', json={'expectedRevision': applied['document']['revision'],
            'action': {'type': 'definition.patch', 'patch': {'name': '继续制作'}}}).json()
        self.assertFalse(authoring_report(updated, history)['geometryVerified'])

    def test_original_dimensions_preflight_finds_unlinked_member_without_applying(self):
        from backend.app.mcp.authoring import authoring_report
        from backend.tests.test_geometry_resize import member

        source = document([member('left'), {**member('right'), 'position': [0, 30, 0]}])
        source['definition']['code'] = 'AUTHORING-TEST'
        self.draft = self.app.state.drafts.create(document=ComponentDocument.model_validate(source))
        self.base = '/api/component-drafts/' + self.draft['draftId']
        report = authoring_report(self.draft)
        self.assertIn('AUTHORING_RELATION_UNCONFIRMED', {issue['code'] for issue in report['issues']})
        response = self.client.post(self.base+'/resize-jobs', json={'expectedRevision': self.draft['document']['revision']})
        self.assertEqual(response.status_code, 202, response.text)
        result = self.wait(response.json())
        self.assertEqual(result['status'], 'needs_choice', result)
        self.assertEqual(result['error']['code'], 'RESIZE_NEEDS_CHOICE')
        self.assertEqual(self.client.get(self.base).json()['document'], self.draft['document'])

    def test_other_page_edit_invalidates_preview(self):
        job = self.wait(self.start())
        updated = self.client.post(self.base+'/actions', json={'expectedRevision': job['expectedRevision'], 'action': {'type': 'definition.patch', 'patch': {'name': '其他页面名称'}}})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.get(self.base+'/resize-jobs/'+job['jobId']).json()['status'], 'stale')
        self.assertEqual(self.client.post(self.base+'/resize-apply', json=self.body(job)).status_code, 409)
        self.assertEqual(self.client.get(self.base).json()['document']['definition']['name'], '其他页面名称')

    def test_cancel_and_failure_never_save(self):
        job = self.start()
        response = self.client.delete(self.base+'/resize-jobs/'+job['jobId'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'cancelled')
        failed = self.wait(self.start([10, 10, 500]))
        self.assertEqual(failed['status'], 'failed', failed)
        self.assertEqual(self.client.get(self.base).json()['document'], self.draft['document'])

    def test_invalid_input_and_cross_draft_access(self):
        job = self.wait(self.start())
        other = self.app.state.drafts.create(name='另一个组件')
        self.assertEqual(self.client.get(f"/api/component-drafts/{other['draftId']}/resize-jobs/{job['jobId']}").status_code, 404)
        self.assertEqual(self.client.post(self.base+'/resize-jobs', json={'expectedRevision': 0, 'dimensions': [10, 10, 1]}).status_code, 422)
        invalid = self.wait(self.start([10, 10, -1]))
        self.assertEqual(invalid['error']['code'], 'RESIZE_DIMENSION_INVALID')

    def test_mcp_apply_and_undo_record_agent_source_and_preserve_user_priority(self):
        def call(name, request):
            response = self.client.post('/mcp', headers={
                'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer resize-test', 'Host': '127.0.0.1:8020',
            }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': name, 'arguments': {'draftId': self.draft['draftId'], 'request': request},
            }})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()['result']
            self.assertFalse(result.get('isError'), result)
            return result['structuredContent']

        job = self.wait(self.start())
        applied = call('component_resize_apply', self.body(job))
        self.assertEqual(applied['changes'][-1]['actor'], 'agent')
        revision = applied['document']['revision']
        # Recover the undo identity after the process-local job map is gone.
        self.app.state.resize_jobs = ResizeJobs(self.app)
        response = self.client.post('/mcp', headers={
            'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer resize-test', 'Host': '127.0.0.1:8020',
        }, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'component_get_resize_history', 'arguments': {'draftId': self.draft['draftId']},
        }})
        history = response.json()['result']['structuredContent']['undo']
        self.assertEqual(history, {'jobId': job['jobId'], 'checksum': job['result']['checksum'], 'revision': revision})
        undone = call('component_resize_undo', {**self.body(job), 'expectedRevision': revision})
        self.assertEqual(undone['changes'][-1]['actor'], 'agent')
        # A page still on R1 can save its own field after AI geometry edits.
        saved = self.client.post(self.base+'/actions', json={'expectedRevision': 1, 'action': {
            'type': 'definition.patch', 'patch': {'name': '用户输入优先'},
        }})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()['reconciled'])
        self.assertEqual(saved.json()['document']['definition']['name'], '用户输入优先')


if __name__ == '__main__': unittest.main()
