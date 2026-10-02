"""Offline API double for the subprocess integration test. No network access.

Not a scientific model/reviewer: fixed 190 kg fixtures exercise actual process,
Python execution, audit, report-only finalization and publication wiring.
"""
import json
import os
from types import SimpleNamespace as NS


_critic_calls = 0

class OpenAI:
    def __init__(self, **kwargs):
        self.chat=NS(completions=NS(create=self.create))

    def create(self, *, model, messages, **kwargs):
        global _critic_calls
        tools=[]
        prompt = messages[1]['content'] if len(messages)>1 else ''
        repaired = '## REPAIR ATTEMPT' in prompt
        if os.environ.get('PHY_TEST_TRACE'):
            with open(os.environ['PHY_TEST_TRACE'], 'a', encoding='utf-8') as trace:
                trace.write(json.dumps(dict(model=model, repaired=repaired, tool_results=sum(m.get('role')=='tool' for m in messages), prompt=prompt[:3000]))+'\n')
        if model=='mock-clarifier':
            content=json.dumps(dict(task_description='Synthetic integration task: total mass 190 kg. Write model and test CSV then PDF.',
                subtasks=[dict(id=1,description='Write and execute model.py and results.csv for the synthetic 190 kg parameter check.',subtask_type='coding',expected_output='model.py, results.csv'),
                          dict(id=2,description='Produce report.pdf citing the accepted synthetic test files.',subtask_type='analysis',expected_output='report.pdf')],
                expected_output=[dict(path=p) for p in ['model.py','results.csv','report.pdf']]))
        elif model=='mock-supervisor':
            content=json.dumps(dict(subtask_id=1,node_type='draft'))
        elif model=='mock-critic':
            _critic_calls += 1
            content=json.dumps(dict(decision='complete',reward=.95,blocking_issues=[],opinion='Synthetic fixture review accepted. Not a real scientific review.',analysis='Check wiring.'))
            if os.environ.get('PHY_TEST_REVIEW_FAIL'):
                content=json.dumps(dict(decision='to_revise',reward=.7,blocking_issues=['Synthetic scientific issue'],opinion='Revise the physical assumptions.'))
            if os.environ.get('PHY_TEST_CRITIC_EMPTY'):content=''
            if os.environ.get('PHY_TEST_CRITIC_ONCE') and _critic_calls == 1:
                content=json.dumps(dict(decision='complete', reward=.95))
        elif model=='mock-promoter':
            content='Synthetic fixture: user mass remained 190 kg; not a physics conclusion.'
        elif model=='mock-summarizer':
            content='# Summary\nOffline integration fixture: total mass = 190 kg. No skydiving survival estimate.\n'
        else:
            if os.environ.get('PHY_TEST_INHERITANCE'):
                contract=json.JSONDecoder().raw_decode(prompt.split('## Authoritative task contract\n',1)[1])[0]
                inherited=contract.get('continuation_context',{})
                assert inherited.get('mode')=='incremental', 'Clarifier lost inherited context'
                baseline=next(r for r in inherited['priority_files'] if r['source_path']=='node_12/model.py')
                tool_messages=[m for m in messages if m.get('role')=='tool']
                if not tool_messages:
                    name,args='read_inherited_file',{'file_id':baseline['file_id']}
                elif len(tool_messages)==1:
                    name,args='reuse_inherited_file',{'file_id':baseline['file_id']}
                elif len(tool_messages)==2:
                    name,args='Python_code_interpreter',{'code':
                        "from pathlib import Path\nfrom model import TOTAL_MASS, OLD_SENTINEL\n"
                        "assert TOTAL_MASS==190 and OLD_SENTINEL=='original-baseline-keep'\n"
                        "Path('results.csv').write_text('parameter,value\\ntotal_mass,190\\n')\n"
                        "print('reused original model and dependency, no rebuild')\n"}
                else:
                    content=json.dumps(dict(analysis='Incremental extension reused original fixed model.',
                        core_results='total mass = 190 kg; original baseline retained',files=['model.py','results.csv'],
                        primary_parameters={'total_mass':{'value':190,'unit':'kg'}},
                        parameter_evidence={'total_mass':{'file':'model.py','symbol':'TOTAL_MASS','unit':'kg'}},
                        reused_files=[dict(file_id=baseline['file_id'],impact_checked=True,unchanged_reason='Added analysis leaves model constants unchanged')]))
                    return NS(choices=[NS(finish_reason='stop',message=NS(content=content,tool_calls=[]))])
                tools=[NS(id='inherit-'+str(len(tool_messages)),type='function',function=NS(name=name,arguments=json.dumps(args)))]
                return NS(choices=[NS(finish_reason='tool_calls',message=NS(content='',tool_calls=tools))])
            report='REPORT FINALIZATION ONLY' in messages[1]['content']
            if os.environ.get('PHY_TEST_NORMAL_REPORT') and '## Current subtask\nProduce report.pdf' in prompt:
                report=True
            if os.environ.get('PHY_TEST_WORKER_FAILURE') and not repaired and not report:
                os._exit(27)
            files=['report.pdf'] if report else ['model.py','results.csv']
            if not any(m.get('role')=='tool' for m in messages):
                code=("from reportlab.pdfgen.canvas import Canvas\nfrom pypdf import PdfReader\n"
                      "c=Canvas('report.pdf');c.drawString(40,800,'Synthetic test only: total mass 190 kg.');c.save()\n"
                      "assert len(PdfReader('report.pdf').pages)==1\nprint('report validated')\n" if report else
                      "from pathlib import Path\nPath('model.py').write_text('TOTAL_MASS=190\\n')\n"
                      "exec(Path('model.py').read_text());assert TOTAL_MASS==190\n"
                      "Path('results.csv').write_text('parameter,value\\ntotal_mass,190\\n');print('executed 190 kg check')\n")
                if os.environ.get('PHY_TEST_MASS_DRIFT') and not report:
                    code=code.replace('TOTAL_MASS=190','TOTAL_MASS=210')
                if os.environ.get('PHY_TEST_REPAIR_CODE') and not report and not repaired:
                    code += "raise RuntimeError('synthetic first-attempt failure')\n"
                if os.environ.get('PHY_TEST_REPAIR_FILE') and not report and not repaired:
                    code += "Path('results.csv').unlink()\n"
                tools=[NS(id='execute',type='function',function=NS(name='Python_code_interpreter',arguments=json.dumps({'code':code})))];content=''
            else:
                content=json.dumps(dict(analysis='Executed synthetic fixture',core_results='total mass = 190 kg; synthetic test only',files=files,
                    primary_parameters={'total_mass':{'value':190,'unit':'kg'}},
                    parameter_evidence={} if report else {'total_mass':{'file':'model.py','symbol':'TOTAL_MASS','unit':'kg'}}))
        return NS(choices=[NS(finish_reason='tool_calls' if tools else 'stop',message=NS(content=content,tool_calls=tools))])
