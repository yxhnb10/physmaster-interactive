"""v9.6 task-local usage, cooperative budgets and observable progress. No acceptance changes."""
import contextvars
from contextlib import contextmanager
import functools
import hashlib
import inspect
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse,parse_qs

VERSION='v9.6-cloud-1'
SETTINGS='run_controls.json'
DB='run_usage.sqlite'
CONTEXT=contextvars.ContextVar('physmaster_call_context',default={})
DEFAULTS=dict(request_limit=None,token_limit=None,cost_limit=None,currency='CNY',prices={},parallel_workers=None,draft_branches=None,fast_draft=False,sdk_retries=None,stall_threshold=3,pause_on_stall=False)


def read_json(path,fallback=None):
    try:return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError):return fallback


def validate_settings(data,base=None):
    if data is None:data={}
    if not isinstance(data,dict) or set(data)-set(DEFAULTS):raise ValueError('未知或无效的运行设置')
    out={**DEFAULTS,**(base or {}),**data}
    for key,high in [('request_limit',100000),('token_limit',10000000000),('parallel_workers',4),('draft_branches',3),('sdk_retries',3)]:
        v=out[key];low=0 if key=='sdk_retries' else 1
        if v is not None and (isinstance(v,bool) or not isinstance(v,int) or not low<=v<=high):raise ValueError(key+' 超出允许范围')
    for key in ('fast_draft','pause_on_stall'):
        if not isinstance(out[key],bool):raise ValueError(key+' 必须是布尔值')
    if isinstance(out['stall_threshold'],bool) or not isinstance(out['stall_threshold'],int) or not 2<=out['stall_threshold']<=8:raise ValueError('停滞阈值须为 2～8')
    if out['currency'] not in ('CNY','USD'):raise ValueError('币种必须为 CNY 或 USD')
    v=out['cost_limit']
    if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<v<=1000000):raise ValueError('费用上限无效')
    prices=out['prices']
    if not isinstance(prices,dict) or len(prices)>50:raise ValueError('模型费率须为对象，最多 50 个模型')
    for model,row in prices.items():
        if not isinstance(model,str) or not model.strip() or len(model)>120 or not isinstance(row,dict) or set(row)-{'input','cached_input','output'} or not {'input','output'}<=set(row):raise ValueError('费率须包含每百万 token 的 input、output，可选 cached_input')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1000000 for v in row.values()):raise ValueError('费率须为有限非负数')
    return out


def configure_task(config,data=None):
    controls=validate_settings(data,config.get('v96') or {})
    config['v96']=controls
    if controls['parallel_workers'] is not None:config.setdefault('pipeline',{})['parallel_processes']=controls['parallel_workers']
    if controls['draft_branches'] is not None:config.setdefault('mcts',{})['draft_expansion']=controls['draft_branches']
    return controls


def settings_for(root,fallback=None):return validate_settings(read_json(Path(root)/SETTINGS) or {},fallback or {})


def save_settings(root,data,base=None):
    from utils.live_updates import atomic_json
    controls=validate_settings(data,base);atomic_json(Path(root)/SETTINGS,controls)
    try:(Path(root)/SETTINGS).chmod(0o600)
    except OSError:pass
    return controls


@contextmanager
def connect(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);path=root/DB
    c=sqlite3.connect(path,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,started REAL,finished REAL,role TEXT,node TEXT,model TEXT,status TEXT,elapsed REAL,input_tokens INTEGER,output_tokens INTEGER,cached_tokens INTEGER,reasoning_tokens INTEGER,error_type TEXT);
CREATE TABLE IF NOT EXISTS node_progress(node INTEGER PRIMARY KEY,subtask TEXT,revision INTEGER,at REAL,signature TEXT,accepted INTEGER,streak INTEGER,issues TEXT);
CREATE TABLE IF NOT EXISTS notices(kind TEXT PRIMARY KEY,at REAL,message TEXT,active INTEGER);''')
    try:path.chmod(0o600)
    except OSError:pass
    try:
        with c:yield c
    finally:c.close()


def integer(value):return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def mapping(value):
    if isinstance(value,dict):return value
    if hasattr(value,'model_dump'):return value.model_dump()
    return vars(value) if hasattr(value,'__dict__') else {}


def usage_values(usage):
    usage=mapping(usage);inp=integer(usage.get('prompt_tokens'));out=integer(usage.get('completion_tokens'))
    details=mapping(usage.get('prompt_tokens_details'));cached=integer(usage.get('prompt_cache_hit_tokens',details.get('cached_tokens')))
    reasoning=integer(mapping(usage.get('completion_tokens_details')).get('reasoning_tokens'))
    if cached is not None and inp is not None:cached=min(cached,inp)
    return inp,out,cached,reasoning


def estimated_cost(row,prices):
    price=prices.get(row['model']);inp=row['input_tokens'];out=row['output_tokens'];cached=row['cached_tokens']
    if not price or inp is None or out is None:return None
    cache_rate=price.get('cached_input',price['input'])
    if cached is None and cache_rate!=price['input']:return None
    cached=cached or 0
    return ((inp-cached)*price['input']+cached*cache_rate+out*price['output'])/1000000


def totals(connection,controls):
    # Aggregate in SQLite rather than loading the full ledger before every model call.
    rows=connection.execute('SELECT model,COUNT(*) n,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,SUM(cached_tokens) cached_tokens,input_tokens IS NULL bad_input,output_tokens IS NULL bad_output FROM calls GROUP BY model,input_tokens IS NULL,output_tokens IS NULL,cached_tokens IS NULL').fetchall()
    costs=[(estimated_cost(r,controls['prices']),r['n']) for r in rows]
    return dict(requests=sum(r['n'] for r in rows),known_tokens=sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in rows),usage_unknown=sum(r['n'] for r in rows if r['bad_input'] or r['bad_output']),estimated_cost=sum(v for v,n in costs if v is not None),cost_unknown=sum(n for v,n in costs if v is None))


def exceeded(total,controls):
    reasons=[]
    for key,value in [('request_limit',total['requests']),('token_limit',total['known_tokens']),('cost_limit',total['estimated_cost'])]:
        if controls[key] is not None and value>=controls[key]:reasons.append(key)
    return reasons


def pause(root,kind,message):
    from utils.live_updates import atomic_json
    with connect(root) as c:
        c.execute('BEGIN IMMEDIATE')
        if kind=='budget' and not exceeded(totals(c,settings_for(root)),settings_for(root)):return
        previous=c.execute('SELECT active,message FROM notices WHERE kind=?',(kind,)).fetchone()
        control=read_json(Path(root)/'live_updates/control.json',{}) or {}
        if previous and previous['active'] and previous['message']==message and control.get('paused'):return
        c.execute('INSERT OR REPLACE INTO notices VALUES(?,?,?,1)',(kind,time.time(),message))
        control['paused']=True;control['v96_pause_notice']=message
        if kind=='budget':control['v96_budget_waiting']=True
        atomic_json(Path(root)/'live_updates/control.json',control)


def reserve(root,role,model,fallback):
    """Bound request count atomically; tokens/cost stop the next request after receipts."""
    while True:
        if (read_json(Path(root)/'live_updates/control.json',{}) or {}).get('stop_requested'):
            from utils.recovery import ResearchInterrupted
            raise ResearchInterrupted('收到中断请求，未开始下一次模型请求')
        controls=settings_for(root,fallback)
        with connect(root) as c:
            c.execute('BEGIN IMMEDIATE');total=totals(c,controls);reasons=exceeded(total,controls)
            control=read_json(Path(root)/'live_updates/control.json',{}) or {}
            budget_waiting=control.get('paused') and control.get('v96_budget_waiting')
            if not reasons and not budget_waiting:
                cursor=c.execute('INSERT INTO calls(started,role,node,model,status) VALUES(?,?,?,?,?)',(time.time(),role,str(CONTEXT.get().get('node','')),model,'running'))
                c.execute("UPDATE notices SET active=0 WHERE kind='budget'");return cursor.lastrowid,controls
        if reasons:pause(root,'budget','预算达到上限：'+', '.join(reasons)+'。已保存用量；调整预算并解除暂停后才能开始下一次模型请求。')
        if (read_json(Path(root)/'live_updates/control.json',{}) or {}).get('stop_requested'):
            from utils.recovery import ResearchInterrupted
            raise ResearchInterrupted('预算等待期间收到中断请求')
        time.sleep(.2)


def monitored(role=None):
    def decorate(fn):
        signature=inspect.signature(fn)
        @functools.wraps(fn)
        def run(*args,**kwargs):
            bound=signature.bind(*args,**kwargs);metadata=bound.arguments.get('node_metadata') or {};previous=CONTEXT.get();ctx=dict(previous)
            if 'role' in bound.arguments:ctx['role']=bound.arguments['role'] or 'default'
            elif role:ctx['role']=role
            if metadata:ctx.update(node=metadata.get('node_id'),root=metadata.get('task_dir'))
            node=bound.arguments.get('node') or bound.arguments.get('child_node')
            owner=bound.arguments.get('self')
            if node is not None:ctx.update(node=getattr(node,'node_id',None),root=getattr(owner,'task_dir',None))
            token=CONTEXT.set(ctx)
            try:return fn(*args,**kwargs)
            finally:CONTEXT.reset(token)
        return run
    return decorate


class CompletionObserver:
    def __init__(self,original,config_path):
        self.original=original
        import yaml
        cfg=yaml.safe_load(Path(config_path).read_text(encoding='utf8')) or {};self.controls=validate_settings(cfg.get('v96') or {});self.base_url=(cfg.get('llm') or {}).get('base_url','')
    def __getattr__(self,key):return getattr(self.original,key)
    def create(self,**kwargs):
        root=CONTEXT.get().get('root') or os.environ.get('PHY_TIMING_ROOT');role=CONTEXT.get().get('role','default')
        if not root:return self.original.create(**kwargs)
        ident,controls=reserve(root,role,kwargs.get('model','unknown'),self.controls)
        if controls['fast_draft'] and role=='draft' and urlparse(self.base_url).hostname=='api.deepseek.com' and str(kwargs.get('model','')).startswith('deepseek'):
            kwargs['extra_body']=dict(kwargs.get('extra_body') or {},thinking={'type':'disabled'})
        start=time.monotonic()
        try:
            result=self.original.create(**kwargs)
        except Exception as exc:
            try:
                with connect(root) as c:c.execute('UPDATE calls SET finished=?,status=?,elapsed=?,error_type=? WHERE id=?',(time.time(),'failed',time.monotonic()-start,type(exc).__name__,ident))
            except (OSError,sqlite3.Error):print('[Usage] Failure receipt could not be saved; cost remains unknown.',flush=True)
            raise
        try:
            values=usage_values(getattr(result,'usage',None))
            with connect(root) as c:c.execute('UPDATE calls SET finished=?,status=?,elapsed=?,input_tokens=?,output_tokens=?,cached_tokens=?,reasoning_tokens=? WHERE id=?',(time.time(),'done',time.monotonic()-start,*values,ident))
        except (OSError,sqlite3.Error,TypeError,ValueError):
            print('[Usage] Receipt unavailable; valid model reply retained, cost remains unknown.',flush=True)
        return result


def attach_client(client,config_path):
    client.chat.completions=CompletionObserver(client.chat.completions,config_path)
    return client


def sdk_retries(config_path,default=3):
    import yaml
    cfg=yaml.safe_load(Path(config_path).read_text(encoding='utf8')) or {};value=(cfg.get('v96') or {}).get('sdk_retries')
    return default if value is None else validate_settings({'sdk_retries':value})['sdk_retries']


def progress(owner,node):
    from utils.research_integrity import parse_output
    ev=node.evaluation or {};result=parse_output(node.result);accepted=ev.get('decision')=='complete'
    issues=list(dict.fromkeys(str(x).strip() for x in (ev.get('blocking_issues') or [])+(ev.get('decision_reasons') or []) if str(x).strip()))
    manifest=getattr(node,'artifact_manifest',{}) or {};files=sorted((str(r.get('node_path') or re.sub(r'^node_\d+/', '', str(r.get('path') or ''))),str(r.get('sha256'))) for r in manifest.get('artifacts',[]) if r.get('sha256'))
    metrics={k:result.get(k) for k in ('core_results','numerical_results','primary_parameters')};signature=hashlib.sha256(json.dumps([sorted(re.sub(r'\s+',' ',x).casefold() for x in issues),files,metrics],sort_keys=True,default=str).encode()).hexdigest()
    settings=settings_for(owner.task_dir);revision=int(getattr(owner,'live_revision',0));subtask=str(node.subtask_id)
    with connect(owner.task_dir) as c:
        c.execute('BEGIN IMMEDIATE');existing=c.execute('SELECT * FROM node_progress WHERE node=?',(node.node_id,)).fetchone()
        if existing and existing['signature']==signature and bool(existing['accepted'])==accepted:return
        last=c.execute('SELECT * FROM node_progress WHERE subtask=? AND revision=? AND node!=? ORDER BY at DESC LIMIT 1',(subtask,revision,node.node_id)).fetchone()
        streak=(last['streak']+1 if last and not last['accepted'] and last['signature']==signature else 1) if not accepted else 0
        c.execute('INSERT OR REPLACE INTO node_progress VALUES(?,?,?,?,?,?,?,?)',(node.node_id,subtask,revision,time.time(),signature,int(accepted),streak,json.dumps(issues,ensure_ascii=False)))
        active=not accepted and streak>=settings['stall_threshold']
        message='子任务 '+subtask+' 连续 '+str(streak)+' 次出现相同阻断，且未观察到成果哈希或核心结果变化。建议检查共同原因或补充条件。'
        c.execute('INSERT OR REPLACE INTO notices VALUES(?,?,?,?)',('stall:'+subtask,time.time(),message,int(active)))
    if active and settings['pause_on_stall']:pause(owner.task_dir,'stall:'+subtask,message)


def capacity():
    out={'cpu_count':os.cpu_count()}
    try:
        rows={line.split(':')[0]:int(line.split(':')[1].split()[0]) for line in Path('/proc/meminfo').read_text().splitlines()};out.update(memory_total_mb=rows['MemTotal']//1024,memory_available_mb=rows['MemAvailable']//1024)
    except (OSError,ValueError,KeyError):pass
    return out


def usage_overview(root):
    root=Path(root);controls=settings_for(root);out=dict(version=VERSION,settings=controls,capacity=capacity(),groups=[],recent=[],notices=[],total=dict(requests=0,known_tokens=0,usage_unknown=0,estimated_cost=0,cost_unknown=0))
    if not (root/DB).is_file():return out
    connection=sqlite3.connect((root/DB).resolve().as_uri()+'?mode=ro',uri=True,timeout=2);connection.row_factory=sqlite3.Row
    with connection as c:
        out['total']=totals(c,controls);rows=[dict(r) for r in c.execute('SELECT * FROM calls ORDER BY id DESC')]
        groups={}
        for row in rows:
            key=(row['role'],row['model'],row['node']);g=groups.setdefault(key,dict(role=key[0],model=key[1],node=key[2],calls=0,input_tokens=0,output_tokens=0,cached_tokens=0,cache_unknown=0,unknown=0,failed=0,elapsed_seconds=0,estimated_cost=0,cost_unknown=0))
            g['calls']+=1;g['failed']+=row['status']=='failed';g['elapsed_seconds']+=row['elapsed'] or 0
            for name in ('input_tokens','output_tokens','cached_tokens'):g[name]+=row[name] or 0
            g['cache_unknown']+=row['cached_tokens'] is None
            g['unknown']+=row['input_tokens'] is None or row['output_tokens'] is None;cost=estimated_cost(row,controls['prices']);g['estimated_cost']+=cost or 0;g['cost_unknown']+=cost is None
        out['groups']=list(groups.values())[:200];out['recent']=rows[:30]
        out['notices']=[dict(r) for r in c.execute('SELECT * FROM notices WHERE active=1 ORDER BY at DESC')]
        out['stalled_nodes']=[dict(r) for r in c.execute('SELECT node,subtask,streak,accepted FROM node_progress WHERE streak>=? ORDER BY at DESC LIMIT 30',(controls['stall_threshold'],))]
    connection.close()
    try:
        from utils.research_web import overview
        web=overview(root,limit=1);out['web_budget']=web.get('budget',[])
    except (OSError,sqlite3.Error,ImportError):out['web_budget']=[]
    return out


def continuation_preview(job,text,manual=None):
    from utils.hard_parameters import resolve_parameters,parameter_text,same_value
    if not isinstance(text,str) or not text.strip() or len(text)>100000:raise ValueError('补充条件须为 1～100000 字符')
    initial=job.get('hard_parameters') or []
    history=[job.get('original_query') or job.get('query') or '']+[m['text'] for m in job.get('messages',[])]
    old=resolve_parameters(history,None,initial)
    if manual in (parameter_text(old),parameter_text(initial)):manual=None
    inputs=history+[text]
    new=resolve_parameters(inputs,manual,old);before={p['name']:p for p in old};after={p['name']:p for p in new};changes=[]
    for name,p in after.items():
        if name not in before or not same_value(before[name],p):changes.append(dict(name=name,previous=before.get(name),next=p))
    manifest=read_json(Path(job['task_dir'])/'final_manifest.json',{}) or {};files=manifest.get('artifacts') or []
    return dict(parent_id=job['id'],changes=changes,unchanged_parameters=[p for p in new if p['name'] not in {c['name'] for c in changes}],appended_condition=text,
        inherited_resources=[dict(path=r.get('download_path') or r.get('path') or r.get('published_path') or r.get('source'),status='作为继承资料或修改基线；不自动继承新条件下的验收') for r in files[:40] if isinstance(r,dict)],
        impact_status='needs_review',impact_notice='程序只比较明确锁定的参数。自然语言条件对模型、数据和结论的影响仍需逐项核对；上述资料可继承，但不能据此判定新条件下仍然有效。',preview_only=True)


def make_run_handler(Base,manager,store=None):
    class Handler(Base):
        def _run_user(self,path,write=False):
            if not self.trusted_host():self.reply({'error':'Invalid host'},403);return None
            if not store:self.reply({'error':'本功能需要小组账号模式'},409);return None
            user=self.identify()
            if not user:self.reply({'error':'请先登录','login_required':True},401);return None
            if write and (not self.trusted_origin() or self.headers.get('X-PhysMaster-Token')!=self.request_token()):self.reply({'error':'请求校验失败，请刷新页面'},403);return None
            if not self.authorize(path,user,write):self.reply({'error':'无权操作此任务'},404);return None
            return user
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/v96/health':
                if self.client_address[0] not in ('127.0.0.1','::1') or not self.trusted_host():return self.reply({'error':'Not found'},404)
                return self.reply({'ok':True,'team':bool(store),'version':VERSION})
            match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/v96',path)
            if not match:return super().do_GET()
            user=self._run_user(path)
            if not user:return
            with manager.lock:
                job=manager.jobs.get(match[1]);root=job['task_dir'] if job else None;can_edit=self.authorize(path,user,True)
            if root is None:return self.reply({'error':'任务不存在'},404)
            try:
                out=usage_overview(root);out['can_edit']=can_edit
                return self.reply(out)
            except (OSError,sqlite3.Error,ValueError):return self.reply({'error':'用量记录暂时不可读；已保存数据保留'},503)
        def do_POST(self):
            path=urlparse(self.path).path;match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(v96-settings|continuation-preview)',path)
            if not match:return super().do_POST()
            user=self._run_user(path,write=True)
            if not user:return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=180000:raise ValueError('请求长度无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('请求须为对象')
                with manager.lock:job=dict(manager.jobs[match[1]])
                if match[2]=='continuation-preview':return self.reply(continuation_preview(job,body.get('text'),body.get('hard_parameter_text')))
                controls=save_settings(job['task_dir'],body.get('settings'),settings_for(job['task_dir']))
                resumed=False
                if body.get('resume'):
                    with connect(job['task_dir']) as c:
                        if exceeded(totals(c,controls),controls):raise ValueError('新的预算仍已达到上限；设置已保存，请提高上限后继续')
                        c.execute("UPDATE notices SET active=0 WHERE kind='budget'")
                    # Preserve pending checkpoint-stop requests rather than silently clearing them.
                    from utils.live_updates import atomic_json
                    control=read_json(Path(job['task_dir'])/'live_updates/control.json',{}) or {}
                    if control.get('stop_requested'):raise ValueError('此任务已请求中断，请等待断点保存后恢复')
                    control['paused']=False;control.pop('v96_budget_waiting',None);control.pop('v96_pause_notice',None);atomic_json(Path(job['task_dir'])/'live_updates/control.json',control);resumed=True
                return self.reply({'ok':True,'settings':controls,'resumed':resumed,'notice':'并行数与候选数用于下次新建/续研；预算、费率与停滞设置供本任务后续检查读取。'})
            except KeyError:return self.reply({'error':'任务不存在'},404)
            except (ValueError,TypeError):return self.reply({'error':str(__import__('sys').exc_info()[1])},400)
            except (OSError,sqlite3.Error):return self.reply({'error':'运行设置暂时无法保存；研究文件保留'},503)
    return Handler
