import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.research_integrity import audit_node, enforce_audit


def load_llm():
    spec = importlib.util.spec_from_file_location('finalization_test_llm', ROOT/'utils/llm_client.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=Mock())}):
        spec.loader.exec_module(module)
    return module


def completion(content='', calls=None, finish='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content=content, tool_calls=calls or []))])


def tool(code="print('ok')"):
    return SimpleNamespace(id='call1', type='function', function=SimpleNamespace(
        name='Python_code_interpreter', arguments=json.dumps({'code': code})))


def client_with(module, responses):
    client = module.LLMClient.__new__(module.LLMClient)
    client.model = 'mock'; client.model_overrides = {}
    # Copy each payload so later message appends do not change the test evidence.
    requests = []
    answers = iter(responses)
    def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        answer = next(answers)
        if isinstance(answer, Exception): raise answer
        return answer
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, requests


class FinalizationTests(unittest.TestCase):
    def test_budget_reserves_final_answer_after_real_tool_writes(self):
        module = load_llm()
        final = json.dumps({'analysis': 'Files written; further validation pending.', 'files': ['model.py']})
        client, requests = client_with(module, [completion(calls=[tool()]) for _ in range(7)] + [completion(final)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'model.py'
            def execute(**kwargs):
                path.write_text('TOTAL_MASS=190\n', encoding='utf-8')
                return '[PhysMaster execution: exit_code=0]\nwritten model.py'
            executor = Mock(side_effect=execute)
            result = client.call_with_tools('Return JSON', 'Solve', tools=[{'type': 'function'}],
                tool_functions={'Python_code_interpreter': executor})
            self.assertEqual(json.loads(result)['files'], ['model.py'])
            self.assertEqual(path.read_text(), 'TOTAL_MASS=190\n')
            self.assertEqual(executor.call_count, 7)
        self.assertEqual(len(requests), 8)
        self.assertTrue(all(r['tools'] for r in requests[:-1]))
        self.assertIsNone(requests[-1]['tools'])
        self.assertIn('Tools are disabled', requests[-1]['messages'][-1]['content'])
        self.assertEqual(sum(m['role'] == 'tool' for m in requests[-1]['messages']), 7)

    def test_early_final_and_markdown_format_need_no_extra_call(self):
        module = load_llm()
        client, requests = client_with(module, [completion('# Unaccepted attempt\nMissing PDF.')])
        self.assertEqual(client.call_with_tools('Return markdown', 'Summarize'), '# Unaccepted attempt\nMissing PDF.')
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(requests[0]['messages']), 2)

    def test_empty_and_truncated_final_do_not_return_prior_planning(self):
        module = load_llm()
        for answer, reason in [(completion(''), 'empty_response'),
                               (completion('{"analysis":', finish='length'), 'truncated_response')]:
            client, requests = client_with(module, [completion('I will compute everything.', [tool()]), answer])
            with self.assertRaises(module.LLMResponseError) as caught:
                client.call_with_tools('Return JSON', 'Solve', max_tool_calls=2,
                    tool_functions={'Python_code_interpreter': lambda **kw: 'executed'})
            self.assertEqual(caught.exception.diagnostic['reason'], reason)
            self.assertEqual(caught.exception.diagnostic['round'], 2)
            self.assertNotIn('I will compute', caught.exception.partial_response)

    def test_final_round_tool_request_and_truncated_tool_request_never_execute(self):
        module = load_llm()
        for budget, answer, reason in [(1, completion(calls=[tool()]), 'unexpected_tool_calls'),
                                      (2, completion(calls=[tool()], finish='length'), 'truncated_tool_request')]:
            client, requests = client_with(module, [answer])
            executor = Mock()
            with self.assertRaises(module.LLMResponseError) as caught:
                client.call_with_tools('s', 'q', max_tool_calls=budget,
                    tool_functions={'Python_code_interpreter': executor})
            self.assertEqual(caught.exception.diagnostic['reason'], reason)
            executor.assert_not_called()

    def test_budget_one_and_invalid_budgets(self):
        module = load_llm()
        client, requests = client_with(module, [completion('done')])
        self.assertEqual(client.call_with_tools('s', 'q', max_tool_calls=1), 'done')
        self.assertIsNone(requests[0]['tools'])
        for budget in [0, -1, True, 1.5]:
            with self.assertRaises(ValueError):client.call_with_tools('s', 'q', max_tool_calls=budget)
        self.assertEqual(len(requests), 1)

    def test_single_call_rejects_empty_and_truncated_answers(self):
        module = load_llm()
        for answer in [completion(''), completion('partial', finish='length')]:
            client, requests = client_with(module, [answer])
            with self.assertRaises(module.LLMResponseError):client.call_without_tools('s', 'q')

    def test_solver_retains_real_execution_and_files_on_delivery_or_api_failure(self):
        module = load_llm()
        spec = importlib.util.spec_from_file_location('finalization_test_solver', ROOT/'core/theoretician.py')
        solver_module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'utils.llm_client': module,
                                      'LANDAU.library': SimpleNamespace(LibraryRetriever=Mock())}):
            spec.loader.exec_module(solver_module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root/'config.yaml'
            cfg.write_text('skills:\n  enabled: false\nlandau:\n  library_enabled: false\n')
            solver = solver_module.Theoretician(prompts_path=str(ROOT/'prompts'), config_path=str(cfg))
            for final in [completion(''), completion('{"analysis":', finish='length'), RuntimeError('API unavailable')]:
                code = "from pathlib import Path\nPath('model.py').write_text('TOTAL_MASS=190\\n')\nprint('written')"
                client, requests = client_with(module, [completion(calls=[tool(code)]), final])
                def run_model(**kwargs):
                    kwargs['max_tool_calls'] = 2
                    return client.call_with_tools(**{k:v for k,v in kwargs.items() if k != 'config_path'})
                with patch.object(solver_module, 'call_model', side_effect=run_model):
                    raw, receipts = solver.solve('Model total mass 190 kg', node_metadata={'output_dir': tmp})
                data = json.loads(raw)
                self.assertEqual(data['delivery_status'], 'failed')
                self.assertTrue(data['delivery_error'])
                self.assertEqual(receipts[0]['execution']['exit_code'], 0)
                self.assertEqual((root/'model.py').read_text(), 'TOTAL_MASS=190\n')
                self.assertEqual(data['files'], [])
                self.assertEqual(data['primary_parameters'], {})
                audit = audit_node({}, data, root, {'subtask_type': 'analysis'}, receipts)
                result = enforce_audit({'decision': 'complete', 'reward': .99}, audit)
                self.assertEqual(audit['status'], 'unchecked')
                self.assertTrue(result['integrity_blocked'])
                self.assertNotEqual(result['decision'], 'complete')


if __name__ == '__main__': unittest.main()
