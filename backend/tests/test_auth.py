"""Real cookies, durable sessions and ownership boundaries; queue mocked only."""
from contextlib import ExitStack
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import api, auth, database as db
from test_api import TEST_PASSWORD, TEST_PASSWORD_HASH, client, document, run
from test_workflows import create_study, succeed


def login_as(browser, username, password=TEST_PASSWORD):
    response = browser.post('/api/auth/login', json={'username':username,'password':password})
    assert response.status_code == 200, response.text
    browser.headers['X-CSRF-Token'] = response.json()['csrf_token']
    browser.account = response.json()['account']
    return response


@pytest.fixture
def team(client):
    with db.session() as session:
        for username in ('001001','001002'):
            session.add(db.Account(username=username,display_name=username,password_hash=TEST_PASSWORD_HASH,is_admin=False,must_change_password=False))
        session.commit()
    with ExitStack() as stack:
        alice,bob,anonymous=[stack.enter_context(TestClient(api.app)) for _ in range(3)]
        login_as(alice,'001001');login_as(bob,'001002')
        yield client,alice,bob,anonymous


def owned(browser,name):
    response=browser.post('/api/circuits',json={'document':document(name=name),'employee_id':'spoofed'})
    assert response.status_code==201,response.text
    return response.json()


@pytest.mark.parametrize('method,path',[(next(iter(r.methods & {'GET','POST','PUT','DELETE'})),r.path)
    for r in api.app.routes if getattr(r,'path','').startswith('/api/')
    and r.path not in {'/api/health','/api/auth/status','/api/auth/login'}])
def test_all_data_routes_require_login(team,method,path):
    for key in ('circuit_id','run_id','study_id','benchmark_id','account_id'):
        path=path.replace('{'+key+'}','missing')
    response=team[3].request(method,path,json={})
    assert response.status_code==401,(path,response.text)
    assert response.headers['cache-control']=='no-store'


def test_public_status_health_and_cookie_rotation(team):
    admin,alice,bob,anonymous=team
    assert anonymous.get('/api/auth/status').json()=={'initialized':True}
    assert anonymous.get('/api/health').status_code==200
    initial=alice.cookies.get(auth.COOKIE)
    response=login_as(alice,'001001')
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/api' in cookie
    assert alice.cookies.get(auth.COOKIE)!=initial
    with db.session() as session:
        assert session.get(db.LoginSession,auth.token_hash(initial)) is None
        assert all(r.token_hash!=alice.cookies.get(auth.COOKIE) for r in session.scalars(select(db.LoginSession)))
    assert alice.post('/api/auth/logout').status_code==204
    assert alice.get('/api/circuits').status_code==401


def test_secure_cookie_configuration(client,monkeypatch):
    monkeypatch.setenv('AUTH_COOKIE_SECURE','true')
    with TestClient(api.app,base_url='https://testserver') as browser:
        response=login_as(browser,'000123')
        assert 'Secure' in response.headers['set-cookie']
        assert browser.get('/api/auth/me').status_code==200


@pytest.mark.parametrize('csrf',[None,'incorrect'])
def test_mutations_require_csrf_and_write_nothing(team,csrf):
    alice=team[1];alice.headers.pop('X-CSRF-Token')
    if csrf is not None:alice.headers['X-CSRF-Token']=csrf
    assert alice.post('/api/circuits',json={'document':document()}).status_code==403
    assert alice.post('/api/auth/logout').status_code==403
    assert alice.get('/api/circuits').json()['total_all']==0


def test_csrf_is_bound_to_own_session_and_login_origin_is_checked(team):
    _,alice,bob,anonymous=team
    alice.headers['X-CSRF-Token']=bob.headers['X-CSRF-Token']
    assert alice.post('/api/circuits',json={'document':document()}).status_code==403
    assert anonymous.post('/api/auth/login',headers={'Origin':'https://unrelated.example'},json={'username':'001001','password':TEST_PASSWORD}).status_code==403
    errors=[anonymous.post('/api/auth/login',json={'username':username,'password':'wrong'}).json() for username in ('001001','missing')]
    assert errors[0]==errors[1]
    secret='sensitive-input-'*20
    response=anonymous.post('/api/auth/login',json={'username':'001001','password':secret})
    assert response.status_code==422 and secret not in response.text


def test_login_throttle_survives_new_client_and_expires(team,monkeypatch):
    for _ in range(8):
        assert team[3].post('/api/auth/login',json={'username':'001001','password':'wrong'}).status_code==401
    with TestClient(api.app) as other:
        blocked=other.post('/api/auth/login',json={'username':'001001','password':TEST_PASSWORD})
        assert blocked.status_code==429 and int(blocked.headers['retry-after'])>0
        future=db.utcnow()+timedelta(minutes=16)
        monkeypatch.setattr(db,'utcnow',lambda:future)
        assert other.post('/api/auth/login',json={'username':'001001','password':TEST_PASSWORD}).status_code==200


def test_expired_or_disabled_sessions_cannot_read_data(team):
    _,alice,bob,_=team
    with db.session() as session:
        session.get(db.LoginSession,auth.token_hash(alice.cookies.get(auth.COOKIE))).expires_at=db.utcnow()-timedelta(seconds=1)
        session.get(db.Account,bob.account['id']).is_active=False
        session.commit()
    assert alice.get('/api/circuits').status_code==401
    assert bob.get('/api/auth/me').status_code==401


def test_list_search_counts_and_actor_identity_are_scoped(team):
    admin,alice,bob,_=team
    a,b=owned(alice,'モデルA'),owned(bob,'モデルB')
    assert a['created_by']==a['updated_by']=='001001' and a['owner_id']==alice.account['id']
    for params in ({},{'q':'モデル','limit':25},{'q':'モデルB'},{'updated_by':'001002'}):
        listing=alice.get('/api/circuits',params=params).json()
        assert listing['total_all']==1
        assert listing['total']==(0 if params.get('q')=='モデルB' or params.get('updated_by') else 1)
        assert all(r['id']==a['id'] for r in listing['circuits'])
    assert admin.get('/api/circuits').json()['total_all']==2
    updated=alice.put('/api/circuits/'+a['id'],json={'document':document(),'expected_revision':1,'employee_id':'001002'})
    assert updated.status_code==200 and updated.json()['updated_by']=='001001'


@pytest.mark.parametrize('action',['get','update','delete','run','study'])
def test_foreign_model_ids_and_mixed_batch_never_write(team,action):
    _,alice,bob,_=team
    a,b=owned(alice,'A'),owned(bob,'B');path='/api/circuits/'+b['id']
    if action=='get':response=alice.get(path)
    elif action=='update':response=alice.put(path,json={'document':document(),'expected_revision':1})
    elif action=='delete':response=alice.post('/api/circuits/delete',json={'circuits':[{'id':a['id'],'expected_revision':1},{'id':b['id'],'expected_revision':1}]})
    elif action=='run':response=alice.post('/api/runs',json={'circuit_id':b['id'],'expected_revision':1,'analysis':{'kind':'op'}})
    else:response=alice.post('/api/studies',json={'circuit_id':b['id'],'expected_revision':1,'analysis':{'kind':'ccp'},'axes':[{'path':'analysis.settings.pressure_pa','values':[1]}]})
    assert response.status_code==404,response.text
    assert alice.get('/api/circuits/'+a['id']).status_code==200
    assert bob.get(path).json()['revision']==1
    with db.session() as session:
        assert session.query(db.SimulationRun).count()==session.query(db.Study).count()==0


def test_every_history_export_comparison_and_study_action_is_scoped(team):
    admin,alice,bob,_=team
    a,b=owned(alice,'A'),owned(bob,'B')
    ra,rb=run(alice,a),run(bob,b);succeed(ra['id']);succeed(rb['id'])
    study=create_study(bob,b).json()
    for path in [f"/runs/{rb['id']}",f"/runs/{rb['id']}/export.csv",f"/runs/{rb['id']}/package",f"/runs?circuit_id={b['id']}",
                 f"/studies/{study['id']}",f"/studies/{study['id']}/export.csv",f"/studies?circuit_id={b['id']}"]:
        assert alice.get('/api'+path).status_code==404,path
        assert admin.get('/api'+path).status_code==200,path
    assert alice.post(f"/api/runs/{rb['id']}/cancel").status_code==404
    for command in ('cancel','resume'):
        assert alice.post(f"/api/studies/{study['id']}/{command}",json={}).status_code==404
    assert alice.post('/api/compare',json={'run_ids':[ra['id'],rb['id']]}).status_code==404
    assert {r['id'] for r in alice.get('/api/runs').json()['runs']}=={ra['id']}
    assert alice.get('/api/studies').json()['studies']==[]
    assert alice.post('/api/circuits/delete',json={'circuits':[{'id':a['id'],'expected_revision':1}]}).status_code==200
    assert alice.get(f"/api/runs/{ra['id']}").status_code==200
    assert bob.get(f"/api/runs/{ra['id']}").status_code==404


def test_benchmark_and_imported_package_ownership(team):
    admin,alice,bob,_=team
    a,b=owned(alice,'A'),owned(bob,'B')
    ra,rb=run(alice,a),run(bob,b);succeed(ra['id']);succeed(rb['id'])
    payload={'name':'Private','material':'Ar','measurement_definition':'density','frequency_hz':4e7,'pressure_pa':1,'provenance':'test',
             'data':[{'metric':'electron_density_m3','value':2e16,'unit':'m-3'}]}
    ref=bob.post('/api/benchmarks',json=payload).json()
    assert alice.get('/api/benchmarks').json()['benchmarks']==[]
    assert alice.get('/api/benchmarks/'+ref['id']).status_code==404
    assert alice.post('/api/benchmarks/'+ref['id']+'/compare',json={'run_id':ra['id']}).status_code==404
    assert bob.post('/api/benchmarks/'+ref['id']+'/compare',json={'run_id':ra['id']}).status_code==404
    assert admin.get('/api/benchmarks/'+ref['id']).status_code==200
    package=alice.get(f"/api/runs/{ra['id']}/package").json()
    imported=alice.post('/api/packages/import',json={'package':package,'employee_id':'001002'})
    assert imported.status_code==201,imported.text
    circuit=imported.json()['circuit'];assert circuit['owner_id']==alice.account['id'] and circuit['created_by']=='001001'
    assert bob.get('/api/circuits/'+circuit['id']).status_code==404


def test_legacy_requires_explicit_assignment_and_transfer_moves_history(team):
    admin,alice,bob,_=team
    with db.session() as session:
        legacy=db.Circuit(name='Legacy',document=document(),created_by='001001',updated_by='001001')
        session.add(legacy);session.commit();cid=legacy.id
    assert alice.get('/api/circuits').json()['total_all']==0
    assert admin.get('/api/circuits/'+cid).status_code==200
    def assign(owner,rev):
        return admin.post('/api/admin/circuits/owner',json={'owner_id':owner,'circuits':[{'id':cid,'expected_revision':rev}]})
    assert assign(alice.account['id'],1).status_code==200
    model=alice.get('/api/circuits/'+cid).json();r=run(alice,model);succeed(r['id'])
    snapshot=alice.get('/api/runs/'+r['id']).json()['snapshot']
    assert assign(bob.account['id'],2).status_code==200
    assert alice.get('/api/circuits/'+cid).status_code==404 and alice.get('/api/runs/'+r['id']).status_code==404
    assert bob.get('/api/runs/'+r['id']).json()['snapshot']==snapshot
    assert bob.get('/api/circuits/'+cid).json()['revision']==3
    assert assign(None,3).status_code==200
    assert bob.get('/api/runs/'+r['id']).status_code==404


def test_assignment_is_atomic_and_cannot_target_disabled_accounts(team):
    admin,alice,bob,_=team
    a,b=owned(alice,'A'),owned(alice,'B')
    payload={'owner_id':bob.account['id'],'circuits':[{'id':a['id'],'expected_revision':1},{'id':b['id'],'expected_revision':2}]}
    assert admin.post('/api/admin/circuits/owner',json=payload).status_code==409
    assert alice.get('/api/circuits/'+a['id']).json()['revision']==1
    with db.session() as session:
        session.get(db.Account,bob.account['id']).is_active=False;session.commit()
    payload['circuits']=payload['circuits'][:1]
    assert admin.post('/api/admin/circuits/owner',json=payload).status_code==422
    assert alice.post('/api/admin/circuits/owner',json=payload).status_code==403


def test_issuance_force_change_reset_and_deletion_revoke_sessions(team):
    admin,alice,_,_=team
    created=admin.post('/api/admin/accounts',json={'username':'007007','display_name':'New'})
    assert created.status_code==201,created.text
    issued=created.json();target=issued['account'];temporary=issued['temporary_password']
    assert target['must_change_password'] and not target['is_admin']
    with TestClient(api.app) as browser:
        login_as(browser,'007007',temporary)
        assert browser.get('/api/auth/me').status_code==200 and browser.get('/api/circuits').status_code==403
        changed=browser.post('/api/auth/password',json={'current_password':temporary,'new_password':TEST_PASSWORD})
        assert changed.status_code==200 and not changed.json()['account']['must_change_password']
        browser.headers['X-CSRF-Token']=changed.json()['csrf_token']
        model=owned(browser,'New model')
        reset=admin.post('/api/admin/accounts/'+target['id']+'/password')
        assert reset.status_code==200 and browser.get('/api/circuits').status_code==401
        assert browser.post('/api/auth/login',json={'username':'007007','password':TEST_PASSWORD}).status_code==401
        login_as(browser,'007007',reset.json()['temporary_password'])
        assert admin.delete('/api/admin/accounts/'+target['id']).status_code==204
        assert browser.get('/api/auth/me').status_code==401
        assert browser.post('/api/auth/login',json={'username':'007007','password':reset.json()['temporary_password']}).status_code==401
        assert admin.get('/api/circuits/'+model['id']).status_code==200
        assert alice.get('/api/circuits/'+model['id']).status_code==404
    assert target['id'] not in [a['id'] for a in admin.get('/api/admin/accounts').json()['accounts']]
    assert target['id'] in [a['id'] for a in admin.get('/api/admin/accounts?include_deleted=true').json()['accounts']]
    assert admin.post('/api/admin/accounts',json={'username':'007007','display_name':'Reissue'}).status_code==409
    with db.session() as session:
        stored=session.get(db.Account,target['id'])
        assert temporary not in stored.password_hash and auth.verify_password(reset.json()['temporary_password'],stored.password_hash)
        assert session.query(db.LoginSession).filter_by(account_id=target['id']).count()==0
        actions={e.action for e in session.scalars(select(db.AccountEvent).where(db.AccountEvent.target_id==target['id']))}
        assert actions>={'created','password_changed','password_reset','deleted'}


def test_admin_only_management_and_self_protection(team):
    admin,alice,bob,_=team
    for method,path in [('GET','/api/admin/accounts'),('POST','/api/admin/accounts'),('PUT','/api/admin/accounts/'+bob.account['id']),('DELETE','/api/admin/accounts/'+bob.account['id'])]:
        assert alice.request(method,path,json={}).status_code==403
    for fields in ({'is_admin':False,'is_active':True},{'is_admin':True,'is_active':False}):
        assert admin.put('/api/admin/accounts/'+admin.account['id'],json={'display_name':'Self',**fields}).status_code==409
    assert admin.delete('/api/admin/accounts/'+admin.account['id']).status_code==409
    assert admin.post('/api/admin/accounts/'+admin.account['id']+'/password').status_code==409
    def update(role,active):
        return admin.put('/api/admin/accounts/'+bob.account['id'],json={'display_name':'Bob','is_admin':role,'is_active':active})
    assert update(True,True).status_code==200 and bob.get('/api/admin/accounts').status_code==401
    login_as(bob,'001002');assert bob.get('/api/admin/accounts').status_code==200
    assert update(False,False).status_code==200 and bob.get('/api/circuits').status_code==401
    assert bob.post('/api/auth/login',json={'username':'001002','password':TEST_PASSWORD}).status_code==401
    assert update(False,True).status_code==200
    login_as(bob,'001002');assert bob.get('/api/circuits').status_code==200


def test_password_change_invalidates_other_sessions_and_checks_current_password(team):
    alice=team[1]
    with TestClient(api.app) as other:
        login_as(other,'001001')
        assert alice.post('/api/auth/password',json={'current_password':'wrong','new_password':'new-test-password'}).status_code==400
        assert alice.post('/api/auth/password',json={'current_password':TEST_PASSWORD,'new_password':'short'}).status_code==422
        response=alice.post('/api/auth/password',json={'current_password':TEST_PASSWORD,'new_password':'new-test-password'})
        assert response.status_code==200
        alice.headers['X-CSRF-Token']=response.json()['csrf_token']
        assert alice.get('/api/auth/me').status_code==200 and other.get('/api/auth/me').status_code==401


@pytest.mark.parametrize('mutation',[{'username':'x'*81},{'username':'id\nnewline'},{'display_name':'  '},{'is_admin':'true'},{'password':'secret'}])
def test_invalid_account_input_is_rejected_without_writes(team,mutation):
    admin=team[0];before=admin.get('/api/admin/accounts').json()['total']
    response=admin.post('/api/admin/accounts',json={'username':'new-user','display_name':'New',**mutation})
    assert response.status_code==422,response.text
    assert admin.get('/api/admin/accounts').json()['total']==before
