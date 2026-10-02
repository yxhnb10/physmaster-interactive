"""User-owned numeric constraints, independent of LLM contract extraction."""
import math
import re

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
ALIASES = {'total_mass': ['total_mass', 'total_mass_kg', 'mass', 'mass_kg', 'm', 'm_total', 'MASS', 'TOTAL_MASS']}
MASS_NAMES = {'total_mass', 'total_mass_kg', 'mass', 'mass_kg', '总质量'}
UNITS = {'kg': ('mass', 1), 'g': ('mass', .001), '千克': ('mass', 1),
         '公斤': ('mass', 1), '克': ('mass', .001), 'm': ('length', 1),
         'km': ('length', 1000), 'cm': ('length', .01), 's': ('time', 1),
         'min': ('time', 60), 'h': ('time', 3600), 'K': ('temperature', 1)}


def normalize_parameters(items):
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError('硬性参数必须是列表，最多 50 项')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('硬性参数格式错误')
        name = item.get('name')
        value = item.get('value')
        unit = item.get('unit', '')
        if isinstance(unit,str) and unit.lower() in ('kg','g'):unit=unit.lower()
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+', name):
            raise ValueError('参数名称只能使用字母、数字、下划线或中文')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('参数值必须是有限数值')
        if not isinstance(unit, str) or len(unit) > 30 or not re.fullmatch(r'[\w/°²³^.*-]*', unit):
            raise ValueError('参数单位格式错误')
        name = 'total_mass' if name in MASS_NAMES else name
        record = dict(name=name, value=float(value), unit=unit,
                      aliases=ALIASES.get(name, [name]), source=str(item.get('source', 'user'))[:500])
        if name == 'total_mass' and value <= 0:
            raise ValueError('总质量必须大于零')
        if name == 'total_mass' and unit not in ('kg', 'g', '千克', '公斤', '克'):
            raise ValueError('总质量需注明 kg 或 g 等质量单位')
        if any(p['name'] == name for p in result):
            raise ValueError('硬性参数名称重复：' + name)
        result.append(record)
    return result


def parse_parameter_text(text):
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError('硬性参数文本必须是字符串，最多 10000 字符')
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+)\s*[=：:]\s*(' + NUMBER + r')\s*([\w/°²³^.*-]*)', line)
        if not match:
            raise ValueError('硬性参数每行应为 名称 = 数值 单位，例如 total_mass = 190 kg')
        items.append(dict(name=match[1], value=float(match[2]), unit=match[3], source='user parameter field'))
    return normalize_parameters(items)


def same_value(expected, actual):
    try:
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            actual = {'value': actual, 'unit': expected['unit']}
        if not isinstance(actual, dict) or isinstance(actual.get('value'), bool):
            return False
        value = float(actual['value'])
        unit = actual.get('unit', expected['unit'])
        if isinstance(unit,str) and unit.lower() in ('kg','g'):unit=unit.lower()
        if unit == expected['unit']:
            factor = 1.0
        elif unit in UNITS and expected['unit'] in UNITS and UNITS[unit][0] == UNITS[expected['unit']][0]:
            factor = UNITS[unit][1] / UNITS[expected['unit']][1]
        else:
            return False
        return math.isfinite(value) and math.isclose(value * factor, expected['value'], rel_tol=1e-9, abs_tol=1e-8)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def discover_mass(text, existing=False):
    # Narrow recognition, never let generated summaries or model prose set locks.
    patterns = [r'(?:total\s+mass|system\s+mass|总质量)[^\n.;。；]{0,140}?(' + NUMBER + r')\s*(kg|千克|公斤)',
                r'(' + NUMBER + r')\s*(kg)\s+(?:skydiver|跳伞者)[^\n]{0,100}?(?:system|系统)']
    if existing:
        patterns.append(r'(?:质量|mass)\s*(?:改为|改成|调整为|=|:|to|is)\s*(' + NUMBER + r')\s*(kg|千克|公斤)')
    hits = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            hits.append((match.start(), float(match[1]), match[2]))
    values = {value for _, value, _ in hits}
    if len(values) > 1:
        raise ValueError('同一条用户输入含多个总质量值；请明确主问题的总质量，并把额外情景分开表述')
    if not hits:
        return None
    _, value, unit = sorted(hits)[-1]
    return normalize_parameters([dict(name='total_mass', value=value, unit=unit,
        source='explicit user total mass')])[0]


def resolve_parameters(user_inputs, manual_text=None, configured=None):
    manual=parse_parameter_text(manual_text) if manual_text is not None else []
    manual_mass=next((p for p in manual if p['name']=='total_mass'),None)
    params = {p['name']: p for p in normalize_parameters(configured or [])}
    discovered = None
    for text in user_inputs:
        try:discovered = discover_mass(text, 'total_mass' in params) or discovered
        except ValueError:
            if manual_mass is None:raise
            discovered=manual_mass
        if discovered:
            params['total_mass'] = discovered
    if manual_text is not None:
        for p in manual:
            if p['name'] == 'total_mass' and discovered and not same_value(discovered, p):
                raise ValueError('硬性参数栏的总质量与用户问题／最新追加条件冲突，请统一后提交')
            params[p['name']] = p
    return list(params.values())


def parameter_text(params):
    return '\n'.join(f"{p['name']} = {p['value']:g} {p['unit']}".rstrip() for p in params)


def normalize_subtasks(contract):
    """Use one subtask list for the dispatcher and final completion audit."""
    payload=contract.get('sub-tasks') or contract.get('sub_tasks') or contract.get('subtasks') or []
    if isinstance(payload,dict):payload=list(payload.values())
    elif isinstance(payload,str):payload=[payload]
    elif not isinstance(payload,list):payload=[]
    if not payload:payload=[dict(description=contract['task_description'])]
    result=[];used=set();next_id=1
    for item in payload:
        record=dict(item) if isinstance(item,dict) else dict(description=str(item))
        try:sid=int(record.get('id'))
        except (TypeError,ValueError,OverflowError):sid=None
        if sid is None or sid in used:
            while next_id in used:next_id+=1
            sid=next_id
        used.add(sid);next_id=max(next_id,sid+1)
        record.update(id=sid,description=str(record.get('description') or record.get('objective') or record.get('task') or record.get('name') or ''),
                      subtask_type=str(record.get('subtask_type') or 'reasoning').strip().lower())
        if not record['description'].strip():record['description']=f'Subtask {sid}'
        result.append(record)
    result.sort(key=lambda item:item['id'])
    contract.pop('sub-tasks',None);contract.pop('sub_tasks',None)
    contract['subtasks']=result
    return result


def bind_contract(contract, params, user_inputs, task_dir):
    if not isinstance(contract, dict) or not str(contract.get('task_description', '')).strip():
        raise ValueError('Clarifier 没有返回有效任务描述')
    contract['hard_parameters'] = normalize_parameters(params)
    contract['authoritative_user_inputs'] = list(user_inputs)
    contract['current_task_dir'] = str(task_dir.resolve())
    # Expected filenames are scoped to this run, never an inherited absolute path.
    from utils.research_integrity import expected_artifacts
    contract['expected_output'] = expected_artifacts(contract, task_dir)
    specs=contract['expected_output']
    subtasks=normalize_subtasks(contract)
    if any(s['path'].lower().endswith('.pdf') for s in specs) and isinstance(subtasks,list) and subtasks:
        if not isinstance(subtasks[-1],dict) or subtasks[-1].get('subtask_type')!='analysis':
            ids=[int(s.get('id',i)) for i,s in enumerate(subtasks,1) if isinstance(s,dict)]
            subtasks.append(dict(id=max(ids,default=0)+1,subtask_type='analysis',
                description='Final analysis and actual report delivery: review assumptions, constraint compliance, uncertainty and numerical validation; generate the required PDF report in this node directory. Do not merely promise files. Prior unsupported conclusions remain provisional.',
                input='Accepted artifacts from the current branch.',expected_output=', '.join(s['path'] for s in specs)))
    return contract
