"""Invite-only accounts and recovery for the existing three-member workspace.

Token hashes, encrypted remembered sessions, and existing password hashes are
stored separately from research. Password/token changes are committed through
members.json; token consumption remains effective even if housekeeping fails.
"""
import copy
import hashlib
import hmac
import json
import re
import secrets
import time

from utils.live_updates import atomic_json
from cryptography.fernet import InvalidToken

MAX_MEMBERS = 3
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 7 * 86400
TOKEN_PATTERN = re.compile(r'^[a-f0-9]{16}\.[A-Za-z0-9_-]{43}$')


class AuthError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class AccountMixin:
    def init_accounts(self):
        self.tokens_path = self.root / 'account_tokens.json'
        self.sessions_path = self.root / 'remembered_sessions.json'
        self.account_tokens = json.loads(self.tokens_path.read_text(encoding='utf-8')) if self.tokens_path.exists() else {}
        if self.sessions_path.exists():
            # A damaged or stale session cache must never prevent server startup.
            try:
                raw = json.loads(self.sessions_path.read_text(encoding='utf-8'))
                records = json.loads(self.cipher.decrypt(raw['encrypted'].encode()))
                if not isinstance(records, dict):
                    raise ValueError('invalid session cache')
                now = time.time()
                self.sessions = {k: v for k, v in records.items()
                                 if isinstance(v, dict) and v.get('remember') is True
                                 and v.get('expires', 0) > now and self.session_user_valid(v)}
            except (ValueError, KeyError, TypeError, OSError, InvalidToken):
                # Credential/key problems invalidate sessions; member data stays intact.
                self.sessions = {}

    def session_user_valid(self, record):
        user = self.users.get(record.get('username'))
        return bool(user and not user.get('disabled')
                    and record.get('auth_revision', 0) == user.get('auth_revision', 0)
                    and record.get('password_fingerprint') == self.password_fingerprint(user))

    @staticmethod
    def password_fingerprint(user):
        # Also catches password changes made by an older CLI during a rollback.
        return hashlib.sha256(user['password']['hash'].encode()).hexdigest()

    def save_sessions(self):
        now = time.time()
        records = {k: v for k, v in self.sessions.items()
                   if v.get('remember') and v['expires'] > now and self.session_user_valid(v)}
        encrypted = self.cipher.encrypt(json.dumps(records).encode()).decode()
        atomic_json(self.sessions_path, {'encrypted': encrypted})
        self.sessions_path.chmod(0o600)

    def make_session(self, username, remember=False):
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items()
                         if v['expires'] > now and self.session_user_valid(v)}
        own = sorted(((k, v) for k, v in self.sessions.items() if v['username'] == username),
                     key=lambda pair: pair[1]['expires'])
        for k, _ in own[:-7]:
            self.sessions.pop(k, None)
        token = secrets.token_urlsafe(32)
        duration = REMEMBER_SECONDS if remember else SESSION_SECONDS
        record = dict(username=username, csrf=secrets.token_urlsafe(32), expires=now+duration,
                      remember=remember, max_age=duration, auth_revision=self.users[username].get('auth_revision', 0),
                      password_fingerprint=self.password_fingerprint(self.users[username]))
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = record
        self.save_sessions()
        return token, record

    def invalidate_sessions(self, username):
        self.sessions = {k: v for k, v in self.sessions.items() if v['username'] != username}
        self.save_sessions()

    def commit_member(self, username, entry):
        before = copy.deepcopy(self.users)
        self.users[username] = entry
        try:
            self.save()
        except Exception:
            self.users = before
            raise

    def save_tokens(self):
        atomic_json(self.tokens_path, self.account_tokens)
        self.tokens_path.chmod(0o600)

    def token_usable(self, record):
        if record.get('revoked') or record['expires'] <= time.time():
            return False
        if record['purpose'] == 'register':
            return not any(u.get('invitation_id') == record['id'] for u in self.users.values())
        user = self.users.get(record['username'])
        return bool(user and not user.get('disabled')
                    and user.get('auth_revision', 0) == record['auth_revision']
                    and record.get('password_fingerprint') == self.password_fingerprint(user))

    def issue_token(self, purpose, username=None, hours=24):
        if purpose not in ('register', 'reset'):
            raise ValueError('无效的链接用途')
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 1 <= hours <= 168:
            raise ValueError('链接有效期须为 1～168 小时')
        if username is not None:
            if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{2,32}', username):
                raise ValueError('用户名限 2～32 位字母、数字、下划线和短横线')
        with self.lock:
            if purpose == 'register':
                if len(self.users) >= MAX_MEMBERS:
                    raise ValueError('此小组版本最多三个成员；已有账号请直接登录')
                if username and username in self.users:
                    raise ValueError('账号已存在')
            else:
                if not isinstance(username, str) or username not in self.users or self.users[username].get('disabled'):
                    raise ValueError('请选择一个可登录的成员账号')
            before = copy.deepcopy(self.account_tokens)
            # Expired/used records can be discarded; never persist plaintext tokens.
            self.account_tokens = {k: v for k, v in self.account_tokens.items() if self.token_usable(v)}
            if len(self.account_tokens) >= 30:
                raise ValueError('有效链接过多，请先撤销不再使用的链接')
            if purpose == 'reset':
                for row in self.account_tokens.values():
                    if row['purpose'] == 'reset' and row['username'] == username:
                        row['revoked'] = True
            ident = secrets.token_hex(8)
            token = ident + '.' + secrets.token_urlsafe(32)
            row = dict(id=ident, purpose=purpose, username=username,
                       digest=hashlib.sha256(token.encode()).hexdigest(), created=time.time(),
                       expires=time.time()+hours*3600, revoked=False)
            if purpose == 'reset':
                row['auth_revision'] = self.users[username].get('auth_revision', 0)
                row['password_fingerprint'] = self.password_fingerprint(self.users[username])
            self.account_tokens[ident] = row
            try:
                self.save_tokens()
            except Exception:
                self.account_tokens = before
                raise
            return dict(id=ident, token=token, expires=row['expires'], username=username, purpose=purpose)

    def resolve_token(self, token, purpose):
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise AuthError('链接或邀请码无效、已过期或已使用，请联系管理员重新生成')
        record = self.account_tokens.get(token.split('.')[0])
        if not record or record['purpose'] != purpose or not self.token_usable(record):
            raise AuthError('链接或邀请码无效、已过期或已使用，请联系管理员重新生成')
        if not hmac.compare_digest(record['digest'], hashlib.sha256(token.encode()).hexdigest()):
            raise AuthError('链接或邀请码无效、已过期或已使用，请联系管理员重新生成')
        return record

    def register_account(self, username, password, confirm, token):
        from utils.team_workspace import password_hash, USER_ID
        if not isinstance(username, str) or not USER_ID.fullmatch(username):
            raise ValueError('用户名限 2～32 位字母、数字、下划线和短横线')
        if password != confirm:
            raise ValueError('两次密码输入不一致')
        with self.lock:
            invitation = self.resolve_token(token, 'register')
            if invitation['username'] and username != invitation['username']:
                raise ValueError('此邀请仅适用于用户名：' + invitation['username'])
            if username in self.users:
                raise AuthError('用户名已被使用，请登录或选择另一个用户名', 409)
            if len(self.users) >= MAX_MEMBERS:
                raise AuthError('小组成员名额已满，请联系管理员', 409)
            entry = dict(username=username, admin=False, password=password_hash(password), profile=None,
                         auth_revision=0, disabled=False, invitation_id=invitation['id'], created=time.time())
            # The committed member row consumes the invitation atomically with creation.
            self.commit_member(username, entry)
            return self.public_user(username)

    def recover_password(self, token, password, confirm):
        if password != confirm:
            raise ValueError('两次密码输入不一致')
        with self.lock:
            record = self.resolve_token(token, 'reset')
            self.reset_password(record['username'], password)

    def revoke_token(self, ident):
        with self.lock:
            row = self.account_tokens.get(ident)
            if not row:
                raise ValueError('链接不存在或已清理')
            old = row.get('revoked', False)
            row['revoked'] = True
            try:
                self.save_tokens()
            except Exception:
                row['revoked'] = old
                raise

    def set_disabled(self, username, disabled, actor):
        if not isinstance(username, str) or username not in self.users or not isinstance(disabled, bool):
            raise ValueError('账号或状态无效')
        if username == actor:
            raise ValueError('不能停用自己的账号')
        with self.lock:
            entry = copy.deepcopy(self.users[username])
            if disabled and entry['admin'] and not any(u['admin'] and not u.get('disabled')
                                                       for n, u in self.users.items() if n != username):
                raise ValueError('至少保留一个可登录的管理员')
            entry['disabled'] = disabled
            entry['auth_revision'] = entry.get('auth_revision', 0)+1
            self.commit_member(username, entry)
            self.invalidate_sessions(username)

    def logout_all(self, username):
        with self.lock:
            entry = copy.deepcopy(self.users[username])
            entry['auth_revision'] = entry.get('auth_revision', 0)+1
            self.commit_member(username, entry)
            self.invalidate_sessions(username)

    def accounts_view(self):
        with self.lock:
            return dict(max_members=MAX_MEMBERS, available_slots=max(0, MAX_MEMBERS-len(self.users)),
                        members=[dict(self.public_user(n), disabled=bool(u.get('disabled')),
                                      has_api_key=self.public_profile(n)['has_api_key']) for n, u in self.users.items()],
                        links=[dict(id=r['id'], purpose=r['purpose'], username=r.get('username'), expires=r['expires'])
                               for r in self.account_tokens.values() if self.token_usable(r)])

    def rate_limit(self, remote, category):
        key = (category, remote)
        now = time.time()
        with self.lock:
            # Bound anonymous state even when a directly exposed origin sees many IPs.
            self.attempts = {k: [t for t in v if now-t < 300] for k, v in self.attempts.items()
                             if any(now-t < 300 for t in v)}
            if key not in self.attempts and len(self.attempts) >= 2048:
                raise AuthError('请求过多，请五分钟后重试', 429)
            values = self.attempts.get(key, [])
            if len(values) >= (6 if category == 'login' else 10):
                raise AuthError('尝试过多，请五分钟后重试', 429)
            self.attempts[key] = values + [now]
