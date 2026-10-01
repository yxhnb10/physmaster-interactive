import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fix_windows_file_lock import ATOMIC_JSON, PROGRESS, patched_source, patch_project


class WindowsFileLockTests(unittest.TestCase):
    def function(self):
        scope={}
        exec(ATOMIC_JSON,scope)
        return scope['atomic_json']

    def test_transient_lock_retried_without_invalidating_old_file(self):
        import os
        real_replace=os.replace
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'progress.json';p.write_text('{"old":true}')
            def replace(tmp,target):
                calls.append(1)
                if len(calls)<=2:
                    self.assertEqual(json.loads(p.read_text()),{'old':True})
                    raise PermissionError('Simulated Windows lock')
                real_replace(tmp,target)
            with patch('os.replace',side_effect=replace), patch('time.sleep') as sleep:
                self.function()(p,{'round':4})
                self.assertEqual(sleep.call_count,2)
            self.assertEqual(json.loads(p.read_text()),{'round':4})
            self.assertEqual(list(Path(d).glob('*.tmp')),[])

    def test_permanent_lock_raises_and_preserves_old_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'contract.json';p.write_text('{"revision":0}')
            with patch('os.replace',side_effect=PermissionError('locked')) as replace,patch('time.sleep'):
                with self.assertRaises(PermissionError):self.function()(p,{'revision':1})
                self.assertEqual(replace.call_count,30)
            self.assertEqual(json.loads(p.read_text()),{'revision':0})
            self.assertEqual(list(Path(d).glob('*.tmp')),[])

    def test_progress_permission_error_is_nonfatal_but_other_errors_raise(self):
        def locked(*args):raise PermissionError('busy')
        scope={'atomic_json':locked}
        exec(PROGRESS.strip(),scope)
        h=SimpleNamespace(root=Path('unused'))
        with patch('builtins.print') as warn:
            scope['progress'](h,'running',3,1)
            scope['progress'](h,'running',4,1)
            self.assertEqual(warn.call_count,1)
        scope['atomic_json']=lambda *a:None
        scope['progress'](h,'running',4,1)
        self.assertFalse(h._progress_write_warned)
        def failed(*args):raise OSError('disk failure')
        scope['atomic_json']=failed
        with self.assertRaises(OSError):scope['progress'](h,'running')

    def test_surgical_patch_preserves_other_functions_and_backup(self):
        source='''def atomic_json(path, value):
    pass
class UpdateInbox:
    def progress(self, state, round_index=0, revision=0):
        pass
    def custom_v6_feature(self):
        return "preserved"
'''
        patched=patched_source(source)
        self.assertIn('return "preserved"',patched)
        self.assertEqual(patched_source(patched),patched)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'utils/live_updates.py';p.parent.mkdir();p.write_text(source)
            patch_project(d)
            backups=list(p.parent.glob('*.backup_*'))
            self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_text(),source)
            self.assertEqual(p.read_text(),patched)

if __name__=='__main__':unittest.main()
