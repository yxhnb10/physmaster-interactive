import importlib.util
import json
import hashlib
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

FAKE_WORKER = '''import argparse,time,yaml,json
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
(td/'completion.json').write_text(json.dumps({'status':'passed','stop_reason':'all_subtasks_completed','subtasks':[], 'artifacts':[], 'reasons':[]}))
'''


def until(fn,timeout=6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=fn()
        if value:return value
        time.sleep(.03)
    raise AssertionError('Condition timed out')


class ChatServerTests(unittest.TestCase):
    def test_v94_upgrade_recovers_legacy_v93_parent_chain(self):
        first=self.manager.create('total mass 190 kg');self.finished(first)
        (self.manager.jobs[first]['task_dir']/'first_model.py').write_text('TOTAL_MASS=190')
        # Legacy v9.3 child has parent_id but no inherited snapshots.
        second=self.manager.create('same research',original_query='total mass 190 kg',parent_id=first,
            initial_messages=[dict(id='old',text='Add thermal analysis',status='included')])
        self.finished(second)
        (self.manager.jobs[second]['task_dir']/'second_data.csv').write_text('mass\n190')
        third=self.manager.continue_task(second,'Validate opening')['id']
        data=json.loads((self.manager.jobs[third]['task_dir']/'inheritance.json').read_text())
        self.assertEqual([s['task_id'] for s in data['sources']],[first,second])
        self.assertTrue(any(r['source_path']=='first_model.py' for r in data['files']))
        self.assertTrue(any(r['source_path']=='second_data.csv' for r in data['files']))
    def test_v94_snapshot_all_outputs_multigeneration_and_restart(self):
        parent=self.manager.create('total mass 190 kg')
        self.finished(parent)
        folder=self.manager.jobs[parent]['task_dir']
        (folder/'node_7').mkdir();(folder/'node_7/model.py').write_text('TOTAL_MASS=190\n')
        (folder/'node_3').mkdir();(folder/'node_3/failed.json').write_text('{"reward":0}')
        child=self.manager.continue_task(parent,'Add heat transfer')['id']
        child_dir=self.manager.jobs[child]['task_dir']
        manifest=json.loads((child_dir/'inheritance.json').read_text())
        self.assertTrue(any(r['source_path']=='node_3/failed.json' for r in manifest['files']))
        self.assertEqual((child_dir/'inherited'/parent/'node_7/model.py').read_text(),'TOTAL_MASS=190\n')
        self.finished(child)
        restored=TaskManager(self.root,self.root/'config.yaml')
        try:
            self.assertIn(child,restored.jobs)
            grandchild=restored.continue_task(child,'Validate parachute')['id']
            inherited=json.loads((restored.jobs[grandchild]['task_dir']/'inheritance.json').read_text())
            self.assertEqual([s['task_id'] for s in inherited['sources']],[parent,child])
            self.assertEqual(inherited['conditions'],['Add heat transfer','Validate parachute'])
            self.assertEqual(restored.snapshot(grandchild)['hard_parameters'][0]['value'],190)
        finally:
            restored.close()
            for job in restored.jobs.values():
                if job['process'] is not None:job['process'].wait(timeout=10)

    def test_v94_inheritance_http_download_hash_and_invalid_input(self):
        parent=self.manager.create('total mass 190 kg');self.finished(parent)
        old=self.manager.jobs[parent]['task_dir'];(old/'model.py').write_text('TOTAL_MASS=190\n')
        child=self.manager.continue_task(parent,'Add thermal model')['id']
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.manager))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)+'/api/tasks/'+child
        try:
            with urllib.request.urlopen(url+'/inheritance?query=model.py') as response:inventory=json.load(response)
            self.assertTrue(inventory['inherited']);self.assertEqual(len(inventory['files']),1)
            row=inventory['files'][0]
            with urllib.request.urlopen(url+'/inherited-file?file_id='+row['file_id']) as response:
                self.assertEqual(response.read(),b'TOTAL_MASS=190\n')
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(url+'/inheritance?offset=bad')
            self.assertEqual(error.exception.code,409)
            (self.manager.jobs[child]['task_dir']/row['snapshot_path']).write_text('changed')
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(url+'/inherited-file?file_id='+row['file_id'])
            self.assertEqual(error.exception.code,409)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)

    def test_v9_continuation_carries_manifest_for_revalidation_not_inherited_acceptance(self):
        ident=self.manager.create('total mass is 190 kg')
        until(lambda:self.manager.snapshot(ident)['state']=='finished')
        folder=self.manager.jobs[ident]['task_dir']
        manifest={'status':'passed','accepted_nodes':[1], 'artifacts':[dict(path='model.py',sha256='synthetic-digest',source='node_1/model.py')]}
        (folder/'final_manifest.json').write_text(json.dumps(manifest))
        original=(folder/'summary.md').read_bytes()
        child=self.manager.continue_task(ident,'Add a thermal constraint')['id']
        snapshot=self.manager.snapshot(child)
        prompt=(self.manager.jobs[child]['task_dir'].parent/'query.txt').read_text(encoding='utf-8')
        self.assertIn('synthetic-digest',prompt)
        self.assertIn('不能直接继承验收',prompt)
        self.assertIn('未恢复旧进程或旧 MCTS 树',prompt)
        self.assertEqual(snapshot['hard_parameters'][0]['value'],190)
        self.assertIsNone(snapshot['completion_report'])
        self.assertEqual((folder/'summary.md').read_bytes(),original)

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
        until(lambda:all(j['state'] in ('finished','partial','failed') for j in self.manager.jobs.values()))
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

    def test_hard_parameters_inherit_user_input_not_old_summary(self):
        with self.assertRaises(ValueError):self.manager.create('总质量190 kg','wrong capabilities')
        with self.assertRaises(ValueError):self.manager.create('总质量190 kg',hard_parameter_text='total_mass = 210 kg')
        self.assertFalse(self.manager.jobs)
        ident=self.manager.create('总质量190 kg',hard_parameter_text='nose_radius = 0.5 m')
        job=self.manager.jobs[ident]
        config=yaml.safe_load((job['task_dir'].parent/'web_config.yaml').read_text())
        self.assertEqual(config['integrity']['hard_parameters'][0]['value'],190)
        self.finished(ident)
        (job['task_dir']/'summary.md').write_text('Old TPS result: mass=210 kg')
        child=self.manager.continue_task(ident,'重新评估热防护')['id']
        values={p['name']:p['value'] for p in self.manager.snapshot(child)['hard_parameters']}
        self.assertEqual(values,{'total_mass':190,'nose_radius':.5})
        self.finished(child)
        current=self.manager.snapshot(child)
        changed=self.manager.continue_task(child,'总质量改为200 kg',hard_parameter_text=current['hard_parameter_text'])['id']
        self.assertEqual({p['name']:p['value'] for p in self.manager.snapshot(changed)['hard_parameters']},
                         {'total_mass':200,'nose_radius':.5})
        self.finished(changed)
        restored=TaskManager(self.root,self.root/'config.yaml')
        self.assertEqual(restored.snapshot(changed)['hard_parameters'],self.manager.snapshot(changed)['hard_parameters'])

    def test_normal_exit_without_completion_report_is_partial(self):
        worker='\n'.join(line for line in FAKE_WORKER.splitlines() if 'completion.json' not in line)
        (self.root/'run.py').write_text(worker)
        ident=self.manager.create('research not fully checked')
        until(lambda:self.manager.snapshot(ident)['state']=='partial')
        saved=self.manager.snapshot(ident)
        self.assertTrue(saved['summary_available']);self.assertEqual(saved['exit_code'],0)
        self.assertIsNone(saved['completion_report'])
        self.manager=TaskManager(self.root,self.root/'config.yaml')
        self.assertEqual(self.manager.snapshot(ident)['state'],'partial')
        child=self.manager.continue_task(ident,'finish missing analysis')['id']
        until(lambda:self.manager.snapshot(child)['state']=='partial')

    def test_runtime_mass_amendment_changes_only_after_receipt(self):
        ident=self.manager.create('总质量190 kg')
        self.manager.control(ident,'pause')
        until(lambda:self.manager.snapshot(ident)['state']=='paused')
        self.manager.update(ident,'总质量改为200 kg')
        self.assertEqual(self.manager.snapshot(ident)['hard_parameters'][0]['value'],190)
        self.manager.control(ident,'resume')
        until(lambda:self.manager.snapshot(ident)['messages'][0]['status']=='applied')
        self.assertEqual(self.manager.snapshot(ident)['hard_parameters'][0]['value'],200)
        self.finished(ident)

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
            td=self.manager.jobs[ident]['task_dir']
            (td/'results.csv').write_bytes(b'h,n\n87,5\n')
            report=json.loads((td/'completion.json').read_text())
            report['artifacts']=[dict(path='results.csv',status='available',sha256=hashlib.sha256((td/'results.csv').read_bytes()).hexdigest())]
            (td/'completion.json').write_text(json.dumps(report))
            download=url+'/api/tasks/'+ident+'/artifact?path=results.csv'
            with urllib.request.urlopen(download) as response:self.assertIn(b'87,5',response.read())
            (td/'results.csv').write_bytes(b'changed after audit')
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(download)
            self.assertEqual(exc.exception.code,409)
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
