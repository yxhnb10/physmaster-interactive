import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from web_server import TaskManager, make_handler
from utils.live_updates import UpdateInbox, set_control

FAKE_WORKER = '''import argparse,time,yaml
from pathlib import Path
from utils.live_updates import UpdateInbox
p=argparse.ArgumentParser();p.add_argument('--cfg_file');a=p.parse_args()
c=yaml.safe_load(Path(a.cfg_file).read_text());td=Path(c['pipeline']['output_path'])/'query'
time.sleep(0.4)
inbox=UpdateInbox(td);revision=0
for round_index in range(14):
 inbox.checkpoint(round_index,revision)
 items=inbox.pending()
 if items:
  revision+=1;inbox.acknowledge(items,revision)
 inbox.progress('running',round_index,revision)
 print('round',round_index,flush=True)
 time.sleep(0.1)
inbox.close();(td/'summary.md').write_text('Mock summary, revision '+str(revision))
'''


def until(fn,timeout=6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=fn()
        if value:return value
        time.sleep(.03)
    raise AssertionError('Condition timed out')


class ChatServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'utils').mkdir()
        shutil.copy2(ROOT/'utils/live_updates.py',self.root/'utils/live_updates.py')
        (self.root/'run.py').write_text(FAKE_WORKER)
        cfg=self.root/'config.yaml';cfg.write_text('pipeline: {}\n')
        self.manager=TaskManager(self.root,cfg)

    def tearDown(self):
        self.manager.close()
        for job in self.manager.jobs.values():
            job['process'].wait(timeout=10)
        until(lambda:all(j['state'] in ('finished','failed') for j in self.manager.jobs.values()))
        self.tmp.cleanup()

    def test_early_message_pause_batch_resume_and_final_receipt(self):
        ident=self.manager.create('initial task')
        first=self.manager.update(ident,'early constraint')
        self.assertEqual(first['status'],'waiting')
        self.manager.control(ident,'pause')
        until(lambda:self.manager.snapshot(ident)['state']=='paused')
        self.manager.update(ident,'second constraint')
        time.sleep(.15)
        snap=self.manager.snapshot(ident)
        self.assertEqual([m['status'] for m in snap['messages']],['queued','queued'])
        self.manager.control(ident,'resume')
        until(lambda:all(m['status']=='applied' for m in self.manager.snapshot(ident)['messages']))
        snap=until(lambda:(s if (s:=self.manager.snapshot(ident))['state']=='finished' else None))
        self.assertEqual(snap['revision'],1)
        self.assertIn('Mock summary',snap['summary'])
        with self.assertRaises(ValueError):self.manager.update(ident,'too late')

    def test_single_worker_and_validation(self):
        with self.assertRaises(ValueError):self.manager.create(' ')
        self.manager.create('task')
        with self.assertRaises(ValueError):self.manager.create('second concurrent task')

    def test_http_security_validation_and_start(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.manager))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url) as response:
                html=response.read().decode()
                self.assertIn(self.manager.token,html)
                self.assertNotIn('__TOKEN__',html)
            req=urllib.request.Request(url+'/api/tasks',data=b'{"query":"x"}',headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
            self.assertEqual(exc.exception.code,403)
            req.add_header('X-PhysMaster-Token',self.manager.token)
            with urllib.request.urlopen(req) as response:
                ident=json.load(response)['id']
                self.assertEqual(response.status,201)
            with urllib.request.urlopen(url+'/api/tasks/'+ident) as response:
                self.assertEqual(json.load(response)['query'],'x')
            evil=urllib.request.Request(url,headers={'Host':'untrusted.example'})
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(evil)
            self.assertEqual(exc.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
