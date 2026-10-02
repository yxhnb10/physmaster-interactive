"""Evidence checks and honest completion reporting; no generated code execution."""
import ast
import copy
import csv
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from pathlib import Path, PureWindowsPath
from utils.live_updates import atomic_json
from utils.hard_parameters import NUMBER, same_value

RESERVED = {'summary.md', 'contract.json', 'completion.json', 'runtime.json', 'task.json',
            'web_config.yaml', 'config.yaml', 'final_manifest.json', 'manifest.json',
            'artifact_manifest.json', 'finalization.json', 'trajectory.json', 'delivery_index.md'}
EXTENSIONS = {'.py', '.pdf', '.csv', '.md', '.txt', '.html', '.json', '.png', '.svg', '.jpg', '.xlsx'}


def expected_artifacts(contract, task_dir):
    items = contract.get('expected_output') or []
    if not isinstance(items, list):
        items = []
    items = list(items)
    for text in contract.get('authoritative_user_inputs',[]):
        explicit_block=False
        for line in text.splitlines():
            header=bool(re.search(r'expected outputs?|required (?:files|deliverables)|deliverables|输出文件|交付文件',line,re.I))
            in_list=explicit_block and bool(re.match(r'\s*[-*]\s|\s*\d+[.)]\s',line))
            if header:explicit_block=True
            elif line.strip() and not in_list:explicit_block=False
            if (header or in_list or re.search(r'生成|produce|generate|create',line,re.I)) and not re.search(r'do not|不要|禁止',line,re.I):
                for name in re.findall(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:pdf|csv|py|md|txt|html|json|png|svg|xlsx)\b',line,re.I):
                    items.append(dict(path=name,format=Path(name).suffix[1:].upper(),description='Explicit user deliverable'))
                if re.search(r'(?:generate|produce|create|生成|制作|输出)[^\n]{0,60}\bPDF\b',line,re.I) and not any(str(x.get('path','')).lower().endswith('.pdf') for x in items if isinstance(x,dict)):
                    items.append(dict(path='research_report.pdf',format='PDF',description='User-requested PDF report'))
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get('path', '')).strip()
        if raw.lower() in ('', 'none', 'n/a'):
            continue
        name = PureWindowsPath(raw).name if '\\' in raw else Path(raw).name
        if Path(name).suffix.lower() not in EXTENSIONS:
            continue
        if name == 'summary.md':
            continue  # The pipeline always writes its own summary after this audit.
        if name in RESERVED or name.startswith('.'):
            raise ValueError('交付文件名与运行记录冲突：' + name)
        if any(r['path'] == name for r in result):
            continue
        result.append(dict(item, path=name, requested_path=raw,
                           resolved_path=str((Path(task_dir)/name).resolve())))
    return result


def _node_file(node_dir, raw):
    if Path(node_dir).is_symlink():raise ValueError('节点目录不能为符号链接')
    root = Path(node_dir).resolve()
    raw = str(raw)
    path = Path(raw)
    if '\\' in raw and os.name != 'nt':
        # A foreign Windows absolute path is never evidence from this run.
        if PureWindowsPath(raw).is_absolute():
            raise ValueError('引用了旧任务或外部绝对路径')
        path = Path(*PureWindowsPath(raw).parts)
    candidate = path if path.is_absolute() else root/path
    if any(part == '..' for part in path.parts):
        raise ValueError('文件路径包含上级目录')
    relative = candidate.relative_to(root) if candidate.is_absolute() and candidate.is_relative_to(root) else path
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('不能将符号链接作为本轮文件证据')
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError('声明路径不在当前节点目录；应核对 '+str(root))
    if not candidate.exists():
        raise ValueError('声明的本节点文件不存在')
    if not candidate.is_file():
        raise ValueError('声明路径不是文件')
    if candidate.stat().st_size == 0:
        raise ValueError('声明的本节点文件为空')
    return candidate


def _constant(expr):
    if isinstance(expr, ast.Constant) and isinstance(expr.value, (int, float)) and not isinstance(expr.value, bool):
        return float(expr.value)
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub)):
        value = _constant(expr.operand)
        return value if isinstance(expr.op, ast.UAdd) else -value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left, right = _constant(expr.left), _constant(expr.right)
        if isinstance(expr.op, ast.Add):return left+right
        if isinstance(expr.op, ast.Sub):return left-right
        if isinstance(expr.op, ast.Mult):return left*right
        return left/right
    raise ValueError('该表达式无法静态核对')


def _bindings(source):
    tree = ast.parse(source)
    values = {}
    def visit(statements, prefix=''):
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        try:values[prefix+target.id] = _constant(statement.value)
                        except (ValueError, TypeError, ZeroDivisionError):values[prefix+target.id] = None
            elif isinstance(statement, ast.ClassDef):
                visit(statement.body, prefix+statement.name+'.')
    visit(tree.body)
    return values


def _file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def text_parameter_conflicts(text, params):
    issues = []
    for p in params:
        if p['name'] != 'total_mass':
            continue
        for line in text.splitlines():
            line=line.replace('\\(','').replace('\\)','').replace('\\mathrm{kg}','kg')
            # Clearly labeled rejected/extra cases are permitted, not primary answers.
            if re.search(r'supplementary|sensitivity|rejected|incorrect|old reference|旧结果|旧参数|敏感性|额外情景|错误值|禁止', line, re.I):
                continue
            pattern = r'(?:total[_ ]mass|mass|总质量|\bm)\s*(?:=|:|：|为|取|is)?\s*(' + NUMBER + r')\s*(kg|千克|公斤)'
            for match in re.finditer(pattern, line, re.I):
                if not same_value(p, {'value':float(match[1]), 'unit':match[2]}):
                    issues.append(f"主结果文本与锁定总质量冲突：{match[1]} {match[2]}；要求 {p['value']:g} {p['unit']}")
    return list(dict.fromkeys(issues))


def parse_output(output):
    if isinstance(output, str):
        try:output=json.loads(output[output.index('{'):output.rindex('}')+1])
        except (ValueError, json.JSONDecodeError):output={}
    return output if isinstance(output, dict) else {}


def node_contract_for_solver(contract, node_dir):
    """Give each solver working paths; final archive paths are program-owned."""
    result=copy.deepcopy(contract)
    root=Path(node_dir).resolve()
    result['current_node_dir']=str(root)
    result['artifact_scope']=dict(generated_files_dir=str(root),archive_dir=str(root.parent),
        files_field='Paths relative to generated_files_dir',
        archiving='The program archives accepted node files; the solver must not write to archive_dir.')
    for spec in result.get('expected_output',[]) if isinstance(result.get('expected_output'),list) else []:
        if not isinstance(spec,dict):continue
        name=spec.get('path')
        if not isinstance(name,str) or Path(name).name!=name or '\\' in name:continue
        spec['final_archive_path']=str(root.parent/name)
        spec['resolved_path']=str(root/name)
        spec.pop('requested_path',None)
    return result


def normalize_node_paths(contract, output, node_dir):
    """Correct only a current-run archive claim with a verified node counterpart.

    Never import files, infer parameters, or borrow evidence from another node.
    An existing archive copy must match the node file before its claim can be
    corrected. The usual parameter/execution/artifact audits still run later.
    """
    parsed=parse_output(output)
    if not parsed:return output,[]
    data=copy.deepcopy(parsed)
    data.pop('path_corrections',None)  # This metadata is supplied by the program.
    root=Path(node_dir).resolve()
    expected=contract.get('expected_output') or []
    names={s['path'] for s in expected if isinstance(s,dict) and isinstance(s.get('path'),str)
           and Path(s['path']).name==s['path'] and '\\' not in s['path']} if isinstance(expected,list) else set()
    corrections=[]

    def corrected(raw,field):
        if not isinstance(raw,str) or not re.fullmatch(r'node_\d+',root.name):return raw
        if '\\' in raw and os.name!='nt':return raw  # Foreign absolute paths are not this run.
        path=Path(raw)
        if not path.is_absolute() or '..' in path.parts or path.parent!=root.parent or path.name not in names:
            return raw
        try:
            actual=_node_file(root,path.name)
            digest=_file_hash(actual)
            if path.is_symlink():return raw
            archive_exists=path.exists()
            if archive_exists and (not path.is_file() or path.stat().st_size==0 or _file_hash(path)!=digest):
                return raw
        except (OSError,ValueError,TypeError):return raw
        relative=actual.relative_to(root).as_posix()
        corrections.append(dict(field=field,declared_path=raw,node_path=relative,sha256=digest,
            archive_file_present=archive_exists,
            reason='声明使用了本任务最终归档路径；已核实当前节点同名文件'))
        return relative

    files=data.get('files')
    if isinstance(files,list):
        for i,item in enumerate(files):
            if isinstance(item,dict) and 'path' in item:item['path']=corrected(item['path'],f'files[{i}].path')
            elif isinstance(item,str):files[i]=corrected(item,f'files[{i}]')
    evidence=data.get('parameter_evidence')
    if isinstance(evidence,dict):
        for key,item in evidence.items():
            if isinstance(item,dict) and 'file' in item:item['file']=corrected(item['file'],f'parameter_evidence.{key}.file')
    if corrections:data['path_corrections']=corrections
    if not corrections and 'path_corrections' not in parsed:return output,[]
    return (json.dumps(data,ensure_ascii=False) if isinstance(output,str) else data),corrections


def audit_node(contract, output, node_dir, subtask, tool_calls=None):
    output = parse_output(output)
    params = contract.get('hard_parameters') or []
    issues, unchecked, checks = [], [], []
    if not output:unchecked.append('节点没有返回可核对的 JSON 结果')
    if output.get('delivery_status') == 'failed' or output.get('delivery_error'):
        unchecked.append('最终回答交付失败；执行回执和部分回答不能替代已核对成果')
    declared = output.get('primary_parameters')
    evidence = output.get('parameter_evidence') or {}
    coding = str(subtask.get('subtask_type', '')).lower() == 'coding'
    for p in params:
        actual = declared.get(p['name']) if isinstance(declared, dict) else None
        if actual is None:
            unchecked.append('缺少主问题参数声明：' + p['name'])
        elif not same_value(p, actual):
            issues.append(f"硬性参数漂移：{p['name']} 要求 {p['value']:g} {p['unit']}，节点声明 {actual}")
        elif coding:
            item = evidence.get(p['name']) if isinstance(evidence, dict) else None
            try:
                if not isinstance(item, dict):raise ValueError('缺少 Python 文件与符号证据')
                path=_node_file(node_dir,item.get('file',''))
                if path.suffix.lower() != '.py':raise ValueError('当前仅支持 Python 数值绑定证据')
                values=_bindings(path.read_text(encoding='utf-8'))
                symbol=item.get('symbol')
                if symbol not in values or values[symbol] is None:raise ValueError('符号不存在或表达式不能静态核对')
                measured={'value':values[symbol], 'unit':item.get('unit',p['unit'])}
                if not same_value(p, measured):
                    issues.append(f"代码参数漂移：{path.name}:{symbol}={measured}；要求 {p['value']:g} {p['unit']}")
                checks.append(dict(parameter=p['name'],file=path.name,symbol=symbol,actual=measured))
            except (OSError, ValueError, SyntaxError, UnicodeError, TypeError) as exc:
                unchecked.append(p['name']+' 尚未核对：'+str(exc))
    cases=output.get('primary_cases') or []
    if not isinstance(cases,list):unchecked.append('primary_cases 必须是列表');cases=[]
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('parameters'), dict):
            unchecked.append('主情景参数格式无效');continue
        for p in params:
            if not same_value(p,case['parameters'].get(p['name'])):
                issues.append('主情景 '+str(case.get('name',''))+' 改变了 '+p['name'])
    issues.extend(text_parameter_conflicts(str(output.get('core_results','')),params))
    files=output.get('files') or []
    if not isinstance(files,list):unchecked.append('files 字段必须是列表');files=[]
    artifact_checks=[]
    for item in files:
        raw=item.get('path') if isinstance(item,dict) else item
        try:
            path=_node_file(node_dir,raw)
            error=_valid_artifact(path)
            if error:raise ValueError('基础格式检查失败：'+error)
            artifact_checks.append(dict(path=path.relative_to(Path(node_dir).resolve()).as_posix(),sha256=_file_hash(path)))
        except (OSError,ValueError,TypeError) as exc:issues.append('声称的文件未核实：'+str(raw)+'；'+str(exc))
    if coding and contract.get('validation_policy',{}).get('python_enabled',False):
        executions=[t.get('execution') for t in (tool_calls or []) if t.get('tool')=='Python_code_interpreter']
        if not any(isinstance(e,dict) and e.get('exit_code')==0 for e in executions):
            unchecked.append('代码子任务没有成功 Python 执行回执')
        if any(isinstance(e,dict) and e.get('exit_code') not in (None,0) for e in executions):
            # Earlier failed attempts can be repaired by a later successful attempt.
            if not executions or not isinstance(executions[-1],dict) or executions[-1].get('exit_code')!=0:
                issues.append('最后一次 Python 执行失败，结果尚未复算')
    return dict(status='violation' if issues else 'unchecked' if unchecked else 'pass',
                issues=list(dict.fromkeys(issues)),unchecked=list(dict.fromkeys(unchecked)),checks=checks,artifact_checks=artifact_checks)


def enforce_audit(evaluation, audit):
    result=dict(evaluation or {}, integrity_audit=audit)
    if audit['status']!='pass':
        decision='to_redraft' if audit['status']=='violation' or result.get('decision')=='to_redraft' else 'to_revise'
        result.update(decision=decision,verdict='reject' if decision=='to_redraft' else 'refine',integrity_blocked=True,
                      integrity_adjusted=decision!=(evaluation or {}).get('decision'))
        if 'model_decision' in result:result['decision_adjusted']=decision!=result['model_decision']
        result['opinion']=str(result.get('opinion',''))+'\n[程序核对] '+'；'.join(audit['issues']+audit['unchecked'])
    return result


def _valid_artifact(path):
    try:
        if path.suffix.lower()=='.pdf':
            with path.open('rb') as stream:
                if not stream.read(1024).lstrip().startswith(b'%PDF-'):return '文件不是 PDF'
                stream.seek(max(0,path.stat().st_size-2048))
                if b'%%EOF' not in stream.read():return 'PDF 缺少结束标记'
            try:
                from pypdf import PdfReader
                from pypdf.errors import PdfReadError
            except ImportError:return 'PDF 解析核对需要 pypdf；请安装 requirements-web.txt 中的报告依赖'
            try:
                document=PdfReader(str(path),strict=True)
                if document.is_encrypted:return 'PDF 加密，不能核对报告内容'
                if not len(document.pages):return 'PDF 没有可读取页面'
            except (PdfReadError,KeyError,TypeError,IndexError) as exc:return 'PDF 无法解析：'+str(exc)
        elif path.suffix.lower()=='.py':ast.parse(path.read_text(encoding='utf-8'))
        elif path.suffix.lower()=='.csv':
            with path.open(encoding='utf-8-sig',newline='') as stream:
                reader=csv.reader(stream)
                if not next(reader,[]) or not next(reader,[]):return 'CSV 缺少表头或数据行'
        elif path.suffix.lower()=='.json':json.loads(path.read_text(encoding='utf-8-sig'))
        return None
    except (OSError,ValueError,SyntaxError,UnicodeError,csv.Error) as exc:return str(exc)


def _copy_verified(source, target, digest):
    """Publish bytes matching the audit receipt; retain old targets on failure."""
    from utils.live_updates import replace_with_retry
    if target.is_symlink():raise ValueError('目标为符号链接')
    if _file_hash(source)!=digest:raise ValueError('核对后源文件发生变化')
    temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        shutil.copyfile(source,temporary)
        if _file_hash(temporary)!=digest:raise ValueError('复制过程中文件发生变化')
        replace_with_retry(temporary,target)
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass


def complete_run(contract, trajectory, task_dir, stop_reason):
    root=Path(task_dir).resolve()
    subtasks=contract.get('subtasks') or contract.get('sub_tasks') or contract.get('sub-tasks') or []
    if not isinstance(subtasks,list):subtasks=[]
    if not subtasks:subtasks=[dict(id=1,description=contract.get('task_description',''))]
    order={int(s.get('id',i)) if isinstance(s,dict) else i:i for i,s in enumerate(subtasks,1)}
    latest={}
    for node in trajectory:
        if not isinstance(node,dict) or node.get('subtask_id') is None:continue
        sid=int(node['subtask_id'])
        if sid not in order:continue
        latest={k:n for k,n in latest.items() if order[k]<=order[sid]}
        latest[sid]=node
    states=[]
    for index,subtask in enumerate(subtasks,1):
        sid=int(subtask.get('id',index)) if isinstance(subtask,dict) else index
        node=latest.get(sid)
        feedback=(node or {}).get('critic_feedback') or {}
        accepted=(feedback.get('decision')=='complete' and feedback.get('verdict')=='accept'
                  and feedback.get('score_valid') is True and feedback.get('integrity_audit',{}).get('status')=='pass'
                  and not feedback.get('blocking_issues'))
        states.append(dict(id=sid,status='passed' if accepted else 'needs_revision' if node else 'not_started',
            node_id=(node or {}).get('node_id'),audit=feedback.get('integrity_audit')))
    accepted_ids={s['node_id'] for s in states if s['status']=='passed'}
    artifacts=[]
    for spec in expected_artifacts(contract,root):
        name=spec['path'];candidates=[]
        for node in trajectory:
            if node.get('node_id') not in accepted_ids:continue
            node_dir=root/f"node_{node['node_id']}"
            # Only this selected accepted branch; old_reference is never promoted.
            for raw in parse_output(node.get('result',{})).get('files',[]):
                try:
                    path=_node_file(node_dir,raw.get('path') if isinstance(raw,dict) else raw)
                    records=node.get('critic_feedback',{}).get('integrity_audit',{}).get('artifact_checks',[])
                    relative=path.relative_to(node_dir.resolve()).as_posix()
                    recorded=next((a for a in records if a['path']==relative),None)
                    if path.name==name and recorded and recorded['sha256']==_file_hash(path) and not any('old_reference' in part for part in path.parts):candidates.append((path,recorded["sha256"]))
                except (OSError,ValueError,TypeError):pass
        record=dict(path=name,status='missing',source=None)
        if candidates:
            source,digest=candidates[-1];error=_valid_artifact(source)
            if error:record.update(status='invalid',reason=error)
            else:
                target=root/name
                if target.is_symlink():record.update(status='invalid',reason='目标为符号链接')
                else:
                    try:
                        record.update(status='available',source=source.relative_to(root).as_posix(),
                                      bytes=source.stat().st_size,sha256=digest)
                    except (OSError,ValueError) as exc:record.update(status='invalid',reason='归档失败：'+str(exc))
        artifacts.append(record)
    reasons=[]
    # Supplementary, declared evidence from this accepted branch is useful too.
    # It is not a substitute for any missing mandatory deliverable.
    additional=[]
    known={a['path'] for a in artifacts}
    for node in trajectory:
        if node.get('node_id') not in accepted_ids:continue
        node_dir=root/f"node_{node['node_id']}"
        checks=node.get('critic_feedback',{}).get('integrity_audit',{}).get('artifact_checks',[])
        for checked in checks:
            try:
                source=_node_file(node_dir,checked['path']);name=source.name
                if name in known or name in RESERVED or name.startswith('.') or source.suffix.lower() not in EXTENSIONS:continue
                if _file_hash(source)!=checked.get('sha256') or _valid_artifact(source):continue
                target=root/name
                if target.is_symlink():continue
                additional.append(dict(path=name,status='available',source=source.relative_to(root).as_posix(),
                    bytes=source.stat().st_size,sha256=checked['sha256'],required=False))
                known.add(name)
            except (OSError,ValueError,KeyError):continue
    if any(s['status']!='passed' for s in states):reasons.append('部分子任务未执行或未通过评审／程序核对')
    if any(a['status']!='available' for a in artifacts):reasons.append('合同要求的交付文件缺失或格式检查未通过')
    report=dict(status='partial' if reasons else 'passed',stop_reason=stop_reason,
        reasons=reasons,subtasks=states,artifacts=artifacts,additional_artifacts=additional,hard_parameters=contract.get('hard_parameters',[]),
        contract_revision=contract.get('contract_revision',0),
        current_task_dir=str(root),validation_scope='参数声明／指定 Python 绑定／执行回执／文件存在及基础格式；不证明科学正确性')
    from utils.artifact_manager import ArtifactManager
    manager = ArtifactManager(root)
    for node in trajectory:
        if node.get('node_id') in accepted_ids:
            manager.record_node(node['node_id'], node.get('result'), node.get('critic_feedback'))
    report=manager.publish(report)
    atomic_json(root/'completion.json',report)
    return report


def finalize_summary(path, report):
    text=Path(path).read_text(encoding='utf-8')
    conflicts=text_parameter_conflicts(text,report['hard_parameters'])
    if conflicts:
        report['status']='partial';report['reasons'].extend(conflicts)
    lines=['# Summary', '', '## 程序核对记录', '', '**'+('研究通过（模型评审与基础程序核对）' if report['status']=='passed' else '部分完成，结果尚未全部通过核对')+'**',
           '', '停止原因：`'+report['stop_reason']+'`。', '']
    for reason in report['reasons']:lines.append('- '+reason)
    for p in report['hard_parameters']:lines.append(f"- 用户锁定参数：`{p['name']} = {p['value']:g} {p['unit']}`。")
    for artifact in report['artifacts']:
        lines.append('- 交付文件：`'+artifact['path']+'` — '+artifact['status']+
                     ('；来源 `'+artifact['source']+'`' if artifact['source'] else ''))
    lines.extend(['','上述检查不等同于实验验证或物理正确性证明。','','---','',text.removeprefix('# Summary').lstrip()])
    Path(path).write_text('\n'.join(lines),encoding='utf-8')
    atomic_json(Path(path).parent/'completion.json',report)
    # Keep publication status aligned if the summary introduced a parameter conflict.
    for manifest_path in (Path(path).parent/'final_manifest.json',Path(path).parent/'final'/'manifest.json'):
        if manifest_path.is_file():
            try:
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                manifest['status']=report['status']
                atomic_json(manifest_path,manifest)
            except (OSError,ValueError):pass
    return report
