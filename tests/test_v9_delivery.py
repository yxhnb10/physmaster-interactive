import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.artifact_manager import ArtifactManager, accepted
from utils.critic_policy import gate_evaluation, DEFAULTS
from utils.node_scores import score_node, failure_categories
from utils.research_integrity import audit_node, enforce_audit, complete_run, finalize_summary
from utils.research_monitor import ResearchMonitor, list_rounds
from utils.finalizer import FinalizationAgent, write_delivery_index
from utils.review_evidence import collect_review_evidence
from web_server import make_handler


def contract():
    return dict(task_description='Test report workflow',hard_parameters=[],validation_policy={'python_enabled':True},
                subtasks=[dict(id=1,subtask_type='coding'),dict(id=2,subtask_type='analysis')],
                expected_output=[dict(path='model.py'),dict(path='report.pdf')])


def node(root,ident,sid,files,reward=.9):
    data=dict(analysis='Executed synthetic test case; not a physics conclusion',core_results='test case only',files=files)
    kind='coding' if sid==1 else 'analysis'
    audit=audit_node(contract(),data,root/f'node_{ident}',{'subtask_type':kind},
                     [dict(tool='Python_code_interpreter',execution={'exit_code':0})])
    ev=enforce_audit(gate_evaluation(dict(decision='complete',reward=reward,blocking_issues=[]),DEFAULTS),audit)
    ev=score_node(data,ev,{'subtask_type':kind}, [dict(tool='Python_code_interpreter', execution={'exit_code':0})])
    ArtifactManager(root).record_node(ident,data,ev)
    return dict(node_id=ident,subtask_id=sid,result=data,critic_feedback=ev)


class ScoreTests(unittest.TestCase):
    def test_threshold_applies_to_science_and_composite_cannot_accept_violation(self):
        ev=gate_evaluation(dict(reward=.59,decision='complete'),DEFAULTS)
        ev=enforce_audit(ev,dict(status='pass',artifact_checks=[{'path':'a.py','sha256':'abc'}]))
        scored=score_node(dict(analysis='a',files=['a.py']),ev,{'subtask_type':'coding'}, [dict(tool='Python_code_interpreter', execution={'exit_code':0})])
        self.assertAlmostEqual(scored['composite_reward'],.795)
        self.assertEqual(scored['science_reward'],.59)
        self.assertEqual(scored['decision'],'to_redraft')
        ev=enforce_audit(gate_evaluation(dict(reward=1,decision='complete'),DEFAULTS),
                         dict(status='violation',issues=['total_mass drift'],unchecked=[],artifact_checks=[{'path':'a','sha256':'abc'}]))
        scored=score_node(dict(analysis='a',files=['a']),ev)
        self.assertAlmostEqual(scored['composite_reward'],round(.75/.90,6))
        self.assertFalse(accepted(scored))

    def test_missing_score_is_not_zero_and_reasoning_omits_irrelevant_artifacts(self):
        for value in (None,True,'bad',float('nan')):
            ev=gate_evaluation(dict(reward=value,decision='complete'),DEFAULTS)
            ev['integrity_audit']={'status':'pass'}
            scored=score_node({'analysis':'reasoning'},ev,{'subtask_type':'reasoning'})
            self.assertIsNone(scored['composite_reward'])
            self.assertIsNone(scored['score_dimensions']['science']['score'])
        ev=gate_evaluation(dict(reward=0,decision='to_redraft'),DEFAULTS)
        ev['integrity_audit']={'status':'pass'}
        scored=score_node({'analysis':'reasoning'},ev,{'subtask_type':'reasoning'})
        self.assertEqual(scored['science_reward'],0)
        self.assertTrue(scored['composite_valid'])
        self.assertFalse(scored['score_dimensions']['artifact']['applicable'])

    def test_missing_evidence_stays_unscored_and_failures_have_recorded_causes(self):
        ev=gate_evaluation(dict(reward=.95,decision='complete'),DEFAULTS)
        ev=enforce_audit(ev,dict(status='unchecked',issues=[],unchecked=['missing binding'],artifact_checks=[]))
        scored=score_node({'analysis':'model'},ev,{'subtask_type':'coding'})
        self.assertEqual(scored['score_dimensions']['artifact']['score'],0)
        self.assertIsNone(scored['score_dimensions']['compliance']['score'])
        self.assertIsNone(scored['composite_reward'])
        failures=failure_categories({'delivery_status':'failed','delivery_error':'empty'},
            dict(scored,critic_error='TimeoutError',score_valid=False),
            [{'execution':{'exit_code':1}}])
        self.assertEqual({x['category'] for x in failures},{'Delivery','API','Critic','Code','Evidence'})


class ArtifactTests(unittest.TestCase):
    def test_real_review_excerpts_are_bounded_and_changed_sources_are_identified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'node_1').mkdir();(root/'node_2').mkdir()
            for i in range(10):(root/f'node_1/data{i}.txt').write_text('long actual source\n'*1000)
            good=node(root,1,1,[f'data{i}.txt' for i in range(10)])
            parent=SimpleNamespace(node_id=1,subtask_id=1,evaluation=good['critic_feedback'],result=good['result'],parent=None)
            current=SimpleNamespace(node_id=2,result={'files':[]},parent=parent)
            evidence=collect_review_evidence(root,current)
            self.assertEqual(len(evidence['accepted_prerequisites']),1)
            rows=evidence['accepted_prerequisites'][0]['files']
            self.assertLessEqual(sum(len(r.get('excerpt','')) for r in rows),18000)
            self.assertTrue(any(r.get('truncated') for r in rows))
            (root/'node_1/data0.txt').write_text('changed')
            evidence=collect_review_evidence(root,current)
            self.assertIn('发生变化',evidence['accepted_prerequisites'][0]['files'][0]['error'])

    def test_malformed_pdf_and_symlink_node_are_rejected_before_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'node_1').mkdir()
            (root/'node_1/report.pdf').write_bytes(b'%PDF-1.4\n%%EOF\n')
            data=node(root,1,2,['report.pdf'])
            self.assertEqual(data['critic_feedback']['integrity_audit']['status'],'violation')
            (root/'outside').mkdir();(root/'outside/model.py').write_text('MASS=190\n')
            (root/'node_2').symlink_to(root/'outside',target_is_directory=True)
            audit=audit_node(contract(),{'files':['model.py']},root/'node_2',{'subtask_type':'analysis'})
            self.assertEqual(audit['status'],'violation')
            self.assertIn('节点目录不能',str(audit['issues']))

    def test_full_lifecycle_selected_branch_hashes_and_auxiliary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_1';folder.mkdir()
            (folder/'model.py').write_text('MASS=190\n')
            (folder/'log.txt').write_text('executed')
            (folder/'scratch.txt').write_text('not declared')
            manager=ArtifactManager(root)
            generated=manager.record_node(1,{'files':['model.py']},None)
            self.assertEqual(generated['artifacts'][1]['status'],'generated')
            scientific=node(root,1,1,['model.py','log.txt'])
            audit=scientific['critic_feedback']['integrity_audit']
            verified=manager.record_node(1,scientific['result'],dict(integrity_audit=audit))
            self.assertTrue(any(f['status']=='verified' for f in verified['artifacts']))
            manager.record_node(1,scientific['result'],scientific['critic_feedback'])
            report=complete_run(contract(),[scientific],root,'round_budget_exhausted')
            self.assertEqual(report['status'],'partial')
            self.assertTrue((root/'final/model.py').is_file())
            self.assertTrue((root/'final/log.txt').is_file())
            self.assertFalse((root/'final/scratch.txt').exists())
            self.assertEqual(report['artifacts'][0]['download_path'],'final/model.py')
            manifest=json.loads((root/'final/manifest.json').read_text())
            self.assertEqual(manifest['accepted_nodes'],[1])
            self.assertEqual(manifest['artifacts'][0]['lifecycle'],['CREATED','REGISTERED','VERIFIED','PROMOTED','PUBLISHED'])
            self.assertEqual(json.loads((folder/'artifact_manifest.json').read_text())['artifacts'][1]['status'],'published')
            (folder/'model.py').write_text('MASS=210\n')
            self.assertEqual(complete_run(contract(),[scientific],root,'stopped')['artifacts'][0]['status'],'missing')

    def test_rejected_branch_undeclared_and_invalid_files_never_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_2';folder.mkdir()
            (folder/'model.py').write_text('MASS=190\n')
            rejected=node(root,2,1,['model.py'],reward=.1)
            manifest=ArtifactManager(root).record_node(2,rejected['result'],rejected['critic_feedback'])
            self.assertEqual(manifest['artifacts'][0]['status'],'verified')
            report=complete_run(contract(),[rejected],root,'stopped')
            self.assertEqual(report['artifacts'][0]['status'],'missing')
            self.assertFalse((root/'final/model.py').exists())
            (folder/'fake.pdf').write_text('I will generate it later')
            bad=node(root,2,2,['fake.pdf'])
            self.assertFalse(accepted(bad['critic_feedback']))
            manifest=json.loads((folder/'artifact_manifest.json').read_text())
            self.assertEqual(next(f for f in manifest['artifacts'] if f['node_path']=='fake.pdf')['status'],'invalid')

    def test_node_timeline_survives_finish_and_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);monitor=ResearchMonitor(root);monitor.start(0,0,{'description':'test'},0)
            n=SimpleNamespace(node_id=1,parent=None,subtask_id=1,node_type='draft',status='open',
                              subtask_description='test',result=None,evaluation=None,knowledge='')
            monitor.node(n,'solving');(root/'node_1/model.py').write_text('MASS=190\n')
            good=node(root,1,1,['model.py']);n.result=good['result'];n.evaluation=good['critic_feedback']
            monitor.node(n,'evaluating');monitor.node(n,'distilling');monitor.finish([n])
            complete_run(contract(),[good],root,'stopped')
            stages=[e['stage'] for e in list_rounds(root)[0]['nodes'][0]['timeline']]
            self.assertEqual(stages,['solving','evaluating','distilling','finished','published'])

    def test_summary_conflict_downgrades_final_manifest_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'node_1').mkdir();(root/'node_1/model.py').write_text('MASS=190\n')
            c=contract();c['subtasks']=c['subtasks'][:1];c['expected_output']=c['expected_output'][:1]
            good=node(root,1,1,['model.py']);report=complete_run(c,[good],root,'done')
            report['hard_parameters']=[dict(name='total_mass',value=190,unit='kg')]
            (root/'summary.md').write_text('total mass = 210 kg')
            report=finalize_summary(root/'summary.md',report)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(json.loads((root/'final/manifest.json').read_text())['status'],'partial')


class FinalizerTests(unittest.TestCase):
    def test_does_not_attempt_to_cover_missing_science_or_csv_with_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=contract();report=complete_run(c,[],root,'budget')
            supervisor=SimpleNamespace(subtasks=c['subtasks'],_expand_and_simulate_nodes=Mock())
            result={'trajectory':[]}
            self.assertEqual(FinalizationAgent(root).run(c,result,report,supervisor)[1]['status'],'partial')
            supervisor._expand_and_simulate_nodes.assert_not_called()
            self.assertEqual(json.loads((root/'finalization.json').read_text())['state'],'skipped')
            write_delivery_index(root,report)
            self.assertFalse((root/'report.pdf').exists())

    def test_bounded_report_node_creates_real_pdf_and_requires_reviewer_acceptance(self):
        from reportlab.pdfgen.canvas import Canvas
        from pypdf import PdfReader
        for score,expected in ((.95,'passed'),(.2,'partial')):
            with self.subTest(score=score),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'node_1').mkdir();(root/'node_1/model.py').write_text('MASS=190\n')
                good=node(root,1,1,['model.py']);c=contract();report=complete_run(c,[good],root,'budget')
                trajectory=[good];parent=SimpleNamespace(node_id=1,status='completed')
                monitor=ResearchMonitor(root)
                supervisor=SimpleNamespace(subtasks=[dict(c['subtasks'][0],description='model'),dict(c['subtasks'][1],description='write report')],
                    tree=SimpleNamespace(get_node=lambda _:parent,get_all_nodes=lambda:trajectory),monitor=monitor,
                    round_counter=2,live_revision=0,_find_best_trajectory=lambda:trajectory,
                    _collect_completed_subtasks=lambda:[])
                supervisor.tree.get_tree_stats=lambda:{}
                def expand(parent,kind,count,task,description,dispatch,round_index):
                    self.assertEqual(count,1);self.assertIn('Do not rerun',description)
                    self.assertIn(report['artifacts'][0]['sha256'],description)
                    folder=root/'node_2';folder.mkdir();canvas=Canvas(str(folder/'report.pdf'))
                    canvas.drawString(40,800,'Synthetic delivery test; no physical result. Source node_1/model.py');canvas.save()
                    reviewed=node(root,2,2,['report.pdf'],reward=score);trajectory.append(reviewed)
                    n=SimpleNamespace(node_id=2,parent=parent,subtask_id=2,node_type='revise',status='completed',
                        result=reviewed['result'],evaluation=reviewed['critic_feedback'],knowledge='',subtask_description=description)
                    return [n]
                supervisor._expand_and_simulate_nodes=Mock(side_effect=expand)
                result,report=FinalizationAgent(root).run(c,{'trajectory':[good],'stop_reason':'budget'},report,supervisor)
                self.assertEqual(report['status'],expected)
                self.assertEqual(supervisor._expand_and_simulate_nodes.call_count,1)
                self.assertEqual(len(PdfReader(root/'node_2/report.pdf').pages),1)
                self.assertEqual((root/'final/report.pdf').exists(),score>.85)
                self.assertEqual(result['total_rounds'],3)

    def test_options_are_bounded_and_disable_without_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            for options in ({'max_attempts':4},{'max_attempts':True},{'max_attempts':-1},{'enabled':'yes'}):
                with self.assertRaises(ValueError):FinalizationAgent(tmp,options)
            result,report=FinalizationAgent(tmp,{'enabled':False}).run({}, {}, {}, None)
            self.assertEqual(json.loads((Path(tmp)/'finalization.json').read_text())['state'],'skipped')


class DownloadTests(unittest.TestCase):
    def test_published_download_tamper_and_symlink_traversal_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'node_1').mkdir();(root/'node_1/model.py').write_text('MASS=190\n')
            good=node(root,1,1,['model.py']);report=complete_run(contract(),[good],root,'budget')
            write_delivery_index(root,report)
            (root/'contract.json').write_text('private runtime file')
            (root/'node_1/alias.txt').symlink_to(root/'contract.json')
            ident='a'*32;manager=SimpleNamespace(lock=threading.RLock(),token='test',_get=lambda _:{'task_dir':root})
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(manager))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}/api/tasks/{ident}'
            def get(path):return urllib.request.urlopen(url+path)
            try:
                with get('/artifact?path=final/model.py') as response:self.assertEqual(response.read(),b'MASS=190\n')
                with get('/manifest') as response:self.assertEqual(json.load(response)['status'],'partial')
                with get('/delivery-index') as response:self.assertIn(b'completion.json',response.read())
                for path in ('node_1/../contract.json','node_1/alias.txt','final/manifest.json'):
                    with self.assertRaises(urllib.error.HTTPError) as error:get('/artifact?path='+urllib.parse.quote(path))
                    self.assertEqual(error.exception.code,404)
                (root/'final/model.py').write_text('tampered')
                with self.assertRaises(urllib.error.HTTPError) as error:get('/artifact?path=final/model.py')
                self.assertEqual(error.exception.code,409)
                (root/'node_1/model.py').write_text('changed after review')
                with self.assertRaises(urllib.error.HTTPError) as error:get('/artifact?path=node_1/model.py')
                self.assertEqual(error.exception.code,409)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_browser_disconnect_does_not_escape_response_writer(self):
        handler_type=make_handler(SimpleNamespace())
        handler=object.__new__(handler_type)
        handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
        for error in (BrokenPipeError(),ConnectionAbortedError(),ConnectionResetError()):
            handler.wfile=SimpleNamespace(write=Mock(side_effect=error))
            handler_type.reply(handler,{'ok':True})
            self.assertTrue(handler.close_connection)
        handler.wfile=SimpleNamespace(write=Mock(side_effect=OSError('disk failure')))
        with self.assertRaises(OSError):handler_type.reply(handler,{'ok':True})


if __name__=='__main__':unittest.main()
