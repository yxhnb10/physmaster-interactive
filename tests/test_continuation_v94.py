"""Real snapshots, source access, dependency copying and continuation isolation."""
import hashlib
import json
import shutil
import sys
import os
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.continuation import (snapshot_research, attach_context, read_file, reuse_file,
    list_files, get_record, inheritance_view, finish_reuse, baseline_previews, file_hash)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old, self.new = self.root/'old/query', self.root/'new/query'
        node = self.old/'node_12';node.mkdir(parents=True)
        (node/'model.py').write_text('from support.constants import MASS\nTOTAL_MASS=MASS\n')
        (node/'support').mkdir();(node/'support/constants.py').write_text('MASS=190\n')
        (node/'raw.csv').write_text('mass\n190\n')
        (node/'empty.txt').write_text('')
        failed=self.old/'node_10';failed.mkdir();(failed/'failure.json').write_text('{"reward":0,"error":"failed"}')
        (self.old/'summary.md').write_text('old summary\n'+'long history '*2000)
        (self.old/'contract.json').write_text(json.dumps({'task_description':'original','subtasks':[{'id':1,'description':'model already built'}]}))
        (node/'artifact_manifest.json').write_text(json.dumps(dict(node_accepted=True,
            artifacts=[dict(path='node_12/model.py',sha256=file_hash(node/'model.py'),state='VERIFIED')])) )
        self.snapshot()
        self.node=self.new/'node_1';self.node.mkdir()

    def tearDown(self):self.tmp.cleanup()

    def snapshot(self):return snapshot_research(self.old,self.new,'aaa',['old condition','new condition'])
    def model(self):return next(r for r in list_files(self.new,query='model.py')['files'])

    def test_every_regular_output_retained_including_empty_failure_and_full_summary(self):
        m=json.loads((self.new/'inheritance.json').read_text())
        files={r['source_path']:r for r in m['files']}
        self.assertIn('node_10/failure.json',files)
        self.assertEqual(files['node_12/empty.txt']['bytes'],0)
        self.assertEqual((self.new/files['summary.md']['snapshot_path']).read_bytes(),(self.old/'summary.md').read_bytes())
        self.assertEqual(files['node_12/model.py']['historical_status'],'accepted')
        self.assertEqual(files['node_10/failure.json']['historical_status'],'unverified')

    def test_old_directory_deleted_snapshot_remains_readable(self):
        row=self.model();shutil.rmtree(self.old)
        content=read_file(self.new,self.node,row['file_id'])
        self.assertIn('TOTAL_MASS=MASS',content['content'])
        self.assertEqual(inheritance_view(self.new)['read_count'],1)

    def test_deterministic_context_survives_clarifier_omission(self):
        contract={'task_description':'new','subtasks':[{'id':1,'description':'extend only'}]}
        attach_context(contract,self.new)
        self.assertEqual(contract['continuation_context']['parent_task_id'],'aaa')
        self.assertTrue(Path(contract['continuation_context']['manifest_path']).exists())
        self.assertIn('INCREMENTAL CONTINUATION',contract['subtasks'][0]['description'])
        self.assertIn('new_work',contract['continuation_plan'])

    def test_multigeneration_flattened_and_preserves_all_conditions(self):
        third=self.root/'third/query'
        (self.new/'new_result.csv').write_text('new result\n')
        snapshot_research(self.new,third,'bbb',['old condition','new condition','third condition'])
        value=json.loads((third/'inheritance.json').read_text())
        self.assertEqual([s['task_id'] for s in value['sources']],['aaa','bbb'])
        self.assertTrue(any(r['source_task_id']=='aaa' and r['source_path']=='node_12/model.py' for r in value['files']))
        self.assertFalse(any('/inherited/' in r['snapshot_path'] for r in value['files']))
        self.assertEqual(value['conditions'][-1],'third condition')
        shutil.rmtree(self.old);shutil.rmtree(self.new)
        get_record(third,self.model_id(value))

    def model_id(self,value):return next(r['file_id'] for r in value['files'] if r['source_path']=='node_12/model.py')

    def test_reuse_copies_dependencies_without_overwriting_current_receipts(self):
        row=self.model();(self.node/'artifact_manifest.json').write_text('current receipt')
        copied=reuse_file(self.new,self.node,row['file_id'])
        self.assertEqual((self.node/'support/constants.py').read_text(),'MASS=190\n')
        self.assertEqual((self.node/'artifact_manifest.json').read_text(),'current receipt')
        self.assertTrue(any(r['path']=='support/constants.py' for r in copied['copied']))
        self.assertEqual((self.old/'node_12/model.py').read_bytes(),(self.node/'model.py').read_bytes())

    def test_reused_modules_import_without_preexisting_pythonpath(self):
        from utils.python_utils import run_python_code
        reuse_file(self.new,self.node,self.model()['file_id'])
        clean={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
        with patch.dict(os.environ,clean,clear=True):
            result=run_python_code('from model import TOTAL_MASS\nassert TOTAL_MASS==190\nprint("imported")',str(self.node))
        self.assertIn('exit_code=0',result)
        self.assertIn('imported',result)

    def test_current_conflicting_files_not_overwritten(self):
        (self.node/'model.py').write_text('changed current code')
        with self.assertRaises(ValueError):reuse_file(self.new,self.node,self.model()['file_id'])
        self.assertEqual((self.node/'model.py').read_text(),'changed current code')
        self.assertFalse((self.node/'raw.csv').exists())

    def test_corruption_not_read_or_reused(self):
        row=self.model();(self.new/row['snapshot_path']).write_text('tampered')
        with self.assertRaises(ValueError):read_file(self.new,self.node,row['file_id'])
        with self.assertRaises(ValueError):reuse_file(self.new,self.node,row['file_id'])

    def test_snapshot_does_not_grant_current_acceptance(self):
        row=self.model();reuse_file(self.new,self.node,row['file_id'])
        finish_reuse(self.new,self.node,{'reused_files':[dict(file_id=row['file_id'],impact_checked=True,unchanged_reason='Thermal extension does not change mass constant')]})
        view=inheritance_view(self.new)
        self.assertEqual(view['unchanged_count'],1)
        self.assertFalse((self.node/'artifact_manifest.json').exists())
        self.assertFalse((self.new/'final_manifest.json').exists())
        self.assertFalse((self.new/'completion.json').exists())

    def test_baseline_preview_delivery_not_falsely_reported_as_tool_read(self):
        content=baseline_previews(self.new,self.node)
        self.assertIn('TOTAL_MASS=MASS',content[0]['content'])
        view=inheritance_view(self.new)
        self.assertEqual(view['context_count'],1)
        self.assertEqual(view['read_count'],0)

    def test_pagination_and_safe_paths(self):
        page=list_files(self.new,limit=2)
        self.assertEqual(len(page['files']),2)
        self.assertEqual(page['next_offset'],2)
        with self.assertRaises(ValueError):get_record(self.new,'../../private')
        value=json.loads((self.new/'inheritance.json').read_text())
        value['files'][0]['snapshot_path']='../secret'
        (self.new/'inheritance.json').write_text(json.dumps(value))
        with self.assertRaises(ValueError):get_record(self.new,value['files'][0]['file_id'])

    def test_links_block_snapshot(self):
        other=self.root/'linked/query'
        (self.old/'linked.py').symlink_to(self.old/'node_12/model.py')
        with self.assertRaises(ValueError):snapshot_research(self.old,other,'ccc',[])
        self.assertFalse((other/'inheritance.json').exists())

    def test_changed_accepted_source_is_visible_but_not_accepted(self):
        (self.old/'node_12/model.py').write_text('altered')
        other=self.root/'changed/query';snapshot_research(self.old,other,'ddd',[])
        row=list_files(other,query='model.py')['files'][0]
        self.assertEqual(row['historical_status'],'unverified')

    def test_server_logs_retained_credentials_not_copied(self):
        (self.old.parent/'console.log').write_text('original console output')
        (self.old.parent/'task.json').write_text('{"state":"finished"}')
        (self.old.parent/'web_config.yaml').write_text('api_key: secret-fixture')
        other=self.root/'server-records/query';snapshot_research(self.old,other,'eee',[])
        rows=list_files(other,query='run_records')['files']
        self.assertEqual({r['source_path'] for r in rows},{'run_records/console.log','run_records/task.json'})
        self.assertFalse(any(p.name=='web_config.yaml' for p in other.rglob('*')))

    def test_ui_current_acceptance_is_separate_from_historical(self):
        row=self.model();reuse_file(self.new,self.node,row['file_id'])
        (self.node/'artifact_manifest.json').write_text(json.dumps(dict(node_accepted=True,
            artifacts=[dict(node_path='model.py',status='accepted',sha256=row['sha256'])])))
        shown=next(r for r in inheritance_view(self.new)['files'] if r['file_id']==row['file_id'])
        event=next(e for e in shown['usage'] if e['action']=='copied')
        self.assertEqual(event['current_status'],'accepted_unchanged')
        (self.node/'artifact_manifest.json').write_text(json.dumps(dict(node_accepted=True,
            artifacts=[dict(node_path='model.py',status='accepted',sha256='modified-hash')])))
        shown=next(r for r in inheritance_view(self.new)['files'] if r['file_id']==row['file_id'])
        event=next(e for e in shown['usage'] if e['action']=='copied')
        self.assertEqual(event['current_status'],'accepted_modified')

    def test_replan_keeps_inherited_context_and_user_conditions(self):
        old={'task_description':'first','authoritative_user_inputs':['mass 190','extra heat']}
        attach_context(old,self.new)
        new={'task_description':'replanned','authoritative_user_inputs':old['authoritative_user_inputs']+['new limit']}
        attach_context(new,self.new)
        self.assertEqual(new['continuation_context']['manifest_path'],old['continuation_context']['manifest_path'])
        self.assertEqual(new['authoritative_user_inputs'][-1],'new limit')


if __name__=='__main__':unittest.main()
