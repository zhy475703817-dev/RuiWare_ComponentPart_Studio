import asyncio
import copy
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.components.resize_jobs import ResizeJobs, inspection_key
from backend.tests.test_geometry_resize import document
from backend.app.components.resize_geometry import CadProvider
from backend.app.components.resize_solver import ResizeSolver, ResizeFailure
from backend.app.components.qualification import QualificationJobs


class QualificationQueueTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        resize = ResizeJobs(SimpleNamespace())
        repository = SimpleNamespace(qualification=lambda _: {'jobId': 'q', 'status': 'running'})
        jobs = QualificationJobs(SimpleNamespace(state=SimpleNamespace(drafts=repository, resize_jobs=resize)))
        job = {'cancelled': False, 'draftId': 'draft', 'report': {'jobId': 'q'}}
        jobs.jobs['draft'] = job
        return jobs, resize, job

    async def test_constructor_waits_for_cad_slot_and_runs_off_event_loop(self):
        jobs, resize, job = self.fixture()
        entered, release = threading.Event(), threading.Event()
        thread_ids = []
        measured = {'dimensions': [10, 10, 100], 'interfaces': []}
        class SlowConstructor:
            def __init__(self, *_args):
                thread_ids.append(threading.get_ident())
                entered.set()
                if not release.wait(3): raise AssertionError('HTTP loop failed to resume during constructor CAD')
            def solve(self): return measured

        await resize.semaphore.acquire()
        with patch('backend.app.components.qualification.ResizeSolver', SlowConstructor):
            task = asyncio.create_task(jobs.solve(job, document(), None, {}, inspect=True))
            try:
                await asyncio.sleep(.03)
                self.assertFalse(entered.is_set(), 'constructor must not overlap another CAD operation')
                resize.semaphore.release()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.assertNotEqual(thread_ids, [threading.get_ident()])
                self.assertFalse(task.done())
                release.set()
                self.assertEqual(await asyncio.wait_for(task, 2), measured)
            finally:
                release.set()
                if not task.done(): task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(resize.semaphore.locked())

    async def test_cancelled_qualification_leaves_queue_without_starting_cad(self):
        jobs, resize, job = self.fixture()
        await resize.semaphore.acquire()
        with patch('backend.app.components.qualification.ResizeSolver', side_effect=AssertionError('cancelled work must not start CAD')):
            task = asyncio.create_task(jobs.solve(job, document(), None, {}, inspect=True))
            await asyncio.sleep(.03)
            job['cancelled'] = True
            with self.assertRaises(ResizeFailure) as caught:
                await asyncio.wait_for(task, 1)
        self.assertEqual(caught.exception.code, 'RESIZE_CANCELLED')
        self.assertTrue(resize.semaphore.locked(), 'other work must keep its slot')
        resize.semaphore.release()


class TemplateReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_reads_share_one_load_and_failed_reads_can_retry(self):
        jobs = ResizeJobs(SimpleNamespace())
        gate = asyncio.Event()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            await gate.wait()
            return {'immutable': 'template'}

        first = asyncio.create_task(jobs.resource(('part', 'id', 'hash'), load))
        second = asyncio.create_task(jobs.resource(('part', 'id', 'hash'), load))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        gate.set()
        result = await second
        self.assertEqual(calls, 1)
        self.assertEqual(await jobs.resource(('part', 'id', 'hash'), load), result)
        self.assertEqual(calls, 1)

        async def fail(): raise ValueError('unavailable')
        with self.assertRaises(ValueError): await jobs.resource(('part', 'id', 'new-hash'), fail)
        self.assertNotIn(('part', 'id', 'new-hash'), jobs.resources)
        self.assertEqual(await jobs.resource(('part', 'id', 'new-hash'), load), result)
        self.assertEqual(calls, 2)
        await jobs.close()

    async def test_immutable_template_cache_is_bounded(self):
        jobs = ResizeJobs(SimpleNamespace())
        async def load(): return 'value'
        for index in range(70): await jobs.resource(('part', str(index), 'hash'), load)
        self.assertEqual(len(jobs.resources), 64)
        self.assertNotIn(('part', '0', 'hash'), jobs.resources)
        await jobs.close()

    async def test_queued_inspection_can_use_background_measurement_without_waiting_for_resize(self):
        jobs = ResizeJobs(SimpleNamespace())
        source = document()
        key = inspection_key(source)
        await jobs.semaphore.acquire()
        async def inspect():
            async with jobs.geometry_turn(key, lambda: False) as measured:
                return measured
        pending = asyncio.create_task(inspect())
        await asyncio.sleep(.01)
        measured = {'dimensions': [10, 10, 100], 'interfaces': []}
        jobs.remember_inspection(source, measured)
        self.assertEqual(await asyncio.wait_for(pending, 1), measured)
        self.assertTrue(jobs.semaphore.locked())
        jobs.semaphore.release()
        await jobs.close()

    async def test_cancelled_inspection_releases_its_queue_wait_without_consuming_cad_slot(self):
        jobs = ResizeJobs(SimpleNamespace())
        await jobs.semaphore.acquire()
        async def inspect():
            async with jobs.geometry_turn('uncached', lambda: False):
                self.fail('background work still holds the slot')
        pending = asyncio.create_task(inspect())
        await asyncio.sleep(.01)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        jobs.semaphore.release()
        await asyncio.wait_for(jobs.semaphore.acquire(), 1)
        jobs.semaphore.release()
        await jobs.close()


class InspectionIdentityTests(unittest.TestCase):
    def test_nested_shape_and_layout_inputs_always_invalidate_inspection(self):
        source = document()
        nested = document()
        source['assembly']['members'][0]['instanceDocument'] = nested
        key = inspection_key(source)
        for field, value in [('parameterValues', {'length': 120}), ('rotation', [0, 1, 0]), ('position', [1, 0, 0])]:
            changed = copy.deepcopy(source)
            changed['assembly']['members'][0]['instanceDocument']['assembly']['members'][0][field] = value
            self.assertNotEqual(inspection_key(changed), key)
        changed = copy.deepcopy(source)
        changed['assembly']['members'][0]['template']['sha256'] = 'b'*64
        self.assertNotEqual(inspection_key(changed), key)
        changed = copy.deepcopy(source)
        changed['revision'] += 1
        changed['assembly']['members'][0]['instanceDocument']['revision'] += 1
        changed['assembly']['members'][0]['interfaces'][0]['hardware'] = {'mode': 'none'}
        self.assertEqual(inspection_key(changed), key)
        self.assertNotIn('hardware', source['assembly']['members'][0]['interfaces'][0])

    def test_exact_cad_inspection_skips_mesh_and_later_resize_restores_it(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/geometry-resize-v1.json').read_text(encoding='utf-8'))
        provider = CadProvider({(item['reference']['publicationId'], item['reference']['sha256']):
            (item['draft'], item['material']) for item in fixture['templates'].values()})
        provider.include_hull = False
        source = copy.deepcopy(fixture['components']['beam'])
        before = copy.deepcopy(source)
        measured = ResizeSolver(source, provider, {}, {'inspectOnly': True}).solve()
        self.assertTrue(provider.cache)
        self.assertTrue(all(geometry.hull is None for geometry in provider.cache.values()))
        self.assertEqual(source, before)
        provider.include_hull = True
        repeated = ResizeSolver(source, provider, {}, {'inspectOnly': True}).solve()
        self.assertEqual(measured, repeated)
        self.assertTrue(all(geometry.hull is not None for geometry in provider.cache.values()))
        target = list(measured['dimensions']); target[0] += 100
        preview = ResizeSolver(source, provider, {}, {'dimensions': target}).solve()
        self.assertAlmostEqual(preview['dimensions'][0], target[0], delta=.1)
        self.assertEqual(source, before)
