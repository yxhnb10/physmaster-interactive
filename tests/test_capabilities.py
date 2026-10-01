import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_theoretician():
    spec = importlib.util.spec_from_file_location('testable_theoretician', ROOT/'core/theoretician.py')
    module = importlib.util.module_from_spec(spec)
    # Network clients are replaced; the actual tool-selection and invocation code runs.
    with patch.dict(sys.modules, {
        'LANDAU.library': SimpleNamespace(LibraryRetriever=Mock()),
        'utils.llm_client': SimpleNamespace(call_model=Mock(return_value='test result')),
    }):
        spec.loader.exec_module(module)
    return module


class CapabilityToolTests(unittest.TestCase):
    def test_disabled_python_skills_and_arxiv_are_not_exposed_or_invokable(self):
        module = load_theoretician()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)/'config.yaml'
            cfg.write_text(yaml.safe_dump({'tools':{'python_enabled':False},
                'skills':{'enabled':False}, 'landau':{'library_enabled':False}}))
            solver = module.Theoretician(prompts_path=str(ROOT/'prompts'), config_path=str(cfg))
            with patch.object(module, 'run_python_code') as python, \
                 patch.object(module, 'load_skill_specs') as skills, \
                 patch.object(module, 'build_skill_brief_prompt') as brief:
                solver.solve('Solve the task')
                call = module.call_model.call_args.kwargs
                self.assertEqual(call['tools'], [])
                self.assertEqual(call['tool_functions'], {})
                self.assertIn('Do not claim code', call['user_prompt'])
                python.assert_not_called();skills.assert_not_called();brief.assert_not_called()
                module.LibraryRetriever.assert_not_called()

    def test_python_can_run_independently_of_skills(self):
        module = load_theoretician()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)/'config.yaml'
            cfg.write_text(yaml.safe_dump({'tools':{'python_enabled':True},
                'skills':{'enabled':False}, 'landau':{'library_enabled':False}}))
            solver = module.Theoretician(prompts_path=str(ROOT/'prompts'), config_path=str(cfg))
            module.call_model.side_effect = lambda **kw: kw['tool_functions']['Python_code_interpreter'](code='print(2)')
            with patch.object(module, 'run_python_code', return_value='2') as python:
                result, log = solver.solve('Calculate', node_metadata={'output_dir':tmp})
                self.assertEqual(result, '2')
                python.assert_called_once_with(cwd=tmp, code='print(2)')
                self.assertEqual([x['tool'] for x in log], ['Python_code_interpreter'])
            self.assertEqual([t['function']['name'] for t in module.call_model.call_args.kwargs['tools']],
                             ['Python_code_interpreter'])

    def test_skills_use_the_task_config_and_can_run_without_python(self):
        module = load_theoretician()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'custom_skills'/'example';folder.mkdir(parents=True)
            (folder/'SKILL.md').write_text('---\nname: task-local-example\n---\nTask local guidance.\n')
            cfg = Path(tmp)/'config.yaml'
            cfg.write_text(yaml.safe_dump({'tools':{'python_enabled':False},
                'skills':{'enabled':True,'roots':[str(folder.parent)]},
                'landau':{'library_enabled':False}}))
            solver = module.Theoretician(prompts_path=str(ROOT/'prompts'), config_path=str(cfg))
            module.call_model.side_effect = lambda **kw: kw['tool_functions']['load_skill_specs'](skill_names=['task-local-example'])
            result, log = solver.solve('Follow a skill')
            self.assertIn('Task local guidance.', result)
            self.assertIn('task-local-example', module.call_model.call_args.kwargs['user_prompt'])
            self.assertEqual([x['tool'] for x in log], ['load_skill_specs'])
            self.assertEqual([t['function']['name'] for t in module.call_model.call_args.kwargs['tools']],
                             ['load_skill_specs'])

    def test_web_and_arxiv_and_codata_registrations_are_independent(self):
        tree = ast.parse((ROOT/'core/supervisor.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='_kb_tool_functions')
        scope = {'MCTSNode':object, 'Dict':dict, 'Any':object}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual supervisor tool registry>', 'exec'), scope)
        supervisor = SimpleNamespace(landau_library_enabled=True, landau_prior_enabled=False,
            web_enabled=False, codata_enabled=True, _log_tool_call=Mock(),
            _library_search=Mock(return_value='paper'), _codata_lookup=Mock(return_value='constant'),
            _web_search=Mock(side_effect=AssertionError('disabled web search invoked')))
        tools = scope['_kb_tool_functions'](supervisor, 'critic', None)
        self.assertEqual(set(tools), {'library_search','codata_lookup'})
        self.assertEqual(tools['library_search'](query='q'), 'paper')
        self.assertEqual(tools['codata_lookup'](name='speed_of_light'), 'constant')
        supervisor._web_search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
