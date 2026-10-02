import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.research_integrity import (_node_file, normalize_node_paths,
    node_contract_for_solver, audit_node, complete_run)
from utils.python_utils import run_python_code, execution_receipt


def contract(root):
    return dict(current_task_dir=str(root),hard_parameters=[dict(name='total_mass',value=190,unit='kg')],
        validation_policy={'python_enabled':True},subtasks=[dict(id=1,subtask_type='coding')],
        expected_output=[dict(path=name,resolved_path=str(root/name),requested_path=str(root/name))
                         for name in ['model.py','model_log.txt']])


def output(root):
    return dict(primary_parameters={'total_mass':{'value':190,'unit':'kg'}},
        parameter_evidence={'total_mass':{'file':str(root/'model.py'),'symbol':'TOTAL_MASS','unit':'kg'}},
        files=[str(root/'model.py'),{'path':str(root/'model_log.txt'),'description':'execution log'}],
        core_results='Total mass = 190 kg')


class NodeFilePathTests(unittest.TestCase):
    def test_real_execution_archive_claims_normalize_audit_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root)
            receipt=run_python_code("from pathlib import Path\nPath('model.py').write_text('TOTAL_MASS=190\\n')\nPath('model_log.txt').write_text('unit checks pending')",cwd=str(node))
            tools=[{'tool':'Python_code_interpreter','execution':execution_receipt(receipt)}]
            raw=json.dumps(output(root));normalized,corrections=normalize_node_paths(c,raw,node)
            data=json.loads(normalized)
            self.assertEqual(data['files'],['model.py',{'path':'model_log.txt','description':'execution log'}])
            self.assertEqual(data['parameter_evidence']['total_mass']['file'],'model.py')
            self.assertEqual(len(corrections),3)
            self.assertTrue(all(not x['archive_file_present'] for x in corrections))
            self.assertFalse((root/'model.py').exists())  # Normalization copies nothing.
            audit=audit_node(c,normalized,node,{'subtask_type':'coding'},tools)
            self.assertEqual(audit['status'],'pass')
            trajectory=[dict(node_id=1,subtask_id=1,result=normalized,critic_feedback=dict(
                decision='complete',verdict='accept',score_valid=True,reward=.9,integrity_audit=audit))]
            report=complete_run(c,trajectory,root,'all_subtasks_completed')
            self.assertEqual(report['status'],'passed')
            self.assertEqual((root/'model.py').read_bytes(),(node/'model.py').read_bytes())

    def test_parameter_drift_and_failed_execution_still_block_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root)
            (node/'model.py').write_text('TOTAL_MASS=210\n');(node/'model_log.txt').write_text('failed')
            normalized,changes=normalize_node_paths(c,output(root),node)
            self.assertTrue(changes)
            audit=audit_node(c,normalized,node,{'subtask_type':'coding'},
                [{'tool':'Python_code_interpreter','execution':{'exit_code':1}}])
            self.assertEqual(audit['status'],'violation')
            self.assertTrue(any('代码参数漂移' in x for x in audit['issues']))
            self.assertTrue(any('执行失败' in x for x in audit['issues']))

    def test_existing_archive_copy_must_match_current_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root)
            (node/'model.py').write_text('TOTAL_MASS=190\n');(root/'model.py').write_text('TOTAL_MASS=210\n')
            raw=dict(files=[str(root/'model.py')]);result,changes=normalize_node_paths(c,raw,node)
            self.assertEqual(result,raw);self.assertEqual(changes,[])
            shutil.copyfile(node/'model.py',root/'model.py')
            result,changes=normalize_node_paths(c,raw,node)
            self.assertEqual(result['files'],['model.py']);self.assertTrue(changes[0]['archive_file_present'])

    def test_missing_empty_and_unlisted_node_files_cannot_be_replaced_by_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root)
            (root/'model.py').write_text('TOTAL_MASS=190\n')
            raw=dict(files=[str(root/'model.py')])
            self.assertEqual(normalize_node_paths(c,raw,node),(raw,[]))
            (node/'model.py').write_text('')
            self.assertEqual(normalize_node_paths(c,raw,node),(raw,[]))
            (node/'unlisted.py').write_text('TOTAL_MASS=190\n')
            unlisted=dict(files=[str(root/'unlisted.py')])
            self.assertEqual(normalize_node_paths(c,unlisted,node),(unlisted,[]))

    def test_other_nodes_old_runs_foreign_paths_and_traversal_not_corrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'query';root.mkdir();node=root/'node_1';node.mkdir();c=contract(root)
            (node/'model.py').write_text('TOTAL_MASS=190\n')
            for claim in [str(root/'node_2'/'model.py'),str(base/'old_run'/'model.py'),
                          str(root/'subdir'/'..'/'model.py'),r'C:\old_run\query\model.py',
                          '../model.py']:
                raw=dict(files=[claim]);self.assertEqual(normalize_node_paths(c,raw,node),(raw,[]))

    def test_symlink_cannot_supply_current_node_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root)
            external=root/'external.py';external.write_text('TOTAL_MASS=190\n')
            try:(node/'model.py').symlink_to(external)
            except OSError as exc:self.skipTest('Host does not permit symlink creation: '+str(exc))
            raw=dict(files=[str(root/'model.py')]);self.assertEqual(normalize_node_paths(c,raw,node),(raw,[]))

    def test_diagnostics_distinguish_missing_empty_and_wrong_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();(node/'empty.txt').write_text('')
            (root/'exists.txt').write_text('exists')
            for claim,message in [('missing.txt','不存在'),('empty.txt','为空'),(str(root/'exists.txt'),'不在当前节点目录')]:
                with self.assertRaisesRegex(ValueError,message):_node_file(node,claim)

    def test_solver_paths_are_node_scoped_without_mutating_contract_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);node=root/'node_1';node.mkdir();c=contract(root);before=copy.deepcopy(c)
            local=node_contract_for_solver(c,node)
            self.assertEqual(c,before)
            self.assertEqual(local['current_task_dir'],str(root))
            self.assertEqual(local['current_node_dir'],str(node))
            for spec in local['expected_output']:
                self.assertEqual(spec['resolved_path'],str(node/spec['path']))
                self.assertEqual(spec['final_archive_path'],str(root/spec['path']))
                self.assertNotIn('requested_path',spec)
            (node/'model.py').write_text('TOTAL_MASS=190\n');(node/'model_log.txt').write_text('log')
            raw=output(root);before_output=copy.deepcopy(raw)
            normalize_node_paths(c,raw,node)
            self.assertEqual(raw,before_output)
            fake=dict(files=['model.py'],path_corrections=[{'reason':'fabricated'}])
            fixed,changes=normalize_node_paths(c,fake,node)
            self.assertNotIn('path_corrections',fixed);self.assertEqual(changes,[])

    def test_actual_worker_passes_node_working_paths_to_solver(self):
        spec=importlib.util.spec_from_file_location('path_test_theoretician',ROOT/'core/theoretician.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'LANDAU.library':SimpleNamespace(LibraryRetriever=Mock()),
            'utils.llm_client':SimpleNamespace(call_model=Mock(),LLMResponseError=type('LLMResponseError',(RuntimeError,),{}))}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=contract(root);before=copy.deepcopy(c)
            solver=Mock();solver.solve.return_value=('{}',[])
            with patch.object(module,'Theoretician',return_value=solver):
                module.run_theo_node(dict(depth=1,node_id=1,structured_problem=c,
                    subtask={'id':1,'description':'Build model'},task_dir=str(root),node_type='draft'))
            prompt=solver.solve.call_args.kwargs['subtask_description']
            local=json.loads(prompt.split('## Authoritative task contract\n',1)[1].split('\nLater live_updates',1)[0])
            self.assertEqual(local['expected_output'][0]['resolved_path'],str(root/'node_1'/'model.py'))
            self.assertEqual(c,before)


if __name__=='__main__':unittest.main()
