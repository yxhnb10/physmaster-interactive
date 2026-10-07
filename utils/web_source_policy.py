"""Group-wide excluded domains. Reads are shared by server and research workers."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

VERSION='v9.6.2-appearance-sources-1'
POLICY_FILE='web_source_policy.json'


def normalize_domains(value):
    if isinstance(value,str):value=re.split(r'[\n,;，；]+',value)
    if not isinstance(value,list) or len(value)>150:raise ValueError('最多填写 150 个网站，每行一个')
    result=[]
    for entry in value:
        if not isinstance(entry,str) or len(entry)>2000:raise ValueError('网站格式无效')
        text=entry.strip()
        if not text:continue
        if text.startswith('*.'):text=text[2:]
        parsed=urlparse(text if '://' in text else 'https://'+text)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('请填写网站域名或公开网站地址')
        try:host=parsed.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError:raise ValueError('网站域名无效') from None
        labels=host.split('.')
        if len(host)>253 or len(labels)<2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p) for p in labels):raise ValueError('请填写完整域名，例如 example.com')
        if host not in result:result.append(host)
    return sorted(result)


def policy_path(project=None):
    return (Path(project) if project else Path(__file__).resolve().parents[1])/'team_data'/POLICY_FILE


def load_policy(project=None):
    path=policy_path(project)
    if path.is_symlink():raise ValueError('来源设置文件不能为符号链接')
    if not path.exists():return dict(mode='denylist',deny_domains=[],revision='default',updated_at=None,updated_by=None)
    if path.stat().st_size>65536:raise ValueError('来源设置文件过大')
    data=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data,dict) or data.get('mode')!='denylist' or not isinstance(data.get('revision'),str):raise ValueError('来源设置格式异常')
    return dict(mode='denylist',deny_domains=normalize_domains(data.get('deny_domains')),revision=data['revision'],updated_at=data.get('updated_at'),updated_by=data.get('updated_by'))


def policy_fingerprint(project=None):
    try:data=load_policy(project);return hashlib.sha256(json.dumps(['denylist',data['deny_domains']],sort_keys=True).encode()).hexdigest()
    except (OSError,ValueError,TypeError):return 'policy-unavailable'


def domain_allowed(url,policy):
    try:host=(urlparse(url).hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
    except (ValueError,UnicodeError):return False
    return bool(host) and not any(host==d or host.endswith('.'+d) for d in policy['deny_domains'])


def save_policy(project,domains,actor,expected_revision):
    current=load_policy(project)
    if expected_revision!=current['revision']:raise RuntimeError('来源设置已被其他管理员修改，请重新读取后保存')
    normalized=normalize_domains(domains)
    data=dict(mode='denylist',deny_domains=normalized,revision=uuid.uuid4().hex,updated_at=time.time(),updated_by=actor)
    from utils.live_updates import atomic_json
    path=policy_path(project);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    atomic_json(path,data);path.chmod(0o600)
    return data


def budget_limits(root,defaults):
    path=Path(root)/'web_budget_settings.json'
    if path.is_symlink():raise ValueError('联网预算文件不能为符号链接')
    data=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
    if not isinstance(data,dict) or set(data)-{'search','read'}:raise ValueError('联网预算设置无效')
    result=dict(defaults)
    for key,value in data.items():
        if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=1000:raise ValueError('联网次数上限须为 0～1000 的整数')
        result[key]=value
    return result


def budget_overview(root):
    root=Path(root);defaults={'search':12,'read':6}
    config=root.parent/'web_config.yaml'
    if config.is_file():
        import yaml
        tools=(yaml.safe_load(config.read_text(encoding='utf8')) or {}).get('tools') or {}
        defaults={'search':max(0,min(100,int(tools.get('max_web_calls',12)))),'read':max(0,min(100,int(tools.get('max_source_reads',6))))}
    limits=budget_limits(root,defaults)
    try:revision=int(json.loads((root/'contract.json').read_text()).get('contract_revision',0))
    except (OSError,ValueError,TypeError):revision=0
    rows=[];path=root/'research_web.sqlite'
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as connection:
            connection.row_factory=sqlite3.Row
            rows=[dict(row) for row in connection.execute('SELECT * FROM web_budget ORDER BY revision,action')]
    used={row['action']:row['used'] for row in rows if row['revision']==revision}
    current=[dict(action=action,revision=revision,used=used.get(action,0),max_requests=limit,remaining=max(0,limit-used.get(action,0))) for action,limit in limits.items()]
    return dict(limits=limits,revision=revision,current=current,history=rows,scope='当前任务，每个条件版本分别计数；所有角色、节点与并行候选共用。缓存命中不增加联网次数；无结果或筛选未保留仍可能是已执行的请求。')


def make_source_policy_handler(Base,manager,store=None):
    class Handler(Base):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/v962/health':
                if self.client_address[0] not in ('127.0.0.1','::1') or not self.trusted_host():return self.reply({'error':'Not found'},404)
                try:load_policy(manager.root)
                except (OSError,ValueError,TypeError):return self.reply({'ok':False,'error':'来源设置不可读'},503)
                return self.reply({'ok':True,'team':bool(store),'version':VERSION})
            match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/web-budget',path)
            if match:
                user=self._run_user(path)
                if not user:return
                with manager.lock:job=manager.jobs.get(match[1]);root=job['task_dir'] if job else None;can_edit=self.authorize(path,user,True)
                if root is None:return self.reply({'error':'任务不存在'},404)
                try:return self.reply(dict(budget_overview(root),can_edit=can_edit))
                except (OSError,ValueError,TypeError,sqlite3.Error):return self.reply({'error':'联网预算暂时不可读，原记录保留'},503)
            if path!='/api/team/web-source-policy':return super().do_GET()
            user=self._run_user(path)
            if not user:return
            try:return self.reply(dict(load_policy(manager.root),can_edit=bool(user.get('admin')),notice='全组共用；排除列表以外的公开 HTTPS 来源均可尝试搜索和读取。旧 config.yaml 的 allow_domains 不再用于这一工具。'))
            except (OSError,ValueError,TypeError):return self.reply({'error':'来源设置暂时不可读；未放宽限制'},503)

        def do_POST(self):
            path=urlparse(self.path).path
            match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/web-budget',path)
            if match:
                user=self._run_user(path,write=True)
                if not user:return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=1000:raise ValueError('请求长度无效')
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict) or set(body)!={'search','read'} or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<=1000 for v in body.values()):raise ValueError('搜索和正文上限须为 0～1000 的整数')
                    with manager.lock:job=manager.jobs.get(match[1]);root=Path(job['task_dir']) if job else None
                    if root is None:return self.reply({'error':'任务不存在'},404)
                    from utils.live_updates import atomic_json
                    atomic_json(root/'web_budget_settings.json',body)
                    # Update display and subsequent checks; preserve all used counts.
                    path=root/'research_web.sqlite'
                    if path.is_file():
                        revision=budget_overview(root)['revision']
                        with sqlite3.connect(path,timeout=2) as connection:
                            for action,limit in body.items():connection.execute('UPDATE web_budget SET max_requests=? WHERE action=? AND revision=?',(limit,action,revision))
                    return self.reply(dict(budget_overview(root),ok=True,can_edit=True,notice='上限已保存，已用次数不重置。下一次联网工具请求生效；不会自动重试已失败的搜索，也不会自动恢复研究。'))
                except (ValueError,TypeError):return self.reply({'error':'联网上限须为 0～1000 的整数'},400)
                except (OSError,sqlite3.Error):return self.reply({'error':'联网预算保存未全部确认，请重新读取核对'},503)
            if path!='/api/team/web-source-policy':return super().do_POST()
            user=self._run_user(path,write=True)
            if not user:return
            if not user.get('admin'):return self.reply({'error':'只有小组管理员能修改全组来源设置'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:raise ValueError('请求长度无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or set(body)-{'deny_domains','expected_revision'}:raise ValueError('请求格式错误')
                with store.lock:data=save_policy(manager.root,body.get('deny_domains'),user['username'],body.get('expected_revision'))
                return self.reply(dict(data,can_edit=True,ok=True,notice='已保存。研究进程下一次工具请求会读取新设置；已经返回的历史证据保留。'))
            except RuntimeError as exc:return self.reply({'error':str(exc)},409)
            except (ValueError,TypeError):return self.reply({'error':'网站列表格式无效：最多 150 个，每行填写完整域名或网站地址'},400)
            except OSError:return self.reply({'error':'来源设置保存失败，原设置保留'},503)
    return Handler
