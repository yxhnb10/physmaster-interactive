import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.live_updates import UpdateInbox, submit_update
from utils.runtime_timing import timed_stage


class LiveUpdateTests(unittest.TestCase):
    def test_atomic_order_receipts_and_session_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            inbox = UpdateInbox(d)
            a = submit_update(d, '质量改为 200 kg')
            b = submit_update(d, '加入热约束')
            items = inbox.pending()
            self.assertEqual([x[1] for x in items], ['质量改为 200 kg', '加入热约束'])
            inbox.acknowledge(items, 1)
            self.assertEqual(inbox.pending(), [])
            self.assertEqual(json.loads((a.parent / 'acks' / a.name).read_text())['revision'], 1)
            inbox.close()
            with self.assertRaises(RuntimeError):
                submit_update(d, 'late')
            self.assertEqual(UpdateInbox(d).pending(), [])

    def test_empty_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            UpdateInbox(d)
            with self.assertRaises(ValueError):
                submit_update(d, '  ')

    def test_replanning_preserves_shared_contract_and_invalidates_old_tree(self):
        # Execute the actual integration method without importing optional LLM,
        # CUDA, network or vector database dependencies.
        source = ast.parse((ROOT / 'core/supervisor.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef))
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in ('_apply_pending_updates', '_replan_updates')]
        module = ast.Module(body=methods, type_ignores=[])
        from utils.live_updates import atomic_json
        class FakeTree:
            def __init__(self, **kw): self.root = SimpleNamespace()
        scope = dict(Path=Path, atomic_json=atomic_json, MCTSTree=FakeTree, timed_stage=timed_stage)
        exec(compile(module, '<actual supervisor method>', 'exec'), scope)
        apply = scope['_apply_pending_updates']
        with tempfile.TemporaryDirectory() as d:
            contract = {'task_description': 'old'}
            old_tree = FakeTree()
            h = SimpleNamespace(update_inbox=UpdateInbox(d),
                rebuild_contract=lambda history: {'task_description': 'new'},
                live_update_history=[], live_revision=0, task_dir=d,
                _seen_queries={"old query"}, _seen_urls={"old url"},
                _tool_call_counts={"web_search": 4, "library_search": 8},
                structured_problem=contract, tree=old_tree, round_counter=5,
                revision_round_budget=8, node_id_counter=19,
                _find_best_trajectory=lambda: [{'node_id': 18}],
                _build_subtasks=lambda: [{'id': 1}],
                _get_prior_knowledge=lambda x: '')
            submit_update(d, 'new constraint')
            h._replan_updates = lambda items: scope['_replan_updates'](h, items)
            self.assertTrue(apply(h))
            self.assertIs(contract, h.structured_problem)
            self.assertEqual(contract['live_updates'], ['new constraint'])
            self.assertIsNot(h.tree, old_tree)
            self.assertEqual(h.max_rounds, 13)
            self.assertEqual(h.revision_start_round, 5)
            self.assertEqual(h._seen_queries, set())
            self.assertEqual(h._seen_urls, set())
            self.assertEqual(h._tool_call_counts, {"web_search": 0, "library_search": 0})
            self.assertEqual(h.node_id_counter, 19)
            self.assertEqual(h.update_inbox.pending(), [])
            self.assertEqual(json.loads((Path(d)/'revisions/revision_0/contract.json').read_text()),
                             {'task_description': 'old'})
            self.assertFalse(apply(h))
            submit_update(d, 'another')
            h.rebuild_contract = lambda history: {}
            with self.assertRaises(ValueError): apply(h)
            self.assertEqual(h.live_revision, 1)
            self.assertEqual(len(h.update_inbox.pending()), 1)

    def test_update_arriving_during_round_prevents_old_completion(self):
        source = ast.parse((ROOT / 'core/supervisor.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef))
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in ('run', '_apply_pending_updates', '_replan_updates')]
        from utils.live_updates import atomic_json
        class FakeTree:
            def __init__(self, **kw): self.root = SimpleNamespace()
            def get_all_nodes(self): return []
            def get_tree_stats(self): return {}
        scope = dict(Path=Path, atomic_json=atomic_json, MCTSTree=FakeTree,
                     Dict=dict, Any=object, timed_stage=timed_stage)
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<integration>', 'exec'), scope)
        with tempfile.TemporaryDirectory() as d:
            h = SimpleNamespace(update_inbox=UpdateInbox(d),
                rebuild_contract=lambda history: {'task_description': 'updated'},
                live_update_history=[], live_revision=0, task_dir=d,
                structured_problem={'task_description': 'initial'},
                tree=FakeTree(), round_counter=0, max_rounds=2,
                revision_round_budget=2, node_id_counter=1, logger=None,
                _seen_queries=set(), _seen_urls=set(), _tool_call_counts={},
                _find_best_trajectory=lambda: [], _build_subtasks=lambda: [],
                _get_prior_knowledge=lambda x: '', _select_leaf_node=lambda: SimpleNamespace(),
                _resolve_dispatch=lambda n: dict(node_type='draft', expansion_count=1,
                    subtask={}, description='', supervisor_dispatch={}),
                _find_full_completion_path=lambda: [1], _find_best_path_nodes=lambda: [],
                _collect_completed_subtasks=lambda: [], _serialize_trajectory=lambda n: [])
            revisions_solved = []
            def expand(**kwargs):
                revisions_solved.append(h.live_revision)
                if len(revisions_solved) == 1:
                    submit_update(d, 'change condition while worker runs')
                return []
            h._expand_and_simulate_nodes = expand
            h._control_checkpoint = lambda: None
            h._apply_pending_updates = lambda: scope['_apply_pending_updates'](h)
            h._replan_updates = lambda items: scope['_replan_updates'](h, items)
            result = scope['run'](h)
            self.assertEqual(revisions_solved, [0, 1])
            self.assertEqual(result['contract_revision'], 1)
            self.assertEqual(result['total_rounds'], 2)


if __name__ == '__main__':
    unittest.main()
