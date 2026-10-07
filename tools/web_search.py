"""Tavily search/extract; failed requests are never stored as successful empties."""
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlparse
from .cache import Cache

VERSION='web-tools-v962'
from utils.web_source_policy import load_policy,policy_fingerprint,domain_allowed


def public_url(url):
    if not isinstance(url,str) or len(url)>2000:raise ValueError('invalid_url')
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError('public_https_required')
    host=p.hostname.rstrip('.').lower()
    if '.' not in host or host in {'localhost','metadata.google.internal'} or host.endswith(('.local','.internal','.localhost')):raise ValueError('public_https_required')
    try:
        if not ipaddress.ip_address(host).is_global:raise ValueError('public_https_required')
    except ValueError as e:
        if str(e)=='public_https_required':raise
    # Provider performs extraction; PhysMaster never directly connects to this URL.
    return url


def error_kind(exc):
    # Do not persist provider exception text, which can contain credentials.
    name=type(exc).__name__.lower();status=getattr(getattr(exc,'response',None),'status_code',None)
    if status in (401,403) or any(x in name for x in ('invalidapikey','unauthorized','forbidden')):return 'authentication'
    if status==429 or any(x in name for x in ('usage','limit','quota')):return 'rate_or_quota'
    if 'timeout' in name:return 'timeout'
    if isinstance(exc,ImportError):return 'dependency_missing'
    return 'provider_error'


class WebSearch:
    def __init__(self,cfg):
        self.cfg=dict(cfg or {});self.enabled=bool(self.cfg.get('web_enabled',False));self.provider=self.cfg.get('search_provider','none')
        self.cache=Cache(self.cfg.get('cache_path','.cache/web.sqlite'),self.cfg.get('cache_ttl_hours',168))
    def _key(self,action,value,k):
        opts={name:self.cfg.get(name) for name in ('search_provider','allow_domains','cache_ttl_hours')}
        return VERSION+'::'+hashlib.sha256(json.dumps([action,value,k,opts,policy_fingerprint()],sort_keys=True).encode()).hexdigest()
    def _client(self):
        key=os.environ.get(self.cfg.get('api_key_env','TAVILY_API_KEY'),'')
        if not key:return None
        from tavily import TavilyClient
        return TavilyClient(api_key=key)
    def _allowed(self,url):
        return domain_allowed(url,load_policy())
    def detailed(self,action,value,k=3):
        if not self.enabled:return dict(status='disabled',error_code='disabled',results=[])
        if action=='read' and not self.cfg.get('source_read_enabled',True):return dict(status='disabled',error_code='source_read_disabled',results=[])
        if self.provider!='tavily':return dict(status='failed',error_code='unsupported_provider',results=[])
        try:policy=load_policy()
        except (OSError,ValueError,TypeError):return dict(status='failed',error_code='source_policy_unavailable',results=[])
        if action=='read':
            try:public_url(value)
            except (ValueError,TypeError):return dict(status='failed',error_code='public_https_required',results=[])
            if not domain_allowed(value,policy):return dict(status='failed',error_code='domain_excluded',results=[])
        cachekey=self._key(action,value,k);cached=self.cache.get(cachekey)
        if isinstance(cached,dict) and cached.get('status') in ('success','no_results') and cached.get('expires_at',0)>time.time():return dict(cached,cached=True)
        try:
            client=self._client()
            if client is None:return dict(status='failed',error_code='missing_key',results=[])
            timeout=max(2,min(60,float(self.cfg.get('timeout_s',20))))
            if action=='search':
                r=client.search(query=value,max_results=max(1,min(8,int(k))),exclude_domains=policy['deny_domains'] or None,search_depth='basic',include_answer=False,include_raw_content=False,timeout=timeout)
            else:r=client.extract(urls=[value],extract_depth='basic',format='text',timeout=timeout)
            if not isinstance(r,dict) or not isinstance(r.get('results'),list):return dict(status='failed',error_code='invalid_response',results=[])
            try:current_policy=load_policy()
            except (OSError,ValueError,TypeError):return dict(status='failed',error_code='source_policy_unavailable',results=[])
            if action=='read' and not domain_allowed(value,current_policy):return dict(status='failed',error_code='domain_excluded',results=[])
            rows=[]
            excluded=0
            for item in r['results']:
                if not isinstance(item,dict):continue
                url=item.get('url','')
                try:public_url(url)
                except (ValueError,TypeError):continue
                if not domain_allowed(url,current_policy):excluded+=1;continue
                content=str((item.get('raw_content') or '') if action=='read' else item.get('content') or item.get('snippet') or '')
                if action=='read' and not content.strip():continue
                rows.append(dict(title=str(item.get('title') or url)[:300],url=url,snippet=content[:1400] if action=='search' else '',content=content[:200000] if action=='read' else '',content_truncated=action=='read' and len(content)>200000))
            if action=='read' and not rows:return dict(status='failed',error_code='extraction_failed',results=[])
            out=dict(status='success' if rows else 'no_results',results=rows,cached=False,excluded_result_count=excluded,expires_at=time.time()+(300 if not rows else max(0,float(self.cfg.get('cache_ttl_hours',168)))*3600))
            self.cache.put(cachekey,out);return out
        except Exception as exc:return dict(status='failed',error_code=error_kind(exc),results=[])
    def search(self,query,k=None):
        """Legacy interface. Raises failures rather than pretending they are no results."""
        out=self.detailed('search',query,int(k or self.cfg.get('max_results',3)))
        if out['status']=='failed':raise RuntimeError('WebSearch '+out['error_code'])
        return out['results']
