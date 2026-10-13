"""Credentials-file authentication for deployed checks; no secrets in output."""
from __future__ import annotations

import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request


def read_credentials(path):
    if not path:
        raise ValueError("--auth-file に変更済みパスワードを持つ検証用アカウントのJSONファイルを指定してください")
    value=json.loads(Path(path).read_text())
    if not isinstance(value,dict) or not all(isinstance(value.get(k),str) and value[k] for k in ('username','password')):
        raise ValueError("認証ファイルには username と password の文字列が必要です")
    return value


class AuthenticatedApi:
    def __init__(self,base,path):
        self.base=base.rstrip('/');self.csrf=''
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        result=self.request('/api/auth/login',read_credentials(path))
        if result['account']['must_change_password']:
            raise ValueError("ブラウザで仮パスワードを変更してから検証してください")
        self.account=result['account'];self.csrf=result['csrf_token']

    def request(self,path,payload=None,method=None,expected=200):
        headers={'Content-Type':'application/json'}
        if self.csrf:headers['X-CSRF-Token']=self.csrf
        data=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
        try:
            response=self.opener.open(req,timeout=30)
        except urllib.error.HTTPError as error:
            if error.code!=expected:raise RuntimeError(f'{path}: HTTP {error.code}, expected {expected}') from error
            response=error
        with response:
            if response.code!=expected:raise RuntimeError(f'{path}: HTTP {response.code}, expected {expected}')
            content=response.read()
            return json.loads(content) if response.headers.get_content_type()=='application/json' else content.decode()


def authenticate_browser(page,context,base,path):
    response=context.request.post(base.rstrip('/')+'/api/auth/login',data=read_credentials(path))
    if response.status!=200:raise RuntimeError(f'検証アカウントのログインに失敗しました（HTTP {response.status}）')
    session=response.json()
    if session['account']['must_change_password']:raise ValueError('ブラウザで仮パスワードを変更してから検証してください')
    page.goto(base,wait_until='networkidle')
    page.locator('#employee-id').wait_for(state='visible')
    return session['account'],session['csrf_token']
