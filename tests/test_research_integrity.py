import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.hard_parameters import resolve_parameters,parse_parameter_text,bind_contract
from utils.research_integrity import audit_node,enforce_audit,complete_run,finalize_summary,text_parameter_conflicts
from utils.python_utils import run_python_code,execution_receipt
spec=importlib.util.spec_from_file_location('integrity_test_mcts',ROOT/'core/mcts.py')
mcts=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mcts;spec.loader.exec_module(mcts)
MCTSNode=mcts.MCTSNode


def contract():
    return dict(task_description='Total mass is 190 kg.',hard_parameters=resolve_parameters(['总质量190 kg']),
        validation_policy={'python_enabled':True},subtasks=[dict(id=1,subtask_type='coding')],expected_output=[])


def output():
    return dict(primary_parameters={'total_mass':{'value':190,'unit':'kg'}},
        parameter_evidence={'total_mass':{'file':'model.py','symbol':'TOTAL_MASS','unit':'kg'}},
        core_results='total mass = 190 kg',files=['model.py'])


def accepted_node(root,node_id,sid,files,kind='analysis'):
    folder=root/f'node_{node_id}';folder.mkdir(exist_ok=True)
    data=output();data['files']=files
    audit=audit_node(contract(),data,folder,{'subtask_type':kind},
        [{'tool':'Python_code_interpreter','execution':{'exit_code':0}}])
    return dict(node_id=node_id,subtask_id=sid,result=json.dumps(data),
        critic_feedback=dict(decision='complete',verdict='accept',reward=.95,score_valid=True,integrity_audit=audit))


class IntegrityTests(unittest.TestCase):
    def test_unchecked_evidence_cannot_weaken_redraft_decision(self):
        audit={'status':'unchecked','issues':[],'unchecked':['empty JSON'],'checks':[],'artifact_checks':[]}
        for feedback in [dict(decision='to_redraft',verdict='reject',reward=0,model_decision='to_redraft'),
                         dict(decision='to_redraft',verdict='reject',reward=.2,model_decision='complete')]:
            result=enforce_audit(feedback,audit)
            self.assertEqual(result['decision'],'to_redraft')
            self.assertEqual(result['verdict'],'reject')
            self.assertTrue(result['integrity_blocked'])
            self.assertFalse(result['integrity_adjusted'])
            self.assertEqual(result['decision_adjusted'],feedback['model_decision']!='to_redraft')
        refined=enforce_audit(dict(decision='complete',verdict='accept',model_decision='complete'),audit)
        self.assertEqual(refined['decision'],'to_revise')
        self.assertTrue(refined['integrity_adjusted']);self.assertTrue(refined['decision_adjusted'])

    def test_authority_amendments_units_and_manual_validation(self):
        for text in ['总质量为190公斤','The total mass of the skydiver and suit is 190 kg.',
                     'A 190 kg skydiver + spacesuit + parachute system']:
            self.assertEqual(resolve_parameters([text])[0]['value'],190)
        self.assertEqual(resolve_parameters(['总质量190 kg','质量改为200 kg'])[0]['value'],200)
        with self.assertRaises(ValueError):resolve_parameters(['总质量190 kg'],'total_mass = 210 kg')
        self.assertEqual(resolve_parameters(['总质量190 kg'],'total_mass = 190000 g')[0]['value'],190000)
        for text in ['mass = nan kg','mass = -1 kg','mass = 190 m','mass = 190 kg\nmass = 200 kg']:
            with self.assertRaises(ValueError):parse_parameter_text(text)

    def test_contract_preserves_user_parameters_and_current_pdf_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            c=dict(task_description='Model incorrectly extracted m=210 kg',hard_parameters=[{'value':210}],
                subtasks=[dict(id=1,subtask_type='coding')],expected_output=[dict(format='PDF',
                    path=r'C:\old\outputs\ab4dd392\query\report.pdf')])
            bind_contract(c,resolve_parameters(['总质量190 kg']),['Required files:\n- model.py\n- results.csv'],root)
            self.assertEqual(c['hard_parameters'][0]['value'],190)
            self.assertEqual({a['path'] for a in c['expected_output']},{'report.pdf','model.py','results.csv'})
            self.assertTrue(all(a['resolved_path'].startswith(str(root)) for a in c['expected_output']))
            self.assertEqual(c['subtasks'][-1]['subtask_type'],'analysis')

    def test_210kg_code_cannot_pass_even_with_high_critic_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'model.py').write_text('TOTAL_MASS=210\n')
            audit=audit_node(contract(),output(),root,{'subtask_type':'coding'},
                [{'tool':'Python_code_interpreter','execution':{'exit_code':0}}])
            result=enforce_audit({'decision':'complete','verdict':'accept','reward':.99},audit)
            self.assertEqual(audit['status'],'violation')
            self.assertEqual(result['decision'],'to_redraft')
            self.assertTrue(result['integrity_blocked'])

    def test_alias_subtasks_and_explicit_files_survive_malformed_model_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=dict(task_description='test',expected_output='unstructured model text',
                   **{'sub-tasks':[{'id':'bad','subtask_type':'CODING'}, {'id':1,'description':'next'}]})
            bind_contract(c,[],['Required files:\n- report.pdf'],Path(tmp))
            self.assertEqual([s['id'] for s in c['subtasks']],[1,2,3])
            self.assertEqual(c['subtasks'][0]['subtask_type'],'coding')
            self.assertEqual(c['expected_output'][0]['path'],'report.pdf')
            self.assertNotIn('sub-tasks',c)

    def test_primary_scenarios_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'model.py').write_text('TOTAL_MASS=190\n')
            data=output();data['primary_cases']=[{'name':'with TPS','parameters':{'total_mass':210}}]
            tools=[{'tool':'Python_code_interpreter','execution':{'exit_code':0}}]
            self.assertEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'violation')
            data.pop('primary_cases');data['supplementary_cases']=[{'name':'extra mass sensitivity','parameters':{'total_mass':210}}]
            data['core_results']+='\nSupplementary sensitivity: mass=210 kg'
            self.assertEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'pass')
            data.pop('parameter_evidence')
            self.assertEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'unchecked')

    def test_failed_execution_and_missing_files_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'model.py').write_text('TOTAL_MASS=190\n')
            data=output();tools=[{'tool':'Python_code_interpreter','execution':{'exit_code':1}}]
            self.assertNotEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'pass')
            tools.append({'tool':'Python_code_interpreter','execution':{'exit_code':0}})
            self.assertEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'pass')
            data['files'].append('missing.pdf')
            self.assertEqual(audit_node(contract(),data,root,{'subtask_type':'coding'},tools)['status'],'violation')

    def test_python_receipts_capture_real_exit_and_unicode(self):
        good=run_python_code("print('中文')")
        self.assertEqual(execution_receipt(good)['exit_code'],0);self.assertIn('中文',good)
        failed=run_python_code("print(123);raise RuntimeError('bad')")
        self.assertEqual(execution_receipt(failed)['exit_code'],1)
        self.assertIn('123',failed)

    def test_missing_analysis_pdf_stays_partial_and_accepted_files_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_12';folder.mkdir()
            (folder/'model.py').write_text('TOTAL_MASS=190\n')
            (folder/'results.csv').write_text('h,n\n87,5\n')
            node=accepted_node(root,12,1,['model.py','results.csv'],'coding')
            c=contract();c['subtasks'].append({'id':2,'subtask_type':'analysis'})
            c['expected_output']=[{'path':name} for name in ['model.py','results.csv','report.pdf']]
            report=complete_run(c,[node],root,'round_budget_exhausted')
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['subtasks'][1]['status'],'not_started')
            self.assertEqual(report['artifacts'][2]['status'],'missing')
            self.assertEqual((root/'model.py').read_text(),'TOTAL_MASS=190\n')
            self.assertFalse((root/'report.pdf').exists())

    def test_deliverables_from_rejected_branch_or_changed_files_are_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_17';folder.mkdir();(folder/'model.py').write_text('TOTAL_MASS=190\n')
            node=accepted_node(root,17,1,['model.py'],'coding');c=contract();c['expected_output']=[{'path':'model.py'}]
            self.assertEqual(complete_run(c,[node],root,'all_subtasks_completed')['status'],'passed')
            node['critic_feedback']['verdict']='refine'
            self.assertEqual(complete_run(c,[node],root,'all_subtasks_completed')['status'],'partial')
            node['critic_feedback']['verdict']='accept'
            (root/'model.py').unlink();(folder/'model.py').write_text('TOTAL_MASS=210\n')
            self.assertEqual(complete_run(c,[node],root,'all_subtasks_completed')['status'],'partial')
            self.assertFalse((root/'model.py').exists())
            node['critic_feedback']['decision']='to_revise'
            self.assertEqual(complete_run(c,[node],root,'round_budget_exhausted')['artifacts'][0]['status'],'missing')

    def test_missing_or_invalid_pdf_is_not_manufactured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'node_1';folder.mkdir();(folder/'report.pdf').write_text('PDF will be written later')
            node=accepted_node(root,1,1,['report.pdf']);c=contract();c['expected_output']=[{'path':'report.pdf'}]
            report=complete_run(c,[node],root,'all_subtasks_completed')
            self.assertEqual(report['status'],'partial')
            self.assertEqual(node['critic_feedback']['integrity_audit']['status'],'violation')
            self.assertEqual(report['artifacts'][0]['status'],'missing')
            self.assertFalse((root/'report.pdf').exists())

    def test_previous_case_summary_mass_drift_forces_provisional_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'summary.md'
            text='# Summary\nTPS: \\(m=210\\) kg, body CdA = 0.75 m²; parametric survivability not proven.\n'
            self.assertTrue(text_parameter_conflicts(text,contract()['hard_parameters']))
            path.write_text(text)
            report=dict(status='passed',hard_parameters=contract()['hard_parameters'],artifacts=[],reasons=[],stop_reason='all_subtasks_completed')
            finalize_summary(path,report)
            self.assertEqual(report['status'],'partial')
            self.assertIn('部分完成',path.read_text());self.assertIn('total_mass = 190 kg',path.read_text())

    def test_mcts_cannot_upgrade_high_reward_or_conflicting_verdict(self):
        parent=MCTSNode(node_id=1,subtask_id=1,node_type='draft',subtask_description='test')
        child=MCTSNode(node_id=2,subtask_id=1,node_type='revise',subtask_description='test',knowledge='m=210 result',
            evaluation={'decision':'to_revise','verdict':'accept','reward':.99,'score_valid':True,'integrity_audit':{'status':'pass'}})
        parent.add_child(child)
        self.assertFalse(child.is_subtask_complete())
        child.backpropagate(.99)
        self.assertNotIn('Reviewed Knowledge',parent.knowledge or '')

    def test_revision_of_prerequisite_invalidates_later_acceptance(self):
        source=ast.parse((ROOT/'core/supervisor.py').read_text());cls=next(n for n in source.body if isinstance(n,ast.ClassDef))
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_count_completed_subtasks_in_path')
        scope=dict(MCTSNode=MCTSNode,List=list,Tuple=tuple)
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual path completeness>','exec'),scope)
        feedback=dict(decision='complete',verdict='accept',score_valid=True,integrity_audit={'status':'pass'})
        nodes=[MCTSNode(node_id=i,subtask_id=sid,node_type='draft',subtask_description='test',evaluation=dict(feedback)) for i,sid in [(1,1),(2,2),(3,1)]]
        host=SimpleNamespace(subtasks=[{'id':1},{'id':2}])
        self.assertEqual(scope['_count_completed_subtasks_in_path'](host,nodes),(1,{1}))
        c=contract();c['subtasks']=[{'id':1},{'id':2}]
        with tempfile.TemporaryDirectory() as tmp:
            trajectory=[{'node_id':n.node_id,'subtask_id':n.subtask_id,'critic_feedback':n.evaluation} for n in nodes]
            report=complete_run(c,trajectory,Path(tmp),'round_budget_exhausted')
            self.assertEqual(report['subtasks'][1]['status'],'not_started')

    def test_cli_does_not_force_advance_after_failures(self):
        source=ast.parse((ROOT/'core/supervisor.py').read_text());cls=next(n for n in source.body if isinstance(n,ast.ClassDef))
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_default_next_subtask_id')
        scope={};exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual default dispatch>','exec'),scope)
        host=SimpleNamespace(subtasks=[{'id':1},{'id':2}],update_inbox=None,
            _count_failures_for_subtask=lambda sid:999,_get_next_subtask=lambda sid:{'id':2})
        node=SimpleNamespace(node_type='revise',subtask_id=1,is_subtask_complete=lambda:False)
        self.assertEqual(scope['_default_next_subtask_id'](host,node,'complete'),1)


if __name__=='__main__':unittest.main()
