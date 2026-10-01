import ast
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.critic_policy import DEFAULTS, merge_critic_settings, gate_evaluation
from utils.runtime_timing import timed_stage


class CriticPolicyTests(unittest.TestCase):
    def test_validation_and_ordering(self):
        for value in (-0.1, 1.1, float('nan'), float('inf'), True, '0.9'):
            with self.assertRaises(ValueError): merge_critic_settings(DEFAULTS, {'accept_threshold':value})
        for setting in ({'redraft_threshold':.9}, {'accept_threshold':.6},
                        {'retrieval_enabled':1}, {'unknown':.5}, []):
            with self.assertRaises(ValueError): merge_critic_settings(DEFAULTS, setting)

    def test_score_boundaries_and_no_automatic_promotion(self):
        settings = merge_critic_settings(DEFAULTS, {'accept_threshold':.9,'redraft_threshold':.7})
        self.assertEqual(gate_evaluation({'reward':.699,'decision':'complete'},settings)['decision'],'to_redraft')
        self.assertEqual(gate_evaluation({'reward':.7,'decision':'complete'},settings)['decision'],'to_revise')
        self.assertEqual(gate_evaluation({'reward':.899,'decision':'complete'},settings)['decision'],'to_revise')
        self.assertEqual(gate_evaluation({'reward':.9,'decision':'complete'},settings)['decision'],'complete')
        self.assertEqual(gate_evaluation({'reward':.99,'decision':'to_revise'},settings)['decision'],'to_revise')
        self.assertEqual(gate_evaluation({'reward':.99,'decision':'to_redraft'},settings)['verdict'],'reject')

    def test_invalid_scores_cannot_complete_or_poison_tree(self):
        for value in (None, True, float('nan'), float('inf'), -1, 2, {}, 'bad'):
            result = gate_evaluation({'decision':'complete','reward':value},DEFAULTS)
            self.assertEqual(result['decision'],'to_revise')
            self.assertFalse(result['score_valid'])
            self.assertTrue(math.isfinite(result['reward']))

    def test_actual_critic_call_uses_config_in_prompt_and_gate(self):
        source = ast.parse((ROOT/'core/supervisor.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='_call_critic')
        call_model = Mock(return_value=json.dumps({'decision':'complete','reward':.88,'opinion':'review'}))
        scope = dict(MCTSNode=object, Dict=dict, Any=object, json=json, call_model=call_model, timed_stage=timed_stage)
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual critic integration>','exec'),scope)
        supervisor = SimpleNamespace(structured_problem={'task':'check model'},
            critic_prompt=(ROOT/'prompts/critic_prompt.txt').read_text(),critic_system_prompt='Critic',
            critic_settings=merge_critic_settings(DEFAULTS,{'accept_threshold':.9,'redraft_threshold':.7}),
            revision_round_budget=6,round_counter=1,revision_start_round=0,
            kb_search_tools=[],config_path='unused.yaml',_extract_json_object=json.loads,
            _to_natural_text=lambda x:str(x) if x else '',_kb_tool_functions=lambda *args:{},
            update_inbox=None,tree=SimpleNamespace(get_all_nodes=Mock(side_effect=AssertionError('legacy force accept'))))
        node=SimpleNamespace(result={'core_results':'result'},node_type='revise',node_id=1,subtask_id=1)
        result=scope['_call_critic'](supervisor,node)
        self.assertEqual(result['decision'],'to_revise')
        self.assertTrue(result['threshold_adjusted'])
        self.assertIn('reward >= 0.9',call_model.call_args.kwargs['user_prompt'])
        self.assertIn('0.7 <= reward',call_model.call_args.kwargs['user_prompt'])
        supervisor.critic_settings=merge_critic_settings(DEFAULTS,{'accept_threshold':.8})
        self.assertEqual(scope['_call_critic'](supervisor,node)['decision'],'complete')
        call_model.return_value=json.dumps({'decision':'to_revise','reward':.99})
        self.assertEqual(scope['_call_critic'](supervisor,node)['decision'],'to_revise')

    def test_retrieval_threshold_keeps_boundary_and_can_reject_all(self):
        spec=importlib.util.spec_from_file_location('testable_retrieval_critic',ROOT/'core/retrieval_critic.py')
        module=importlib.util.module_from_spec(spec)
        mocked=Mock(return_value=json.dumps({'scores':[.2,.5,.9]}))
        with patch.dict(sys.modules, {'utils.llm_client':SimpleNamespace(call_model_without_tools=mocked)}):
            spec.loader.exec_module(module)
        results=[{'title':'a'},{'title':'b'},{'title':'c'}]
        self.assertEqual(module.RetrievalCritic(threshold=.5).filter('q',results),results[1:])
        self.assertEqual(module.RetrievalCritic(threshold=.95).filter('q',results),[])
        mocked.side_effect=RuntimeError('service unavailable')
        self.assertEqual(module.RetrievalCritic(threshold=.95).filter('q',results),results)
        with self.assertRaises(ValueError):module.RetrievalCritic(threshold=float('nan'))


if __name__=='__main__': unittest.main()
