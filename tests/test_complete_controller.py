"""Controller policies and negative publication cases (offline, no model calls)."""
import json
import tempfile
import unittest
from pathlib import Path
from core.research_lifecycle import stage_for, stage_contract, audit_stage
from core.repair_agent import repair_settings, RepairAgent
from utils.failure_classifier import classify_failure
from utils.artifact_manager import ArtifactManager
from utils.critic_policy import gate_evaluation, DEFAULTS
from utils.node_scores import score_node


class CompletePolicyTests(unittest.TestCase):
    def test_stage_is_scope_not_draft_revise_and_explicit_stage_wins(self):
        self.assertEqual(stage_for({'subtask_type':'coding','node_type':'revise'}),'SIMULATION')
        self.assertEqual(stage_for({'research_stage':'MODEL_BUILDING','subtask_type':'coding'}),'MODEL_BUILDING')
        self.assertEqual(stage_for({'subtask_type':'analysis','expected_output':'report.pdf'}),'FINALIZATION')
        self.assertEqual(stage_for({'description':'convergence validation'}),'VALIDATION')
        self.assertEqual(stage_for({'description':'derive model equations'}),'MODEL_BUILDING')
        self.assertEqual(stage_for({}),'EXPLORATION')
        self.assertIn('EVERY',stage_contract({})['mandatory_rules'][0])

    def test_current_files_block_acceptance_but_later_report_does_not(self):
        task={'subtask_type':'coding','expected_output':'model.py, data.csv'}
        audit={'status':'pass','artifact_checks':[{'path':'model.py','sha256':'a'}, {'path':'data.csv','sha256':'b'}]}
        tools=[{'tool':'Python_code_interpreter','execution':{'exit_code':0}}]
        self.assertEqual(audit_stage(task,audit,tools)['status'],'pass')
        missing=audit_stage(dict(task,expected_output='report.pdf'),audit,tools)
        self.assertEqual(missing['status'],'violation')
        self.assertIn('report.pdf',str(missing['issues']))
        self.assertEqual(audit_stage(task,audit,[])['status'],'unchecked')
        self.assertEqual(audit['status'],'pass') # no mutation

    def test_execution_missing_is_unscored_and_failure_is_not_success(self):
        ev=dict(gate_evaluation({'decision':'complete','reward':.9},DEFAULTS),integrity_audit={'status':'pass'})
        scored=score_node({'analysis':'model'},ev,{'subtask_type':'coding'},[])
        self.assertIsNone(scored['score_dimensions']['execution']['score'])
        self.assertFalse(scored['composite_valid'])
        scored=score_node({'analysis':'model'},ev,{'subtask_type':'coding'},[{'tool':'Python_code_interpreter','execution':{'exit_code':1}}])
        self.assertEqual(scored['score_dimensions']['execution']['score'],0)

    def test_failure_priority_and_repaired_receipt(self):
        good=dict(score_valid=True,review_valid=True,decision='complete',integrity_audit={'status':'pass'})
        tools=[{'tool':'Python_code_interpreter','execution':{'exit_code':1}}]
        self.assertEqual(classify_failure({'analysis':'a'},good,tools)['kind'],'CODE_FAILURE')
        tools.append({'tool':'Python_code_interpreter','execution':{'exit_code':0}})
        self.assertEqual(classify_failure({'analysis':'a'},good,tools)['kind'],'NONE')
        bad=dict(good,critic_error='timeout')
        self.assertEqual(classify_failure({'analysis':'a'},bad,tools)['action'],'reevaluate')
        self.assertEqual(classify_failure({},good)['kind'],'DELIVERY_FAILURE')
        self.assertEqual(classify_failure({'execution_error':'worker exit'},good)['kind'],'EXECUTION_FAILURE')
        self.assertEqual(classify_failure({'analysis':'a'},dict(good,decision='to_revise'))['action'],'research_revision')

    def test_budget_validation_and_disable(self):
        for raw in ({'enabled':'yes'},{'max_node_retries':True},{'max_node_retries':4},{'max_critic_retries':-1}):
            with self.assertRaises(ValueError):repair_settings({'repair':raw})
        agent=RepairAgent(repair_settings({}))
        failure={'kind':'CODE_FAILURE'}
        self.assertTrue(agent.allowed(failure,0));self.assertFalse(agent.allowed(failure,1))
        self.assertFalse(agent.allowed({'kind':'CRITIC_FAILURE'},0))
        self.assertFalse(RepairAgent(repair_settings({'repair':{'enabled':False}})).allowed(failure,0))

    def test_critic_aliases_and_unknown_protocol(self):
        for alias,expected in [('refine','to_revise'),('redraft','to_redraft'),('pass','complete')]:
            self.assertEqual(gate_evaluation({'decision':alias,'reward':.95},DEFAULTS)['decision'],expected)
        result=gate_evaluation({'decision':'whatever','reward':1},DEFAULTS)
        self.assertFalse(result['decision_valid']);self.assertNotEqual(result['decision'],'complete')

    def test_nonempty_or_registered_file_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_1';folder.mkdir();(folder/'a.txt').write_text('nonempty')
            manager=ArtifactManager(root)
            manifest=manager.record_node(1,{'files':['a.txt']},{})
            record=manifest['artifacts'][0]
            self.assertEqual(record['state'],'REGISTERED')
            report={'status':'passed','subtasks':[{'status':'passed','node_id':1}], 'reasons':[],
                    'artifacts':[{'path':'a.txt','source':'node_1/a.txt','sha256':record['sha256'],'status':'available'}]}
            result=manager.publish(report)
            self.assertEqual(result['status'],'partial')
            self.assertFalse((root/'final/a.txt').exists())
            self.assertFalse((root/'a.txt').exists())
            self.assertIn('回执',result['artifacts'][0]['reason'])

    def test_verified_file_from_unselected_node_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'node_2').mkdir();(root/'node_2/a.txt').write_text('data')
            manager=ArtifactManager(root);rec=manager.record_node(2,{'files':['a.txt']},{})['artifacts'][0]
            report={'status':'passed','subtasks':[{'status':'passed','node_id':1}],'reasons':[],
                    'artifacts':[{'path':'a.txt','source':'node_2/a.txt','sha256':rec['sha256'],'status':'available'}]}
            self.assertEqual(manager.publish(report)['status'],'partial')
            self.assertFalse((root/'final/a.txt').exists())

    def test_no_dead_adapters_on_supervisor_path(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/'core/supervisor.py').read_text()
        self.assertIn('pipeline.process(child_node, output, payload, subtask)',source)
        self.assertIn('future = _GLOBAL_POOL.submit(run_theo_node, payload, self.config_path)',source)
        self.assertIn('output = future.result()',source)
        for name in ['execute_v921_full_pipeline','execute_v93_pipeline','v921_core_execute_pipeline']:
            self.assertNotIn(name,source)
        self.assertFalse((root/'utils/v92_reward.py').exists())


if __name__=='__main__':unittest.main()
