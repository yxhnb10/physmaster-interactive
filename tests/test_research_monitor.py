import json
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.research_monitor import ResearchMonitor,list_rounds
from web_server import make_handler


class MonitorTests(unittest.TestCase):
    def test_round_outputs_evaluation_files_and_revision_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);monitor=ResearchMonitor(root)
            monitor.start(0,0,{'description':'Build drag model','subtask':{'id':1}},0)
            node=SimpleNamespace(node_id=1,parent=SimpleNamespace(node_id=0),
                subtask_id=1,node_type='draft',status='open',subtask_description='Drag model',
                result=None,evaluation=None,knowledge='')
            monitor.node(node,'solving')
            node.result={'core_results':'v(t) trajectory'}
            (root/'node_1').mkdir(exist_ok=True);(root/'node_1/result.csv').write_text('v\n12\n')
            monitor.node(node,'evaluating',[{'tool':'Python_code_interpreter','arguments':{'code':'x'}}])
            self.assertEqual(list_rounds(root)[0]['nodes'][0]['stage'],'evaluating')
            node.evaluation={'decision':'to_revise','reward':.4,'opinion':'Missing heat constraint'}
            node.status='completed';node.knowledge='Need heating model'
            monitor.finish([node])
            r=list_rounds(root)[0]
            self.assertEqual(r['nodes'][0]['evaluation']['opinion'],'Missing heat constraint')
            self.assertEqual(r['nodes'][0]['result'],node.result)
            self.assertEqual(r['nodes'][0]['artifacts'][0]['path'],'node_1/result.csv')
            self.assertEqual(r['nodes'][0]['tools'][0]['tool'],'Python_code_interpreter')
            monitor.start(1,1,{'description':'Add heat'},0)
            self.assertEqual([r['revision'] for r in list_rounds(root)],[0,1])
            (root/'research_monitor/round_999999.json').write_text('{broken')
            self.assertEqual(len(list_rounds(root)),2)

    def test_round_api_file_download_and_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'node_1').mkdir(exist_ok=True);(root/'node_1/result.csv').write_text('result')
            monitor=ResearchMonitor(root);monitor.start(0,0,{'description':'Goal'},0)
            ident='a'*32
            manager=SimpleNamespace(lock=threading.RLock(),token='test',_get=lambda i:{'task_dir':root})
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(manager))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}/api/tasks/{ident}'
            try:
                with urllib.request.urlopen(url+'/rounds') as r:
                    self.assertEqual(json.load(r)['rounds'][0]['goal'],'Goal')
                with urllib.request.urlopen(url+'/artifact?path=node_1/result.csv') as r:
                    self.assertEqual(r.read(),b'result')
                    self.assertEqual(r.headers['Content-Type'],'application/octet-stream')
                for path in ['../config.yaml','live_updates/active.json','node_1/../../config.yaml']:
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        urllib.request.urlopen(url+'/artifact?path='+path)
                    self.assertEqual(err.exception.code,404)
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
