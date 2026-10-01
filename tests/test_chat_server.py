import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import yaml
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from web_server import TaskManager, make_handler
from utils.live_updates import UpdateInbox, set_control

FAKE_WORKER = '''import argparse,time,yaml
from pathlib import Path
from utils.live_updates import UpdateInbox
from utils.runtime_timing import RunTimer, measure_operation
p=argparse.ArgumentParser();p.add_argument('--cfg_file');a=p.parse_args()
c=yaml.safe_load(Path(a.cfg_file).read_text());td=Path(c['pipeline']['output_path'])/'query'
timer=RunTimer(td);timer.change('clarifying')
time.sleep(0.4)
inbox=UpdateInbox(td,timing=timer);revision=0;timer.change('theoretician')
for round_index in range(14):
 inbox.checkpoint(round_index,revision)
 items=inbox.pending()
 if items:
  revision+=1;inbox.acknowledge(items,revision)
 inbox.progress('running',round_index,revision)
 print('round',round_index,flush=True)
 with measure_operation('tool:web_search'):time.sleep(0.1)
inbox.close();timer.change('summary');time.sleep(0.1)
(td/'summary.md').write_text('Mock summary, revision '+str(revision))
timer.finish(True)
'''


def until(fn,timeout=6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=fn()
        if value:return value
        time.sleep(.03)
    raise AssertionError('Condition timed out')


class ChatServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'utils').mkdir()
        shutil.copy2(ROOT/'utils/live_updates.py',self.root/'utils/live_updates.py')
        shutil.copy2(ROOT/'utils/runtime_timing.py',self.root/'utils/runtime_timing.py')
        (self.root/'run.py').write_text(FAKE_WORKER)
        cfg=self.root/'config.yaml';cfg.write_text('pipeline: {}\n')
        self.manager=TaskManager(self.root,cfg)

    def tearDown(self):
        self.manager.close()
        for job in self.manager.jobs.values():
            if job['process'] is not None:
                job['process'].wait(timeout=10)
        until(lambda:all(j['state'] in ('finished','failed') for j in self.manager.jobs.values()))
        self.tmp.cleanup()

    def test_early_message_pause_batch_resume_and_final_receipt(self):
        ident=self.manager.create('initial task')
        first=self.manager.update(ident,'early constraint')
        self.assertEqual(first['status'],'waiting')
        self.manager.control(ident,'pause')
        until(lambda:self.manager.snapshot(ident)['state']=='paused')
        self.manager.update(ident,'second constraint')
        time.sleep(.15)
        snap=self.manager.snapshot(ident)
        self.assertEqual([m['status'] for m in snap['messages']],['queued','queued'])
        self.manager.control(ident,'resume')
        until(lambda:all(m['status']=='applied' for m in self.manager.snapshot(ident)['messages']))
        snap=until(lambda:(s if (s:=self.manager.snapshot(ident))['state']=='finished' else None))
        self.assertEqual(snap['revision'],1)
        self.assertIn('Mock summary',snap['summary'])
        with self.assertRaises(ValueError):self.manager.update(ident,'too late')

    def test_single_worker_and_validation(self):
        with self.assertRaises(ValueError):self.manager.create(' ')
        self.manager.create('task')
        with self.assertRaises(ValueError):self.manager.create('second concurrent task')

    def finished(self, ident):
        return until(lambda:(s if (s:=self.manager.snapshot(ident))['state']=='finished' else None))

    def test_completed_continuation_inherits_conditions_and_preserves_outputs(self):
        parent = self.manager.create('Original physics question')
        self.manager.update(parent, 'Consider aerodynamic heating')
        old = self.finished(parent)
        old_dir = self.manager.jobs[parent]['task_dir']
        artifact = old_dir / 'node_14' / 'model.py'
        artifact.parent.mkdir()
        artifact.write_text('old model remains intact', encoding='utf-8')
        old_summary = (old_dir / 'summary.md').read_bytes()
        child = self.manager.continue_task(parent, 'Correct the heat flux law')['id']
        child_job = self.manager.jobs[child]
        prompt = (child_job['task_dir'].parent / 'query.txt').read_text(encoding='utf-8')
        self.assertIn('Original physics question', prompt)
        self.assertIn('Consider aerodynamic heating', prompt)
        self.assertIn('Correct the heat flux law', prompt)
        self.assertIn(old['summary'], prompt)
        self.assertIn(str(old_dir), prompt)
        self.assertNotEqual(old_dir, child_job['task_dir'])
        self.assertEqual(self.manager.snapshot(child)['parent_id'], parent)
        self.assertEqual(self.manager.snapshot(child)['query'], 'Original physics question')
        self.assertEqual([m['status'] for m in child_job['messages']], ['included', 'included'])
        self.manager.update(child, 'Use finite canopy inflation')
        self.finished(child)
        grandchild = self.manager.continue_task(child, 'Check landing impact')['id']
        grand_prompt = (self.manager.jobs[grandchild]['task_dir'].parent / 'query.txt').read_text(encoding='utf-8')
        for condition in ('Consider aerodynamic heating', 'Correct the heat flux law',
                          'Use finite canopy inflation', 'Check landing impact'):
            self.assertIn(condition, grand_prompt)
        self.assertEqual(self.manager.snapshot(parent)['messages'], old['messages'])
        self.assertEqual((old_dir / 'summary.md').read_bytes(), old_summary)
        self.assertEqual(artifact.read_text(encoding='utf-8'), 'old model remains intact')

    def test_restore_new_and_legacy_completed_history(self):
        parent = self.manager.create('Historical question')
        self.manager.update(parent, 'Historical condition')
        old = self.finished(parent)
        restored = TaskManager(self.root, self.root / 'config.yaml')
        self.assertEqual(restored.snapshot(parent)['summary'], old['summary'])
        self.assertEqual(restored.snapshot(parent)['messages'], old['messages'])
        restored.close()
        # A completed v8 output has no task.json; recover its queued conditions.
        (self.manager.jobs[parent]['task_dir'].parent / 'task.json').unlink()
        self.manager = TaskManager(self.root, self.root / 'config.yaml')
        self.assertEqual(self.manager.snapshot(parent)['summary'], old['summary'])
        self.assertEqual(self.manager.snapshot(parent)['messages'][0]['text'], 'Historical condition')
        self.assertEqual(self.manager.snapshot(parent)['messages'][0]['status'], 'applied')
        child = self.manager.continue_task(parent, 'New condition after restart')['id']
        self.finished(child)
        self.manager = TaskManager(self.root, self.root / 'config.yaml')
        self.assertEqual(self.manager.snapshot(child)['parent_id'], parent)
        self.assertEqual([m['text'] for m in self.manager.snapshot(child)['messages']],
                         ['Historical condition', 'New condition after restart'])

    def test_continue_rejects_running_source_and_concurrent_worker(self):
        parent = self.manager.create('task')
        with self.assertRaises(ValueError): self.manager.continue_task(parent, 'too early')
        self.finished(parent)
        with self.assertRaises(ValueError): self.manager.continue_task(parent, ' ')
        self.manager.create('another task')
        with self.assertRaises(ValueError): self.manager.continue_task(parent, 'cannot run in parallel')

    def test_late_unapplied_condition_is_carried_into_next_run(self):
        parent = self.manager.create('task')
        self.finished(parent)
        self.manager.jobs[parent]['messages'].append(dict(id='late', text='Late correction', status='not_applied'))
        child = self.manager.continue_task(parent, 'Additional correction')['id']
        messages = self.manager.snapshot(child)['messages']
        self.assertEqual([m['text'] for m in messages], ['Late correction', 'Additional correction'])
        self.assertEqual(self.manager.snapshot(parent)['messages'][0]['status'], 'not_applied')

    def test_capability_validation_has_no_worker_side_effect(self):
        for values in ({'web_search':'false'}, {'unknown':True}, [], False):
            with self.assertRaises(ValueError): self.manager.create('task', values)
        self.assertFalse(self.manager.jobs)

    def test_capabilities_are_per_task_and_survive_continuation_and_restart(self):
        cfg = self.root / 'config.yaml'
        cfg.write_text(yaml.safe_dump({'pipeline':{}, 'tools':{'web_enabled':True},
            'landau':{'prior_enabled':False}, 'llm':{'api_key':'private-test-key'}}), encoding='utf-8')
        original_config = cfg.read_bytes()
        parent = self.manager.create('task', {'web_search':False, 'arxiv_search':False, 'python':False})
        job = self.manager.jobs[parent]
        runtime = yaml.safe_load((job['task_dir'].parent / 'web_config.yaml').read_text())
        self.assertFalse(runtime['tools']['web_enabled'])
        self.assertFalse(runtime['tools']['python_enabled'])
        self.assertFalse(runtime['landau']['library_enabled'])
        self.assertEqual(cfg.read_bytes(), original_config)
        settings = self.manager.capability_settings()
        self.assertTrue(settings['defaults']['web_search'])
        self.assertNotIn('private-test-key', json.dumps(settings))
        self.finished(parent)
        child = self.manager.continue_task(parent, 'correction', {'web_search':True, 'skills':False})['id']
        values = self.manager.snapshot(child)['capabilities']
        self.assertTrue(values['web_search'])
        self.assertFalse(values['skills'])
        self.assertFalse(values['python'])
        self.assertFalse(values['arxiv_search'])
        self.assertFalse(self.manager.snapshot(parent)['capabilities']['web_search'])
        self.finished(child)
        self.manager = TaskManager(self.root, cfg)
        self.assertEqual(self.manager.snapshot(child)['capabilities'], values)
        grandchild = self.manager.continue_task(child, 'another correction')['id']
        self.assertEqual(self.manager.snapshot(grandchild)['capabilities'], values)

    def test_summary_download_formats_and_path_containment(self):
        ident = self.manager.create('task')
        with self.assertRaises(RuntimeError): self.manager.summary_document(ident)
        self.finished(ident)
        path = self.manager.jobs[ident]['task_dir'] / 'summary.md'
        text = '# 中文总结\n<script>alert(1)</script>\n'
        path.write_text(text, encoding='utf-8')
        for format in ('md','txt'):
            content, content_type, filename = self.manager.summary_document(ident, format)
            self.assertEqual(content.decode('utf-8'), text)
            self.assertTrue(filename.endswith('.'+format))
        content, content_type, filename = self.manager.summary_document(ident, 'html')
        self.assertIn('&lt;script&gt;', content.decode('utf-8'))
        self.assertNotIn('<script>', content.decode('utf-8'))
        self.assertEqual(content_type, 'text/html; charset=utf-8')
        with self.assertRaises(ValueError): self.manager.summary_document(ident, '../config.yaml')
        path.unlink()
        with self.assertRaises(FileNotFoundError): self.manager.summary_document(ident)
        secret = self.root / 'private.txt';secret.write_text('private')
        try: path.symlink_to(secret)
        except OSError: return  # Symlink privileges vary on Windows.
        with self.assertRaises(FileNotFoundError): self.manager.summary_document(ident)

    def test_critic_settings_are_written_inherited_and_restored(self):
        cfg = self.root / 'config.yaml'
        original = cfg.read_bytes()
        settings = dict(accept_threshold=.92, redraft_threshold=.55,
                        retrieval_threshold=.7, retrieval_enabled=True)
        parent = self.manager.create('task', critic_settings=settings)
        job = self.manager.jobs[parent]
        runtime = yaml.safe_load((job['task_dir'].parent/'web_config.yaml').read_text())
        self.assertEqual(runtime['critic']['accept_threshold'],.92)
        self.assertEqual(runtime['critic']['redraft_threshold'],.55)
        self.assertEqual(runtime['tools']['retrieval_critic']['threshold'],.7)
        self.assertTrue(runtime['tools']['retrieval_critic']['enabled'])
        self.assertEqual(cfg.read_bytes(),original)
        self.finished(parent)
        child = self.manager.continue_task(parent,'new condition',critic_settings={'accept_threshold':.95})['id']
        self.assertEqual(self.manager.snapshot(child)['critic_settings'],dict(settings,accept_threshold=.95))
        self.assertEqual(self.manager.snapshot(parent)['critic_settings'],settings)
        self.finished(child)
        self.manager=TaskManager(self.root,cfg)
        self.assertEqual(self.manager.snapshot(child)['critic_settings']['accept_threshold'],.95)
        self.assertEqual(self.manager.capability_settings()['critic_defaults']['accept_threshold'],.85)

    def test_invalid_critic_settings_cannot_start_a_worker(self):
        for settings in ({'accept_threshold':float('nan')},{'redraft_threshold':.99},
                         {'accept_threshold':'0.9'},{'retrieval_enabled':'true'},[]):
            with self.assertRaises(ValueError):self.manager.create('task',critic_settings=settings)
        self.assertFalse(self.manager.jobs)

    def test_timing_pause_completion_restart_and_continuation(self):
        ident=self.manager.create('timed research')
        first=self.manager.snapshot(ident)['timing']['elapsed_seconds']
        self.manager.control(ident,'pause')
        until(lambda:self.manager.snapshot(ident)['state']=='paused')
        before=self.manager.snapshot(ident)['timing']
        time.sleep(.18)
        after=self.manager.snapshot(ident)['timing']
        self.assertGreater(after['elapsed_seconds'],first)
        self.assertGreater(after['paused_seconds']-before['paused_seconds'],.12)
        self.assertLess(abs(after['active_seconds']-before['active_seconds']),.06)
        self.manager.control(ident,'resume')
        self.finished(ident)
        saved=self.manager.snapshot(ident)['timing']
        self.assertGreater(saved['phases']['clarifying'],.3)
        self.assertGreater(saved['phases']['summary'],.08)
        self.assertEqual(saved['operations']['tool:web_search']['calls'],14)
        self.assertEqual(saved['operations']['tool:web_search']['running'],0)
        time.sleep(.05)
        self.assertEqual(self.manager.snapshot(ident)['timing'],saved)
        self.manager=TaskManager(self.root,self.root/'config.yaml')
        self.assertEqual(self.manager.snapshot(ident)['timing'],saved)
        child=self.manager.continue_task(ident,'next run')['id']
        self.assertLess(self.manager.snapshot(child)['timing']['elapsed_seconds'],saved['elapsed_seconds'])
        self.finished(child)
        self.assertEqual(self.manager.snapshot(ident)['timing'],saved)

    def test_failed_and_legacy_task_timing(self):
        (self.root/'run.py').write_text(FAKE_WORKER.replace('timer.finish(True)',
            "timer.finish(False);raise RuntimeError('simulated failure')"))
        ident=self.manager.create('failed task')
        until(lambda:self.manager.snapshot(ident)['state']=='failed')
        data=self.manager.snapshot(ident)
        self.assertEqual(data['state'],'failed')
        saved=data['timing'];self.assertGreater(saved['elapsed_seconds'],0)
        time.sleep(.04)
        self.assertEqual(self.manager.snapshot(ident)['timing'],saved)
        # Old metadata has no actual start/end duration: never invent one.
        run_root=self.manager.jobs[ident]['task_dir'].parent
        meta=json.loads((run_root/'task.json').read_text())
        for key in ('started_at','finished_at','elapsed_seconds'):meta.pop(key,None)
        (run_root/'task.json').write_text(json.dumps(meta))
        (run_root/'query/runtime.json').unlink()
        shutil.rmtree(run_root/'query/timing_operations')
        restored=TaskManager(self.root,self.root/'config.yaml').snapshot(ident)['timing']
        self.assertIsNone(restored['elapsed_seconds'])
        self.assertFalse(restored['available'])

    def test_http_security_validation_and_start(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.manager))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url) as response:
                html=response.read().decode()
                self.assertIn(self.manager.token,html)
                self.assertNotIn('__TOKEN__',html)
            with urllib.request.urlopen(url+'/api/capabilities') as response:
                self.assertIn('web_search', json.load(response)['defaults'])
            req=urllib.request.Request(url+'/api/tasks',data=b'{"query":"x"}',headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
            self.assertEqual(exc.exception.code,403)
            req.add_header('X-PhysMaster-Token',self.manager.token)
            with urllib.request.urlopen(req) as response:
                ident=json.load(response)['id']
                self.assertEqual(response.status,201)
            with urllib.request.urlopen(url+'/api/tasks/'+ident) as response:
                self.assertEqual(json.load(response)['query'],'x')
            self.finished(ident)
            with urllib.request.urlopen(url+'/api/tasks/'+ident+'/summary?format=md') as response:
                self.assertIn('attachment;', response.headers['Content-Disposition'])
                self.assertTrue(response.headers['Content-Type'].startswith('text/markdown'))
                self.assertIn(b'Mock summary',response.read())
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(url+'/api/tasks/'+ident+'/summary?format=pdf')
            self.assertEqual(exc.exception.code,400)
            continuation = urllib.request.Request(url+'/api/tasks/'+ident+'/continue',
                data=json.dumps({'text':'Review thermal model'}).encode(),
                headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(continuation)
            self.assertEqual(exc.exception.code,403)
            continuation.add_header('X-PhysMaster-Token',self.manager.token)
            continuation.add_header('Origin','https://untrusted.example')
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(continuation)
            self.assertEqual(exc.exception.code,403)
            continuation.remove_header('Origin')
            with urllib.request.urlopen(continuation) as response:
                self.assertEqual(response.status,201)
                child=json.load(response)['id']
            with urllib.request.urlopen(url+'/api/tasks/'+child) as response:
                self.assertEqual(json.load(response)['parent_id'],ident)
            evil=urllib.request.Request(url,headers={'Host':'untrusted.example'})
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(evil)
            self.assertEqual(exc.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
