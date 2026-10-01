import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.runtime_timing import RunTimer, read_timing, measure_operation


class TimingTests(unittest.TestCase):
    def test_nested_stages_are_exclusive_and_monotonic_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [10.0]
            wall = [100.0]
            timer = RunTimer(tmp, clock=lambda:clock[0], wall_clock=lambda:wall[0])
            clock[0] += 2
            timer.change('theoretician')
            clock[0] += 3
            with self.assertRaises(RuntimeError):
                with timer.stage('critic'):
                    clock[0] += 4
                    wall[0] -= 3600  # Wall clock changes do not affect recorded duration.
                    raise RuntimeError('failed request')
            self.assertEqual(timer.current,'theoretician')
            clock[0] += 1
            timer.finish(False)
            expected={'initializing':2.0,'theoretician':4.0,'critic':4.0}
            self.assertEqual(timer.snapshot()['phases'],expected)
            clock[0] += 100
            self.assertEqual(timer.snapshot()['seconds'],10.0)
            self.assertEqual(json.loads(timer.path.read_text())['state'],'failed')

    def test_parallel_operation_samples_and_crash_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ops=root/'timing_operations';ops.mkdir()
            (root/'runtime.json').write_text(json.dumps(dict(state='running',current='critic',
                phases={'critic':10.0},sampled_at=99.0)))
            for pid,seconds,active in [('1',3.0,['llm:critic']),('2',5.0,[])]:
                (ops/(pid+'.json')).write_text(json.dumps(dict(sampled_at=99.0,active=active,
                    stats={'llm:critic':dict(seconds=seconds,calls=1,failures=0)})))
            (ops/'broken.json').write_text('{partial')
            with patch('utils.runtime_timing.time.time',return_value=100.0):
                current=read_timing(root)
            self.assertEqual(current['operations']['llm:critic']['seconds'],9.0)
            self.assertEqual(current['operations']['llm:critic']['calls'],2)
            self.assertEqual(current['operations']['llm:critic']['running'],1)
            frozen=read_timing(root,terminal=True,finished_at=102.0)
            self.assertEqual(frozen['phases']['critic'],13.0)
            self.assertEqual(frozen['operations']['llm:critic']['seconds'],11.0)
            self.assertEqual(frozen['operations']['llm:critic']['running'],0)
            with patch('utils.runtime_timing.time.time',return_value=10000):
                self.assertEqual(read_timing(root,terminal=True,finished_at=102.0),frozen)

    def test_telemetry_file_lock_does_not_abort_research(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('utils.runtime_timing.atomic_json',side_effect=PermissionError('locked')), \
             patch.dict(os.environ,{'PHY_TIMING_ROOT':tmp}):
            timer=RunTimer(tmp)
            timer.change('summary')
            with measure_operation('tool:web_search'):
                pass
            timer.finish(True)

    def test_actual_llm_loop_records_api_tools_and_failures(self):
        spec=importlib.util.spec_from_file_location('timing_test_llm',ROOT/'utils/llm_client.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'openai':SimpleNamespace(OpenAI=Mock())}):
            spec.loader.exec_module(module)
        client=module.LLMClient.__new__(module.LLMClient)
        client.model='mock';client.model_overrides={}
        tool=SimpleNamespace(id='t1',type='function',function=SimpleNamespace(name='web_search',arguments='{}'))
        def completion(tools,content=''):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(tool_calls=tools,content=content))])
        create=Mock(side_effect=[completion([tool]),completion([], 'done'),RuntimeError('API failed')])
        client.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'PHY_TIMING_ROOT':tmp}):
            result=client.call_with_tools('system','query',role='supervisor',
                tool_functions={'web_search':Mock(side_effect=RuntimeError('search failed'))})
            self.assertEqual(result,'done')
            with self.assertRaises(RuntimeError):client.call_without_tools('s','q',role='clarifier')
            operations=read_timing(tmp)['operations']
            self.assertEqual(operations['llm:supervisor']['calls'],2)
            self.assertEqual(operations['llm:clarifier']['failures'],1)
            self.assertEqual(operations['tool:web_search']['failures'],1)
            self.assertTrue(all(r['running']==0 for r in operations.values()))

    def test_actual_pipeline_summary_success_and_failure_persist_timing(self):
        spec=importlib.util.spec_from_file_location('timing_test_run',ROOT/'run.py')
        module=importlib.util.module_from_spec(spec)
        summarizer=Mock()
        summarizer.write_summary_markdown.side_effect=lambda path,**kw:Path(path).write_text('summary')
        supervisor=Mock();supervisor.run.return_value={'trajectory':[]}
        replacements={
            'core.supervisor':SimpleNamespace(SupervisorOrchestrator=Mock(return_value=supervisor)),
            'core.clarifier':SimpleNamespace(Clarifier=Mock(return_value=SimpleNamespace(
                run=Mock(side_effect=lambda query:{'task_description':query})))),
            'core.summarizer':SimpleNamespace(TrajectorySummarizer=Mock(return_value=summarizer)),
            'core.visualization':SimpleNamespace(generate_vis=Mock()),
        }
        with patch.dict(sys.modules,replacements):spec.loader.exec_module(module)
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);query=root/'query.txt';query.write_text('research')
            cfg=root/'config.yaml';cfg.write_text(yaml.safe_dump(dict(
                pipeline=dict(query_file=str(query),output_path=str(root/'out'),live_updates_enabled=True),
                skills={'enabled':False},landau=dict(prior_enabled=False,workflow_enabled=False),
                visualization={'enabled':True})))
            before=os.environ.get('PHY_TIMING_ROOT')
            module.main(str(cfg))
            report=json.loads((root/'out/query/runtime.json').read_text())
            self.assertEqual(report['state'],'finished')
            self.assertTrue({'clarifying','orchestration','summary','visualization'}<=set(report['phases']))
            self.assertEqual(os.environ.get('PHY_TIMING_ROOT'),before)
            summarizer.write_summary_markdown.side_effect=RuntimeError('summary failed')
            with self.assertRaises(RuntimeError):module.main(str(cfg))
            report=json.loads((root/'out/query/runtime.json').read_text())
            self.assertEqual(report['state'],'failed')
            self.assertIsNone(report['current'])


if __name__=='__main__':unittest.main()
