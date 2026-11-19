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
        self.app = create_app(Settings(draft_db=Path(self.temp.name)/'test.sqlite'), dist_dir=None)
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
        publication = self.app.state.drafts.prepare_publication(self.draft['draftId'], revision)
        self.assertEqual(publication['schemaVersion'], 'component-package-1.1')
        self.assertEqual(publication['document'], applied.json()['document'])
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


if __name__ == '__main__': unittest.main()
