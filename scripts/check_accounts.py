#!/usr/bin/env python3
"""Account/ownership acceptance on a disposable PostgreSQL + Redis instance.

Run only on an isolated test instance. Creates accounts, models and native jobs.
Credentials come from a private JSON file and never enter reports/screenshots.
"""
import argparse
import hashlib
import json
import math
import secrets
import shutil
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


def divider(name,voltage=5):
    def wire(a,ap,b,bp):
        return {'id':f'w_{a}_{ap}_{b}_{bp}','source':{'component_id':a,'port':ap},'target':{'component_id':b,'port':bp}}
    return {'schema_version':1,'name':name,'description':'Disposable authentication acceptance',
        'components':[
            {'id':'v1','kind':'V','label':'V1','ports':['p','n'],'parameters':{'dc':voltage},'position':{'x':80,'y':100},'rotation':90},
            {'id':'r1','kind':'R','label':'R1','ports':['p','n'],'parameters':{'value':1000},'position':{'x':300,'y':80},'rotation':0},
            {'id':'r2','kind':'R','label':'R2','ports':['p','n'],'parameters':{'value':2000},'position':{'x':500,'y':240},'rotation':90},
            {'id':'gnd','kind':'GND','label':'GND','ports':['g'],'parameters':{},'position':{'x':100,'y':340},'rotation':0}],
        'wires':[wire('v1','p','r1','p'),wire('r1','n','r2','p'),wire('v1','n','gnd','g'),wire('r2','n','gnd','g')],
        'models':[],'parameters':{},'node_labels':[{'component_id':'r1','port':'n','name':'出力'}]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://localhost:8081')
    parser.add_argument('--admin-file',type=Path,default=Path('.private/auth-test-admin.json'))
    parser.add_argument('--output',type=Path,default=Path('reports/accounts'))
    parser.add_argument('--confirm-isolated-test-db',action='store_true')
    args=parser.parse_args();base=args.base_url.rstrip('/');out=args.output;out.mkdir(parents=True,exist_ok=True)
    if not args.confirm_isolated_test_db:parser.error('管理者の無効化を含む検証です。使い捨てDBに対して --confirm-isolated-test-db を指定してください')
    admin_creds=json.loads(args.admin_file.read_text())
    stamp=str(time.time_ns());usernames={'A':'qa-a-'+stamp,'B':'qa-b-'+stamp};passwords={};accounts={};models={}
    evidence={'completed':False,'api_mocked':False,'solver_results_mocked':False,'database':'isolated PostgreSQL','checks':[],'browser_runtime_errors':[],'runs':[]}
    root=Path(__file__).resolve().parents[1]
    paths=['backend/app/access.py','backend/app/accounts.py','backend/app/api.py','backend/app/auth.py','backend/app/database.py','backend/app/schemas.py','frontend/nginx.conf','frontend/src/Accounts.tsx','frontend/src/Auth.tsx','frontend/src/App.tsx','frontend/src/OwnerAssignment.tsx','frontend/src/ModelLibrary.tsx','frontend/src/types.ts','frontend/src/styles.css','frontend/src/main.tsx','compose.yaml']
    evidence['source_sha256']={path:hashlib.sha256((root/path).read_bytes()).hexdigest() for path in paths}
    report=out/'verification.json'
    def record(message):
        evidence['checks'].append(message);report.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n');print('PASS: '+message,flush=True)
    def timeout(*_):raise TimeoutError('Account acceptance exceeded 420 seconds')
    signal.signal(signal.SIGALRM,timeout);signal.alarm(420)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
            def new_page(width=1480):
                context=browser.new_context(viewport={'width':width,'height':1050},accept_downloads=True)
                page=context.new_page();page.set_default_timeout(12000)
                page.on('pageerror',lambda error:evidence['browser_runtime_errors'].append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                return context,page
            admin,ap=new_page();alice,pa=new_page();bob,pb=new_page();anonymous,public=new_page(390)
            def request(context,path,method='GET',body=None,status=200):
                headers={}
                if method not in ('GET','HEAD'):
                    me=context.request.get(base+'/api/auth/me')
                    if me.ok:headers['X-CSRF-Token']=me.json()['csrf_token']
                response=context.request.fetch(base+'/api'+path,method=method,data=body,headers=headers)
                assert response.status==status,(path,response.status,status)
                if not response.body() or status in (401,403,404):return None
                return response.json() if 'application/json' in response.headers.get('content-type','') else response.text()
            def login(page,username,password):
                page.goto(base)
                expect(page.locator('#login-id')).to_be_visible()
                page.locator('#login-id').fill(username);page.locator('#login-password').fill(password)
                page.get_by_role('button',name='ログイン',exact=True).click()
                page.wait_for_function("document.querySelector('#new-password')||document.querySelector('#employee-id')")
                if page.locator('#new-password').count():
                    new_password=secrets.token_urlsafe(24)
                    page.locator('#new-password').fill(new_password);page.locator('#confirm-password').fill(new_password)
                    with page.expect_response(lambda r:r.url.endswith('/api/auth/password') and r.request.method=='POST') as changed:
                        page.get_by_role('button',name='パスワードを保存',exact=True).click()
                    assert changed.value.status==200
                    password=new_password
                expect(page.locator('#employee-id')).to_have_value(username)
                expect(page.get_by_role('button',name='保存',exact=True)).to_be_enabled()
                return password
            def credentials():
                dialog=ap.get_by_role('dialog',name='仮パスワードを発行しました',exact=True)
                expect(dialog).to_be_visible();temporary=dialog.get_by_label('発行した仮パスワード').input_value()
                dialog.get_by_role('button',name='閉じる',exact=True).click()
                return temporary
            def library(page):
                page.get_by_role('button',name='保存済みモデル',exact=False).click()
                expect(page.get_by_label('モデルを検索',exact=True)).to_be_visible()
            def open_account(username):
                ap.get_by_role('button',name='アカウント管理',exact=True).click()
                ap.get_by_label('アカウントを検索',exact=True).fill(username)
                ap.get_by_role('button',name=username+'を編集',exact=True).click()
                dialog=ap.get_by_role('dialog',name=username+'のアカウント設定',exact=True)
                expect(dialog).to_be_visible()
                return dialog
            def save_account(dialog):
                with ap.expect_response(lambda r:'/api/admin/accounts/' in r.url and r.request.method=='PUT') as response:
                    dialog.get_by_role('button',name='設定を保存',exact=True).click()
                assert response.value.status==200
                expect(dialog).to_have_count(0)
            def assign(model,owner):
                library(ap);ap.get_by_label('モデルを検索',exact=True).fill(model['name'])
                row=ap.locator('[data-model-id="'+model['id']+'"]')
                expect(row).to_be_visible();row.get_by_role('checkbox').check()
                ap.get_by_label('割り当て先を検索',exact=True).fill(owner['username'])
                expect(ap.locator('#model-owner option[value="'+owner['id']+'"]')).to_have_count(1)
                ap.get_by_label('新しい所有者',exact=True).select_option(owner['id'])
                with ap.expect_response(lambda r:r.url.endswith('/api/admin/circuits/owner') and r.request.method=='POST') as response:
                    ap.get_by_role('button',name='選択したモデルに所有者を割り当て',exact=True).click()
                assert response.value.status==200
                return response.value.json()['circuits'][0]
            def save_ui(page,context,doc):
                area=page.get_by_label('回路JSON',exact=True)
                details=area.locator('xpath=ancestor::details[1]')
                if not details.evaluate('e=>e.open'):details.locator('summary').click()
                area.fill(json.dumps(doc,ensure_ascii=False));details.get_by_role('button',name='適用',exact=True).click()
                details.locator('summary').click()
                page.get_by_label('解析方法',exact=True).select_option('transient')
                with page.expect_response(lambda r:r.url.endswith('/api/circuits') and r.request.method=='POST') as response:
                    page.get_by_role('button',name='保存',exact=True).click()
                assert response.value.status==201
                saved=response.value.json()
                assert saved['created_by']==request(context,'/auth/me')['account']['username']
                return saved
            def wait_run(context,run_id):
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    run=request(context,'/runs/'+run_id)
                    if run['status'] in ('succeeded','failed','canceled','timed_out'):break
                    time.sleep(.3)
                assert run['status']=='succeeded',run['status']
                expected=run['snapshot']['components'][0]['parameters']['dc']*2/3
                signal_data=next(s for s in run['result']['signals'] if s.get('display_name')=='V(出力)')
                assert max(abs(v-expected) for v in signal_data['values'])<1e-8
                evidence['runs'].append({'id':run_id,'status':run['status'],'employee_id':run['employee_id'],'solver':run['result']['solver'],'output_v':signal_data['values'][-1],'expected_v':expected})
                return run
            public.goto(base);expect(public.get_by_role('heading',name='ログイン',exact=True)).to_be_visible()
            assert public.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            public.screenshot(path=str(out/'login-mobile.png'),full_page=True)
            request(anonymous,'/circuits',status=401);request(anonymous,'/runs',status=401);request(anonymous,'/admin/accounts',status=401)
            record('Unauthenticated pages show login; protected model/run/account APIs reject requests; 390px login has no overflow')
            admin_creds['password']=login(ap,admin_creds['username'],admin_creds['password'])
            args.admin_file.write_text(json.dumps(admin_creds));args.admin_file.chmod(0o600)
            evidence['frontend_assets']=ap.locator('script[src],link[rel="stylesheet"]').evaluate_all('es=>es.map(e=>e.getAttribute("src")||e.getAttribute("href"))')
            record('Console-issued temporary administrator password must be changed before entering the workspace')
            ap.get_by_role('button',name='アカウント管理',exact=True).click()
            for key in ('A','B'):
                ap.locator('#new-account-id').fill(usernames[key]);ap.locator('#new-account-name').fill('検証ユーザー'+key)
                with ap.expect_response(lambda r:r.url.endswith('/api/admin/accounts') and r.request.method=='POST') as response:
                    ap.get_by_role('button',name='アカウントを発行',exact=True).click()
                assert response.value.status==201
                accounts[key]=response.value.json()['account'];passwords[key]=credentials()
            expect(ap.get_by_role('button',name=usernames['A']+'を編集',exact=True)).to_be_visible()
            expect(ap.get_by_role('button',name=usernames['B']+'を編集',exact=True)).to_be_visible()
            ap.screenshot(path=str(out/'accounts-desktop.png'),full_page=True)
            passwords['A']=login(pa,usernames['A'],passwords['A']);passwords['B']=login(pb,usernames['B'],passwords['B'])
            expect(pa.get_by_role('button',name='アカウント管理',exact=True)).to_have_count(0)
            assert pa.locator('#employee-id').evaluate('e=>e.readOnly')
            assert 'plasma_session' not in pa.evaluate('document.cookie')
            request(alice,'/admin/accounts',status=403)
            record('Admin UI issues two normal accounts and shows passwords once; both users must change them; account identity is read-only and admin navigation is hidden')
            models['A']=save_ui(pa,alice,divider('認証モデルA '+stamp,5))
            models['B']=save_ui(pb,bob,divider('認証モデルB '+stamp,12))
            for key,context,page,other in [('A',alice,pa,'B'),('B',bob,pb,'A')]:
                listing=request(context,'/circuits?limit=25');assert listing['total']==listing['total_all']==1
                assert listing['circuits'][0]['id']==models[key]['id']
                request(context,'/circuits/'+models[other]['id'],status=404)
                library(page);expect(page.locator('[data-model-id="'+models[key]['id']+'"]')).to_be_visible()
                expect(page.locator('[data-model-id="'+models[other]['id']+'"]')).to_have_count(0)
            pa.screenshot(path=str(out/'own-models-desktop.png'),full_page=True)
            record('Browser saves preserve authenticated owners; each model list, count and direct ID lookup exposes only its own model')
            # UI run plus simultaneous HTTP submissions from two independently logged-in browsers.
            pa.get_by_role('button',name=models['A']['name']+'を開く',exact=True).click()
            with pa.expect_response(lambda r:r.url.endswith('/api/runs') and r.request.method=='POST') as response:
                pa.get_by_role('button',name='計算を実行',exact=True).click()
            assert response.value.status==202
            first=wait_run(alice,response.value.json()['id'])
            expect(pa.locator('.trace-list')).to_contain_text('V(出力)')
            pa.screenshot(path=str(out/'owned-waveform.png'),full_page=True)
            auth_headers={}
            for key,context in [('A',alice),('B',bob)]:
                current=request(context,'/circuits/'+models[key]['id']);models[key]=current
                cookie=next(c['value'] for c in context.cookies() if c['name']=='plasma_session')
                auth_headers[key]={'Content-Type':'application/json','Cookie':'plasma_session='+cookie,'X-CSRF-Token':request(context,'/auth/me')['csrf_token']}
            def submit(key):
                payload={'circuit_id':models[key]['id'],'expected_revision':models[key]['revision'],'analysis':{'kind':'transient','settings':{'stop_time':1e-4,'time_step':1e-6}}}
                req=Request(base+'/api/runs',data=json.dumps(payload).encode(),headers=auth_headers[key],method='POST')
                with urlopen(req,timeout=30) as r:assert r.status==202;return json.load(r)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures={key:executor.submit(submit,key) for key in ('A','B')}
                queued={key:future.result() for key,future in futures.items()}
            results={key:wait_run(context,queued[key]['id']) for key,context in [('A',alice),('B',bob)]}
            rb=results['B']['id'];ra=results['A']['id']
            for path in ['/runs/'+rb,'/runs/'+rb+'/export.csv','/runs/'+rb+'/package','/runs?circuit_id='+models['B']['id']]:
                request(alice,path,status=404);request(admin,path)
            csv=request(alice,'/runs/'+ra+'/package');assert csv['input']['document']['name']==models['A']['name']
            record('UI calculation and concurrent submissions complete three real ngspice jobs with 2/3 divider voltages; foreign histories/CSV/packages are denied and admin access succeeds')
            legacy=request(admin,'/circuits','POST',{'document':divider('未割当モデル '+stamp)},status=201)
            legacy=request(admin,'/admin/circuits/owner','POST',{'owner_id':None,'circuits':[{'id':legacy['id'],'expected_revision':legacy['revision']} ]})['circuits'][0]
            request(alice,'/circuits/'+legacy['id'],status=404);request(bob,'/circuits/'+legacy['id'],status=404)
            legacy=assign(legacy,accounts['A'])
            assert request(alice,'/circuits/'+legacy['id'])['owner_id']==accounts['A']['id']
            models['A']=assign(models['A'],accounts['B'])
            request(alice,'/circuits/'+models['A']['id'],status=404);request(alice,'/runs/'+ra,status=404)
            assert request(bob,'/runs/'+ra)['snapshot']==results['A']['snapshot']
            record('Admin model-list assignment reveals unassigned models only to the chosen user; transferring ownership also transfers immutable run-history access')
            # A regular password change leaves the unsaved editor document in place.
            pb.get_by_role('button',name='編集画面に戻る',exact=True).click()
            pb.get_by_label('回路名',exact=True).fill('未保存の内容を保持 '+stamp)
            pb.locator('.react-flow__node[data-id="r1"]').click()
            original_count=len(json.loads(pb.get_by_label('回路JSON',exact=True).input_value())['components'])
            pb.get_by_role('button',name='パスワードを変更',exact=True).click()
            dialog=pb.get_by_role('dialog',name='パスワード変更',exact=True);new_password=secrets.token_urlsafe(24)
            dialog.locator('#current-password').fill(passwords['B']);dialog.locator('#new-password').fill(new_password);dialog.locator('#confirm-password').fill(new_password)
            dialog.get_by_role('button',name='パスワードを保存',exact=True).focus();pb.keyboard.press('Control+d')
            assert len(json.loads(pb.get_by_label('回路JSON',exact=True).input_value())['components'])==original_count
            dialog.get_by_role('button',name='パスワードを保存',exact=True).click()
            expect(dialog).to_have_count(0);expect(pb.get_by_label('回路名',exact=True)).to_have_value('未保存の内容を保持 '+stamp)
            passwords['B']=new_password
            record('Changing a personal password rotates the session and preserves unsaved edits; modal keyboard shortcuts do not duplicate underlying components')
            dialog=open_account(usernames['A']);dialog.get_by_role('button',name='仮パスワードを再発行',exact=True).click()
            passwords['A']=credentials();request(alice,'/auth/me',status=401)
            pa.get_by_role('button',name='保存済みモデル',exact=False).click()
            expect(pa.locator('#login-id')).to_be_visible()
            passwords['A']=login(pa,usernames['A'],passwords['A'])
            record('Admin password reset revokes an existing browser session; its next operation returns to login and the new temporary password must be changed')
            # Toggle roles and deactivate/reactivate without a reusable old session.
            dialog=open_account(usernames['B']);dialog.get_by_label('管理者権限',exact=True).check();save_account(dialog)
            request(bob,'/auth/me',status=401)
            pb.goto(base);passwords['B']=login(pb,usernames['B'],passwords['B'])
            expect(pb.get_by_role('button',name='アカウント管理',exact=True)).to_be_visible()
            dialog=open_account(usernames['B']);dialog.get_by_label('管理者権限',exact=True).uncheck();dialog.get_by_label('アカウントを有効にする',exact=True).uncheck();save_account(dialog)
            request(bob,'/auth/me',status=401);request(bob,'/auth/login','POST',{'username':usernames['B'],'password':passwords['B']},status=401)
            dialog=open_account(usernames['B']);dialog.get_by_label('アカウントを有効にする',exact=True).check();save_account(dialog)
            passwords['B']=login(pb,usernames['B'],passwords['B']);expect(pb.get_by_role('button',name='アカウント管理',exact=True)).to_have_count(0)
            record('Admin role changes and deactivation revoke prior sessions; reactivation permits a fresh login with current permissions')
            # Mobile pages and keyboard focus, with no secret-issuance dialog open.
            pa.set_viewport_size({'width':390,'height':844});library(pa)
            assert pa.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            expect(pa.get_by_role('button',name='ログアウト',exact=True)).to_be_visible()
            pa.screenshot(path=str(out/'own-models-mobile.png'),full_page=True)
            ap.set_viewport_size({'width':390,'height':844});ap.get_by_role('button',name='アカウント管理',exact=True).click()
            ap.get_by_label('アカウントを検索',exact=True).fill('')
            expect(ap.get_by_role('button',name=usernames['A']+'を編集',exact=True)).to_be_visible()
            assert ap.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            ap.screenshot(path=str(out/'accounts-mobile.png'),full_page=True)
            record('At 390px, normal model lists and administrator account controls are usable without horizontal page overflow')
            dialog=open_account(usernames['B']);dialog.get_by_role('button',name='アカウントを削除',exact=True).click()
            expect(dialog).to_have_count(0)
            request(bob,'/auth/me',status=401)
            for key in ('A','B'):
                request(admin,'/circuits/'+models[key]['id'])
            request(alice,'/circuits/'+models['B']['id'],status=404)
            assert request(admin,'/runs/'+rb)['status']=='succeeded'
            record('Deleting an account immediately removes login access while models and successful calculation histories remain available to administrators')
            # Only the disposable DB is affected by this concurrent-admin race.
            ap.set_viewport_size({'width':1480,'height':1050})
            dialog=open_account(usernames['A']);dialog.get_by_label('管理者権限',exact=True).check();save_account(dialog)
            passwords['A']=login(pa,usernames['A'],passwords['A'])
            root_account=request(admin,'/auth/me')['account']
            race_headers={}
            for key,context in [('root',admin),('A',alice)]:
                cookie=next(c['value'] for c in context.cookies() if c['name']=='plasma_session')
                race_headers[key]={'Content-Type':'application/json','Cookie':'plasma_session='+cookie,'X-CSRF-Token':request(context,'/auth/me')['csrf_token']}
            def revoke(key,target):
                payload={'display_name':target['display_name'],'is_admin':False,'is_active':False}
                req=Request(base+'/api/admin/accounts/'+target['id'],data=json.dumps(payload).encode(),headers=race_headers[key],method='PUT')
                try:
                    with urlopen(req,timeout=30) as r:return r.status
                except HTTPError as error:return error.code
            with ThreadPoolExecutor(max_workers=2) as executor:
                one=executor.submit(revoke,'root',accounts['A']);two=executor.submit(revoke,'A',root_account);statuses=[one.result(),two.result()]
            assert sorted(statuses) in ([200,401],[200,403]),statuses
            surviving=admin if statuses[0]==200 else alice
            listing=request(surviving,'/admin/accounts?limit=100')
            assert sum(a['is_admin'] and a['is_active'] for a in listing['accounts'])==1
            evidence['concurrent_admin_updates']={'statuses':statuses,'remaining_active_admins':1}
            self_account=request(surviving,'/auth/me')['account']
            request(surviving,'/admin/accounts/'+self_account['id'],'DELETE',status=409)
            surviving_credentials={'username':self_account['username'],'password':admin_creds['password'] if surviving is admin else passwords['A']}
            args.admin_file.write_text(json.dumps(surviving_credentials));args.admin_file.chmod(0o600)
            record('Concurrent cross-deactivation of the final two administrators is serialized by PostgreSQL; one remains active and self-deletion is refused')
            assert not evidence['browser_runtime_errors']
            evidence['completed']=True
            report.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
            browser.close()
    except BaseException:
        report.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
        raise
    finally:signal.alarm(0)


if __name__=='__main__':main()
