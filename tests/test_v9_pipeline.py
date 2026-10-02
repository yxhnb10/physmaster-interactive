"""Full offline run.py integration with spawned workers and real file execution."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def test_v94_inherited_model_reaches_real_spawned_solver_when_clarifier_omits_all_paths(self):
        from utils.continuation import snapshot_research, file_hash
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);sdk=root/'sdk';sdk.mkdir()
            shutil.copyfile(ROOT/'tests/mock_openai_runtime.py',sdk/'openai.py')
            old=root/'old/query';node=old/'node_12';node.mkdir(parents=True)
            (node/'dependency.py').write_text("OLD_SENTINEL='original-baseline-keep'\n")
            (node/'model.py').write_text('from dependency import OLD_SENTINEL\nTOTAL_MASS=190\n')
            before=(node/'model.py').read_bytes()
            (node/'artifact_manifest.json').write_text(json.dumps(dict(node_accepted=True,artifacts=[
                dict(path='node_12/model.py',sha256=file_hash(node/'model.py'),state='VERIFIED')])))
            task=root/'out/query';snapshot_research(old,task,'old-task',['Keep mass 190 kg','Add analysis'])
            # Source removal ensures execution reads the new snapshot, not the original path.
            shutil.rmtree(old)
            query=root/'query.txt';query.write_text('Continue prior model, total mass 190 kg. Add analysis.')
            config=dict(llm=dict(api_key='offline-fixture',base_url='http://offline.invalid',model='mock-default',
                model_overrides={role:'mock-'+name for role,name in [('clarifier','clarifier'),('supervisor','supervisor'),
                ('critic','critic'),('critic_strong','critic'),('promoter','promoter'),('promoter_strong','promoter'),('summarizer','summarizer')]}),
                pipeline=dict(query_file=str(query),output_path=str(root/'out'),parallel_processes=1,max_rounds=1,
                    authoritative_user_inputs=['total mass 190 kg','Add analysis']),
                mcts=dict(draft_expansion=1,revise_expansion=1),skills={'enabled':False},visualization={'enabled':False},
                landau=dict(prior_enabled=False,library_enabled=False,workflow_enabled=False,wisdom_save_enabled=False),
                tools=dict(python_enabled=True,web_enabled=False,codata_enabled=False),finalization=dict(enabled=False))
            cfg=root/'config.yaml';cfg.write_text(yaml.safe_dump(config))
            env=dict(os.environ,PYTHONPATH=str(sdk)+os.pathsep+os.environ.get('PYTHONPATH',''),
                PYTHONUTF8='1',PHY_TEST_INHERITANCE='1',PHY_TEST_TRACE=str(root/'api-trace.jsonl'))
            completed=subprocess.run([sys.executable,'-X','utf8','run.py','--cfg_file',str(cfg)],cwd=ROOT,env=env,
                capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)
            contract=json.loads((task/'contract.json').read_text())
            self.assertEqual(contract['continuation_context']['parent_task_id'],'old-task')
            self.assertEqual(contract['authoritative_user_inputs'],['total mass 190 kg','Add analysis'])
            rounds=[json.loads(p.read_text()) for p in sorted((task/'research_monitor').glob('round_*.json'))]
            solved=rounds[0]['nodes'][0]
            self.assertEqual([t['tool'] for t in solved['tools']],['read_inherited_file','reuse_inherited_file','Python_code_interpreter'])
            self.assertEqual(solved['tools'][-1]['execution']['exit_code'],0)
            self.assertEqual(solved['evaluation']['decision'],'complete')
            self.assertEqual((task/'node_1/model.py').read_bytes(),before)
            self.assertTrue((task/'node_1/dependency.py').is_file())
            self.assertTrue(any(e['action']=='unchanged_declared' for e in json.loads((task/'node_1/inheritance_usage.json').read_text())['events']))
            self.assertTrue((task/'final/model.py').is_file())
            self.assertEqual(json.loads((task/'completion.json').read_text())['status'],'partial') # Missing new report remains missing.

    def run_variant(self, temporary, mode=None, rounds=1, drafts=1):
        root=Path(temporary);sdk=root/'sdk';sdk.mkdir()
        shutil.copyfile(ROOT/'tests/mock_openai_runtime.py',sdk/'openai.py')
        query=root/'query.txt';query.write_text('Synthetic integration test: total mass 190 kg. Generate model.py, results.csv and report.pdf.')
        config=dict(llm=dict(api_key='offline-fixture',base_url='http://offline.invalid',model='mock-default',
            model_overrides={role:'mock-'+name for role,name in [('clarifier','clarifier'),('supervisor','supervisor'),
            ('critic','critic'),('critic_strong','critic'),('promoter','promoter'),('promoter_strong','promoter'),('summarizer','summarizer')]}),
            pipeline=dict(query_file=str(query),output_path=str(root/'out'),parallel_processes=1,max_rounds=rounds),
            mcts=dict(draft_expansion=drafts,revise_expansion=1),skills={'enabled':False},visualization={'enabled':False},
            landau=dict(prior_enabled=False,library_enabled=False,workflow_enabled=False,wisdom_save_enabled=False),
            tools=dict(python_enabled=True,web_enabled=False,codata_enabled=False),finalization=dict(enabled=True,max_attempts=1))
        cfg=root/'config.yaml';cfg.write_text(yaml.safe_dump(config))
        env=dict(os.environ,PYTHONPATH=str(sdk)+os.pathsep+os.environ.get('PYTHONPATH',''),PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PHY_TEST_TRACE=str(root/'api-trace.jsonl'))
        if mode:env[mode]='1'
        completed=subprocess.run([sys.executable,'-X','utf8','run.py','--cfg_file',str(cfg)],cwd=ROOT,env=env,
            capture_output=True,text=True,encoding='utf-8',timeout=40)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)
        return root/'out/query'

    def test_actual_pipeline_spawns_executes_reviews_finalizes_and_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sdk=root/'sdk';sdk.mkdir()
            shutil.copyfile(ROOT/'tests/mock_openai_runtime.py',sdk/'openai.py')
            query=root/'query.txt';query.write_text('Synthetic integration test: total mass 190 kg. Generate model.py, results.csv and report.pdf.')
            config=dict(llm=dict(api_key='offline-fixture',base_url='http://offline.invalid',model='mock-default',
                model_overrides={role:'mock-'+name for role,name in [('clarifier','clarifier'),('supervisor','supervisor'),
                ('critic','critic'),('critic_strong','critic'),('promoter','promoter'),('promoter_strong','promoter'),('summarizer','summarizer')]}),
                pipeline=dict(query_file=str(query),output_path=str(root/'out'),parallel_processes=1,max_rounds=1),
                mcts=dict(draft_expansion=1,revise_expansion=1),skills={'enabled':False},visualization={'enabled':False},
                landau=dict(prior_enabled=False,library_enabled=False,workflow_enabled=False,wisdom_save_enabled=False),
                tools=dict(python_enabled=True,web_enabled=False,codata_enabled=False),finalization=dict(enabled=True,max_attempts=1))
            cfg=root/'config.yaml';cfg.write_text(yaml.safe_dump(config))
            env=dict(os.environ,PYTHONPATH=str(sdk)+os.pathsep+os.environ.get('PYTHONPATH',''),PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PHY_TEST_TRACE=str(root/'api-trace.jsonl'))
            completed=subprocess.run([sys.executable,'-X','utf8','run.py','--cfg_file',str(cfg)],cwd=ROOT,env=env,
                capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)
            task=root/'out/query';report=json.loads((task/'completion.json').read_text())
            self.assertEqual(report['status'],'passed',json.dumps(report,ensure_ascii=False))
            self.assertEqual(report['hard_parameters'][0]['value'],190)
            self.assertEqual({a['path'] for a in report['artifacts']},{'model.py','results.csv','report.pdf'})
            for row in report['artifacts']:self.assertTrue((task/row['download_path']).is_file())
            self.assertEqual(json.loads((task/'finalization.json').read_text())['state'],'passed')
            self.assertEqual(len(json.loads((task/'trajectory.json').read_text())),2)
            rounds=[json.loads(p.read_text()) for p in sorted((task/'research_monitor').glob('round_*.json'))]
            self.assertEqual(len(rounds),2)
            self.assertIn('score_dimensions',rounds[0]['nodes'][0]['evaluation'])
            self.assertEqual(rounds[0]['nodes'][0]['evaluation']['science_reward'],.95)
            self.assertEqual(rounds[0]['nodes'][0]['tools'][0]['execution']['exit_code'],0)
            self.assertEqual(json.loads((task/'runtime.json').read_text())['state'],'finished')
            self.assertIn('190 kg',(task/'summary.md').read_text())

    def test_empty_critic_response_stays_unscored_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_CRITIC_EMPTY')
            report=json.loads((task/'completion.json').read_text())
            self.assertEqual(report['status'],'partial')
            self.assertFalse((task/'final/model.py').exists())
            record=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())['nodes'][0]
            self.assertFalse(record['evaluation']['score_valid'])
            self.assertIsNone(record['evaluation']['composite_reward'])
            self.assertIn('empty_response',record['evaluation']['critic_error'])
            self.assertTrue((task/'node_1/model.py').is_file())
            self.assertEqual(json.loads((task/'finalization.json').read_text())['state'],'skipped')

    def test_parameter_drift_and_failed_execution_override_high_science_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_MASS_DRIFT')
            report=json.loads((task/'completion.json').read_text())
            self.assertEqual(report['status'],'partial')
            record=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())['nodes'][0]
            self.assertEqual(record['evaluation']['science_reward'],.95)
            self.assertEqual(record['evaluation']['decision'],'to_redraft')
            self.assertEqual(record['evaluation']['integrity_audit']['status'],'violation')
            self.assertIn('代码参数漂移',json.dumps(record['evaluation'],ensure_ascii=False))
            self.assertFalse((task/'final/model.py').exists())
            self.assertEqual(record['tools'][0]['execution']['exit_code'],1)

    def test_code_failure_executes_fresh_repair_node_then_publishes_only_repaired_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_REPAIR_CODE')
            report=json.loads((task/'completion.json').read_text())
            self.assertEqual(report['status'],'passed',report)
            record=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())
            first,repaired=record['nodes']
            self.assertEqual(first['evaluation']['failure_class'],'CODE_FAILURE')
            self.assertNotEqual(first['evaluation']['decision'],'complete')
            self.assertEqual(first['repair_child_id'],repaired['node_id'])
            self.assertEqual(repaired['repair_parent_id'],first['node_id'])
            self.assertEqual(repaired['repair_status'],'succeeded')
            self.assertEqual(repaired['research_stage'],'SIMULATION')
            self.assertEqual(repaired['evaluation']['score_dimensions']['execution']['score'],1)
            self.assertEqual(first['evaluation']['score_dimensions']['execution']['score'],0)
            self.assertEqual(repaired['tools'][-1]['execution']['exit_code'],0)
            model=next(a for a in report['artifacts'] if a['path']=='model.py')
            self.assertEqual(model['source'],f"node_{repaired['node_id']}/model.py")
            self.assertEqual(model['state'],'PUBLISHED')
            self.assertTrue((task/f"node_{first['node_id']}/model.py").exists())
            self.assertEqual(len(repaired['evaluation']['score_dimensions']),5)

    def test_missing_file_is_repaired_by_real_execution_not_by_status_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_REPAIR_FILE')
            record=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())
            first,repaired=record['nodes']
            self.assertEqual(first['evaluation']['failure_class'],'ARTIFACT_FAILURE')
            self.assertFalse((task/f"node_{first['node_id']}/results.csv").exists())
            self.assertTrue((task/f"node_{repaired['node_id']}/results.csv").exists())
            self.assertEqual(json.loads((task/'completion.json').read_text())['status'],'passed')

    def test_critic_protocol_retry_keeps_the_same_solver_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_CRITIC_ONCE')
            record=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())
            self.assertEqual(len(record['nodes']),1)
            n=record['nodes'][0]
            self.assertEqual(len(n['review_history']),2)
            self.assertFalse(n['review_history'][0]['valid'])
            self.assertTrue(n['review_history'][1]['valid'])
            self.assertEqual(n['repair_attempt'],0)
            self.assertEqual(len(n['tools']),1)
            calls=[json.loads(line) for line in (Path(tmp)/'api-trace.jsonl').read_text().splitlines()]
            critics=[c for c in calls if c['model']=='mock-critic']
            self.assertEqual(len(critics),3) # two numerical reviews + one report review
            self.assertEqual(critics[0]['prompt'],critics[1]['prompt'])
            self.assertIn('SIMULATION',critics[0]['prompt'])
            self.assertEqual(json.loads((task/'completion.json').read_text())['status'],'passed')

    def test_persistent_code_failure_stops_at_configured_repair_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_MASS_DRIFT')
            nodes=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())['nodes']
            self.assertEqual(len(nodes),2) # initial + exactly one retry
            self.assertEqual(nodes[-1]['repair_status'],'exhausted')
            self.assertTrue(all(n['evaluation']['decision']!='complete' for n in nodes))
            self.assertFalse((task/'final/model.py').exists())

    def test_killed_worker_rebuilds_pool_and_repair_really_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_WORKER_FAILURE')
            nodes=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())['nodes']
            self.assertEqual(len(nodes),2)
            self.assertEqual(nodes[0]['evaluation']['failure_class'],'EXECUTION_FAILURE')
            self.assertEqual(nodes[1]['repair_status'],'succeeded')
            self.assertEqual(nodes[1]['tools'][-1]['execution']['exit_code'],0)
            self.assertEqual(json.loads((task/'completion.json').read_text())['status'],'passed')

    def test_scientific_review_failure_returns_to_search_without_engineering_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_REVIEW_FAIL')
            nodes=json.loads(sorted((task/'research_monitor').glob('round_*.json'))[0].read_text())['nodes']
            self.assertEqual(len(nodes),1)
            self.assertEqual(nodes[0]['evaluation']['failure_class'],'REVIEW_FAILURE')
            self.assertEqual(nodes[0]['evaluation']['pipeline_action'],'research_revision')
            self.assertEqual(nodes[0]['repair_status'],'not_needed')
            self.assertFalse((task/'final/model.py').exists())

    def test_multiple_rounds_advance_despite_supervisor_repeatedly_requesting_subtask_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=self.run_variant(tmp,'PHY_TEST_NORMAL_REPORT',rounds=4,drafts=2)
            rounds=[json.loads(p.read_text()) for p in sorted((task/'research_monitor').glob('round_*.json'))]
            self.assertEqual(len(rounds),2)
            self.assertTrue(all(n['subtask_id']==1 for n in rounds[0]['nodes']))
            self.assertTrue(all(n['subtask_id']==2 for n in rounds[1]['nodes']))
            self.assertTrue(rounds[1]['dispatch_adjusted'])
            self.assertIn('已验收',rounds[1]['dispatch_reason'])
            self.assertEqual(json.loads((task/'completion.json').read_text())['status'],'passed')
            self.assertEqual(json.loads((task/'finalization.json').read_text())['state'],'skipped')


if __name__=='__main__':unittest.main()
