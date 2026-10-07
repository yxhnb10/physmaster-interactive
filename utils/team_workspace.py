"""Optional workspace for up to three mutually trusted researchers.
Application ACLs are not an OS sandbox for model-generated Python.
"""
import contextlib
import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from cryptography.fernet import Fernet
from utils.live_updates import atomic_json
from utils.capabilities import capability_values, merge_capabilities
from utils.team_auth import AccountMixin, AuthError

ROLES=('supervisor','draft','revise','critic','promoter','clarifier','retrieval_critic','summarizer','critic_strong','supervisor_strong','promoter_strong')
USER_ID=re.compile(r'^[a-zA-Z0-9_-]{2,32}$')


def api_origin(value):
    p=urlparse(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('模型 API 必须是管理员允许的 HTTPS 地址')
    return 'https://'+p.netloc.lower()


def password_hash(password, salt=None):
    if not isinstance(password,str) or not 10<=len(password)<=200:
        raise ValueError('密码长度须为 10～200 个字符')
    salt=salt or secrets.token_hex(16)
    return dict(salt=salt,hash=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex())


class TeamStore(AccountMixin):
    def __init__(self, root, allowed_origins=()):
        self.root=Path(root).resolve();self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lock=threading.RLock();self.sessions={};self.attempts={}
        self.allowed_origins={'https://api.deepseek.com','https://api.openai.com'}
        self.allowed_origins.update(api_origin(u) for u in allowed_origins)
        key=self.root/'master.key'
        if not key.exists():
            with key.open('xb') as f:f.write(Fernet.generate_key())
            key.chmod(0o600)
        self.cipher=Fernet(key.read_bytes())
        self.path=self.root/'members.json'
        self.users=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.init_accounts()

    def save(self):
        atomic_json(self.path,self.users);self.path.chmod(0o600)

    def add_user(self, username, password, admin=False):
        if not isinstance(username,str) or not USER_ID.fullmatch(username):raise ValueError('用户名限 2～32 位字母、数字、下划线和短横线')
        with self.lock:
            if username in self.users:raise ValueError('账号已存在；改密码请用 reset-password')
            if len(self.users)>=3:raise ValueError('此小组版本最多三个成员')
            self.users[username]=dict(username=username,admin=bool(admin),password=password_hash(password),profile=None)
            self.save()

    def reset_password(self,username,password):
        with self.lock:
            if username not in self.users:raise ValueError('账号不存在')
            entry=copy.deepcopy(self.users[username])
            entry['password']=password_hash(password)
            entry['auth_revision']=entry.get('auth_revision',0)+1
            self.commit_member(username,entry)
            self.invalidate_sessions(username)

    def change_password(self,username,current,password):
        with self.lock:
            entry=self.users[username]['password']
            valid=isinstance(current,str) and len(current)<=200
            digest=hashlib.pbkdf2_hmac('sha256',(current if valid else '').encode(),bytes.fromhex(entry['salt']),600000).hex()
            if not valid or not hmac.compare_digest(digest,entry['hash']):raise ValueError('当前密码不正确')
            self.reset_password(username,password)

    def login(self,username,password,remote,remember=False):
        if not isinstance(remember,bool):raise ValueError('记住登录字段无效')
        self.rate_limit(remote,'login')
        with self.lock:
            user=self.users.get(username) if isinstance(username,str) and len(username)<=32 else None
            p=user['password'] if user else dict(salt='00'*16,hash='00'*32)
            valid=isinstance(password,str) and len(password)<=200
            digest=hashlib.pbkdf2_hmac('sha256',(password if valid else '').encode(),bytes.fromhex(p['salt']),600000).hex()
            if not user or user.get('disabled') or not valid or not hmac.compare_digest(digest,p['hash']):
                raise AuthError('用户名或密码错误，或账号已停用',401)
            self.attempts.pop(('login',remote),None)
            return self.make_session(username,remember)

    def session(self,cookie):
        try:
            parsed=SimpleCookie();parsed.load(cookie or '');token=parsed['physmaster_session'].value
        except (KeyError,ValueError,CookieError):return None
        with self.lock:
            record=self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
            if not record or record['expires']<=time.time() or not self.session_user_valid(record):return None
            return dict(record)

    def logout(self,cookie):
        try:
            p=SimpleCookie();p.load(cookie or '');token=p['physmaster_session'].value
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(),None);self.save_sessions()
        except (KeyError,ValueError,CookieError):pass

    def profile(self,username):
        with self.lock:
            value=self.users[username].get('profile')
            return json.loads(self.cipher.decrypt(value.encode())) if value else None

    def public_profile(self,username):
        p=self.profile(username) or {}
        return dict(base_url=p.get('base_url','https://api.deepseek.com/'),model=p.get('model',''),
            model_overrides=p.get('model_overrides',{}),has_api_key=bool(p.get('api_key')),
            has_search_key=bool(p.get('search_key')),allowed_origins=sorted(self.allowed_origins))

    def save_profile(self,username,body):
        p=self.profile(username) or {}
        if body.get('delete_api_key'):p.pop('api_key',None)
        if body.get('delete_search_key'):p.pop('search_key',None)
        for key in ('api_key','search_key'):
            if body.get(key):
                if not isinstance(body[key],str) or not 5<=len(body[key].strip())<=1000:raise ValueError('密钥格式或长度不正确')
                p[key]=body[key].strip()
        url=body.get('base_url',p.get('base_url','https://api.deepseek.com/'))
        if not isinstance(url,str) or len(url)>500 or api_origin(url) not in self.allowed_origins:raise ValueError('此 API 地址尚未由管理员允许')
        model=body.get('model',p.get('model',''))
        if not isinstance(model,str) or not model.strip() or len(model)>120:raise ValueError('请填写实际可用的默认模型名称')
        overrides=body.get('model_overrides',p.get('model_overrides',{}))
        if not isinstance(overrides,dict) or set(overrides)-set(ROLES) or any(not isinstance(v,str) or not v.strip() or len(v)>120 for v in overrides.values()):raise ValueError('角色模型配置无效')
        p.update(base_url=url.rstrip('/')+'/',model=model.strip(),model_overrides={k:v.strip() for k,v in overrides.items()})
        with self.lock:
            self.users[username]['profile']=self.cipher.encrypt(json.dumps(p).encode()).decode();self.save()
        return self.public_profile(username)

    def public_user(self,username):
        return dict(username=username,admin=bool(self.users[username]['admin']))

    def test_connection(self,username):
        p=self.profile(username)
        if not p or not p.get('api_key'):raise ValueError('请先保存 API 配置和密钥')
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        req=Request(p['base_url']+'models',headers={'Authorization':'Bearer '+p['api_key'],'Accept':'application/json'})
        try:
            with build_opener(NoRedirect()).open(req,timeout=15) as response:
                data=json.loads(response.read(1000000))
            models=[r['id'] for r in data.get('data',[]) if isinstance(r,dict) and isinstance(r.get('id'),str)]
            return dict(ok=True,message='模型列表接口可达；尚未进行生成、工具调用或科研验证。',models=models[:100])
        except HTTPError as exc:
            raise ValueError('模型接口返回 HTTP '+str(exc.code)+'；请核对地址、密钥或接口是否支持 /models') from None
        except (URLError,TimeoutError,ValueError,OSError):raise ValueError('连通性检查未通过；请检查服务器网络和 API 地址') from None


def task_readable(job,user):
    return user['admin'] or job.get('owner_id')==user['username'] or bool(job.get('shared'))


def task_writable(job,user):
    return user['admin'] or job.get('owner_id')==user['username']


# Import after class definitions to keep the existing local server dependency-free.
from web_server import TaskManager, make_handler, TERMINAL
import yaml


class TeamTaskManager(TaskManager):
    def __init__(self,root,config_path,store):
        self.store=store;self.actor=threading.local()
        super().__init__(root,config_path);self.queue_enabled=True

    @contextlib.contextmanager
    def as_user(self,user):
        self.actor.user=user
        try:yield
        finally:del self.actor.user

    def create(self,query,capabilities=None,critic_settings=None,hard_parameter_text=None,**kwargs):
        user=self.actor.user
        profile=self.store.profile(user['username'])
        if not profile or not profile.get('api_key'):raise ValueError('请先在“个人 API 设置”保存自己的 API 密钥')
        config=copy.deepcopy(yaml.safe_load(self.config_path.read_text(encoding='utf-8')))
        config['llm']=dict(config.get('llm') or {},base_url=profile['base_url'],model=profile['model'],model_overrides=profile['model_overrides'],api_key_env='PHY_TEAM_LLM_API_KEY')
        config['llm'].pop('api_key',None)
        # Private research must not be distilled into a shared knowledge index.
        config.setdefault('landau',{})['wisdom_save_enabled']=False
        config.setdefault('tools',{})['cache_path']=str(self.store.root/('search-'+user['username']+'.sqlite'))
        config.setdefault('tools',{})['api_key_env']='PHY_TEAM_SEARCH_API_KEY'
        caps=merge_capabilities(capability_values(config),capabilities)
        if caps['web_search'] and config['tools'].get('search_provider','tavily')=='tavily' and not profile.get('search_key'):
            raise ValueError('已启用 Tavily 网页搜索；请保存个人搜索密钥或关闭 WebSearch')
        with self.lock:
            if sum(j['state']=='queued' for j in self.jobs.values())>=6:raise ValueError('队列已满（最多六项等待），请稍后提交')
            if sum(j['state']=='queued' and j.get('owner_id')==user['username'] for j in self.jobs.values())>=2:raise ValueError('每人最多两个排队任务')
            return super().create(query,capabilities,critic_settings,hard_parameter_text,
                **kwargs,owner_id=user['username'],task_config=config,
                task_env={'PHY_TEAM_LLM_API_KEY':profile['api_key'],'PHY_TEAM_SEARCH_API_KEY':profile.get('search_key',''),
                          'OPENAI_API_KEY':'','TAVILY_API_KEY':''})

    def _recovery_env(self,job,config):
        # Recover with the owner's credentials, including when an admin clicks.
        profile=self.store.profile(job.get('owner_id')) if job.get('owner_id') else None
        if not profile or not profile.get('api_key'):raise ValueError('任务所有者需先保存个人 API 密钥')
        if profile['base_url'].rstrip('/')!=config.get('llm',{}).get('base_url','').rstrip('/'):
            raise ValueError('个人 API 地址与原任务不同；请恢复原 API 地址后重试，模型与合同不会自动切换')
        if capability_values(config)['web_search'] and config.get('tools',{}).get('search_provider','tavily')=='tavily' and not profile.get('search_key'):
            raise ValueError('原任务启用了 Tavily，所有者需保存搜索密钥')
        env=super()._recovery_env(job,config)
        env.update(PHY_TEAM_LLM_API_KEY=profile['api_key'],PHY_TEAM_SEARCH_API_KEY=profile.get('search_key',''),OPENAI_API_KEY='',TAVILY_API_KEY='')
        return env

    def snapshot(self,ident,lightweight=False):
        data=super().snapshot(ident,lightweight=lightweight)
        user=getattr(self.actor,'user',None)
        if user:data.update(can_edit=task_writable(data,user))
        return data

    def activity(self,user,action,ident):
        # No prompts, conditions, API secrets or passwords in this log.
        record=dict(at=time.time(),user=user['username'],action=action,task=ident)
        with self.store.lock:
            with (self.store.root/'activity.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')


def make_team_handler(manager,store,public_url):
    parsed=urlparse(public_url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('--public-url 必须为浏览器访问的根地址')
    origin=parsed.scheme+'://'+parsed.netloc
    host=parsed.netloc
    secure=parsed.scheme=='https'
    Base=make_handler(manager)
    class Handler(Base):
        def setup(self):
            super().setup();self.connection.settimeout(30)
        def trusted_host(self):
            # A tunnel may retain the public host or forward a loopback host.
            # Keep the explicit allowlist from the deployed base handler.
            return self.headers.get('Host') in (host, f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
        def trusted_origin(self):return self.headers.get('Origin') in (None,origin)
        def request_token(self):return self.session['csrf'] if getattr(self,'session',None) else manager.token
        def cookie(self,token='',delete=False,max_age=43200):
            return 'physmaster_session='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+('0' if delete else str(max_age))+('; Secure' if secure else '')
        def identify(self):
            self.session=store.session(self.headers.get('Cookie'))
            return store.public_user(self.session['username']) if self.session else None
        def authorize(self,path,user,write=False):
            match=re.match(r'^/api/tasks/([a-f0-9]{32})(?:/|$)',path)
            if not match:return True
            with manager.lock:
                job=manager.jobs.get(match[1])
                return bool(job and (task_writable(job,user) if write else task_readable(job,user)))
        def do_GET(self):
            if not self.trusted_host():return self.reply({'error':'Invalid host'},403)
            path=urlparse(self.path).path;user=self.identify()
            auth_headers={'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; font-src 'self' data:"}
            web=Path(__file__).parents[1]/'web'
            if path in ('/auth.css','/auth.js'):
                name=path[1:];kind='text/css' if name.endswith('.css') else 'application/javascript'
                return self.reply((web/name).read_bytes(),content_type=kind+'; charset=utf-8',headers=auth_headers)
            if path=='/api/team/auth-status':
                return self.reply(dict(registration='invite',available_slots=max(0,3-len(store.users)),max_members=3,csrf=self.request_token(),user=user))
            if path in ('/','/login','/register','/reset-password'):
                if user and path in ('/login','/register'):
                    return self.reply(b'',302,content_type='text/plain',headers={'Location':'/'})
                if not user or path=='/reset-password':
                    page={'/register':'register','/reset-password':'reset'}.get(path,'login')
                    html=(web/'login.html').read_text(encoding='utf-8').replace('__TOKEN__',self.request_token()).replace('__AUTH_PAGE__',page)
                else:
                    html=(web/'index.html').read_text(encoding='utf-8')
                    script=(web/'team.js').read_text(encoding='utf-8')
                    accounts=(web/'accounts.js').read_text(encoding='utf-8')
                    bootstrap='window.PHY_TEAM='+json.dumps(dict(user=user),ensure_ascii=True)+';\n'+script+'\n'+accounts
                    html=html.replace('__TOKEN__',self.request_token()).replace('/*__TEAM_BOOTSTRAP__*/',bootstrap).replace('/*__TEAM_IDENTITY__*/','window.PHY_TEAM='+json.dumps(dict(user=user),ensure_ascii=True)+';')
                return self.reply(html.encode(),content_type='text/html; charset=utf-8',headers=auth_headers)
            if not user:return self.reply({'error':'登录已失效，请重新登录','login_required':True},401)
            if not self.authorize(path,user):return self.reply({'error':'无权查看这个任务'},404)
            with manager.as_user(user):
                if path=='/api/team/profile':return self.reply(store.public_profile(user['username']))
                if path=='/api/team/session':return self.reply(dict(user=user))
                if path=='/api/team/accounts':
                    if not user['admin']:return self.reply({'error':'仅管理员可以管理成员'},403)
                    return self.reply(store.accounts_view())
                if path=='/api/team/workspace':
                    with manager.lock:
                        active=next((j for j in manager.jobs.values() if j['state'] not in TERMINAL and j['state']!='queued'),None)
                        queued=sum(j['state']=='queued' for j in manager.jobs.values())
                    return self.reply(dict(active=bool(active),queued=queued,capacity=1,members=len(store.users)))
                if path=='/api/tasks':
                    with manager.lock:ids=[j['id'] for j in manager.jobs.values() if task_readable(j,user)]
                    return self.reply({'tasks':[manager.history_snapshot(i) for i in ids]})
                return super().do_GET()
        def do_POST(self):
            if not self.trusted_host() or not self.trusted_origin():return self.reply({'error':'请求来源不受信任'},403)
            path=urlparse(self.path).path;user=self.identify()
            if self.headers.get('X-PhysMaster-Token')!=self.request_token():return self.reply({'error':'请求校验失败，请刷新页面'},403)
            if path in ('/api/team/login','/api/team/register','/api/team/reset-password'):
                if user and path!='/api/team/reset-password':return self.reply({'error':'当前已登录，请先退出'},409)
                try:
                    body=self.read_body()
                    # Forwarded headers are not trusted as rate-limit identity. A
                    # loopback tunnel shares this conservative origin-side budget.
                    remote=self.client_address[0]
                    if path=='/api/team/login':
                        token,session=store.login(body.get('username'),body.get('password'),remote,body.get('remember',False))
                        return self.reply({'ok':True},headers={'Set-Cookie':self.cookie(token,max_age=session['max_age'])})
                    store.rate_limit(remote,'register' if path.endswith('/register') else 'reset')
                    if path=='/api/team/register':
                        created=store.register_account(body.get('username'),body.get('password'),body.get('confirm_password'),body.get('invite_code'))
                        return self.reply(dict(ok=True,username=created['username'],message='注册成功，请使用新账号登录'),201)
                    store.recover_password(body.get('reset_code'),body.get('password'),body.get('confirm_password'))
                    return self.reply({'ok':True,'message':'密码已重置，请重新登录'},headers={'Set-Cookie':self.cookie(delete=True)})
                except AuthError as exc:
                    return self.reply({'error':str(exc)},exc.status,headers={'Retry-After':'300'} if exc.status==429 else None)
                except ValueError as exc:return self.reply({'error':str(exc)},400)
                except TypeError:return self.reply({'error':'请求格式或输入无效，请检查输入后重试'},400)
                except Exception:return self.reply({'error':'账号操作暂时失败，请联系管理员检查存储权限'},500)
            if not user:return self.reply({'error':'登录已失效，请重新登录','login_required':True},401)
            if not self.authorize(path,user,write=True):return self.reply({'error':'只有任务所有者或管理员可以修改'},403)
            with manager.as_user(user):
                if path.startswith('/api/team/') or re.fullmatch(r'/api/tasks/[a-f0-9]{32}/(sharing|archive|cancel)',path):
                    try:
                        body=self.read_body()
                        if path=='/api/team/logout':
                            store.logout(self.headers.get('Cookie'));return self.reply({'ok':True},headers={'Set-Cookie':self.cookie(delete=True)})
                        if path=='/api/team/logout-all':
                            store.logout_all(user['username']);return self.reply({'ok':True},headers={'Set-Cookie':self.cookie(delete=True)})
                        if path.startswith('/api/team/accounts/'):
                            if not user['admin']:return self.reply({'error':'仅管理员可以管理成员'},403)
                            if path=='/api/team/accounts/invite':
                                return self.reply(store.issue_token('register',body.get('username') or None,body.get('hours',24)),201)
                            if path=='/api/team/accounts/reset':
                                if body.get('username')==user['username']:raise ValueError('修改自己的密码请使用“修改密码”；忘记密码可使用本机管理工具')
                                return self.reply(store.issue_token('reset',body.get('username'),body.get('hours',1)),201)
                            if path=='/api/team/accounts/revoke':
                                store.revoke_token(body.get('id'));return self.reply({'ok':True})
                            if path=='/api/team/accounts/status':
                                store.set_disabled(body.get('username'),body.get('disabled'),user['username']);return self.reply({'ok':True})
                            return self.reply({'error':'Not found'},404)
                        if path=='/api/team/profile':return self.reply(store.save_profile(user['username'],body))
                        if path=='/api/team/test':return self.reply(store.test_connection(user['username']))
                        if path=='/api/team/password':
                            store.rate_limit(self.client_address[0],'password:'+user['username'])
                            store.change_password(user['username'],body.get('current_password'),body.get('new_password'))
                            return self.reply({'ok':True},headers={'Set-Cookie':self.cookie(delete=True)})
                        match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(sharing|archive|cancel)',path)
                        if not match:return self.reply({'error':'Not found'},404)
                        ident,action=match.groups()
                        with manager.lock:
                            job=manager._get(ident)
                            if action=='cancel':result=manager.cancel_queued(ident)
                            else:
                                field='shared' if action=='sharing' else 'archived'
                                if not isinstance(body.get(field),bool):raise ValueError('字段须为布尔值')
                                if field=='archived' and job['state'] not in TERMINAL:raise ValueError('运行或排队任务暂不能归档')
                                job[field]=body[field];manager._save_job(job);result={field:job[field]}
                            manager.activity(user,action,ident)
                        return self.reply(result)
                    except AuthError as exc:return self.reply({'error':str(exc)},exc.status)
                    except (ValueError,TypeError,KeyError) as exc:return self.reply({'error':str(exc)},400)
                    except Exception:return self.reply({'error':'操作失败，请检查服务器配置与网络'},500)
                result=super().do_POST()
                # Base handler already returns responses; record the attempt only.
                manager.activity(user,'request:'+path,path.split('/')[3] if path.startswith('/api/tasks/') else None)
                return result
        def read_body(self):
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=10000:raise ValueError('请求长度无效')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('请求格式错误')
            return data
    return Handler
