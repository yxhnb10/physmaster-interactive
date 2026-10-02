"""Execute the real solver tool-wiring method without model/network dependencies."""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.continuation import snapshot_research, list_files, read_file, reuse_file, tool_schemas


class ToolTests(unittest.TestCase):
    def test_local_inherited_reads_work_even_with_python_and_network_disabled(self):
        tree=ast.parse((ROOT/'core/theoretician.py').read_text())
        method=next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body
                    if isinstance(n,ast.FunctionDef) and n.name=='solve')
        method.returns=None
        for arg in method.args.args:arg.annotation=None
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';old.mkdir();(old/'conditions.md').write_text('mass 190; all prior conditions')
            current=Path(tmp)/'new';snapshot_research(old,current,'old',[])
            node=current/'node_1';node.mkdir()
            identity=list_files(current)['files'][0]['file_id']
            def model(**kwargs):
                names={x['function']['name'] for x in kwargs['tools']}
                self.assertIn('read_inherited_file',names)
                self.assertNotIn('Python_code_interpreter',names)
                self.assertNotIn('library_search',names)
                result=kwargs['tool_functions']['read_inherited_file'](file_id=identity)
                self.assertIn('mass 190',result['content'])
                return '{"analysis":"read historical conditions without Python"}'
            ns=dict(Path=Path,json=json,tool_schemas=tool_schemas,list_files=list_files,
                read_file=read_file,reuse_file=reuse_file,THEORETICIAN_CORE_TOOLS=[],LIBRARY_TOOLS=[],
                call_model=model,LLMResponseError=type('LLMResponseError',(Exception,),{}))
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'real-solve','exec'),ns)
            class Solver:
                python_enabled=False;skills_enabled=False;library_enabled=False
                prompt_template='{subtask}\n{memory}\n{node_metadata}\n{path}'
                theoretician_system_prompt='test';config_path='unused'
                def _log_tool_call(self,*args):pass
            result,receipts=ns['solve'](Solver(),'Continue',node_metadata={'task_dir':str(current),'output_dir':str(node)})
            self.assertIn('without Python',result)
            self.assertEqual(receipts[0]['tool'],'read_inherited_file')
            self.assertTrue((node/'inheritance_usage.json').is_file())

if __name__=='__main__':unittest.main()
