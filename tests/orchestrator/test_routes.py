import pytest
from .test_store import setup
from backend.orchestrator.app import create_app
from backend.orchestrator.runtime import Service


@pytest.fixture
def api(setup):
    store,shared,sid=setup
    service=Service(store,lambda rid: None,{'fixture':'a'*40})
    app=create_app(service,{'synthetic-token':'owner'})
    return app.test_client(),service,sid


def test_auth_origin_and_owner(api):
    client,service,sid=api
    assert client.get('/api/orchestrator/runs/missing').status_code==401
    headers={'Authorization':'Bearer synthetic-token','Origin':'https://evil.invalid'}
    assert client.post('/api/orchestrator/runs',headers=headers,json={}).status_code==403
    assert client.get('/api/orchestrator/runs/missing',headers={'Authorization':'Bearer synthetic-token'}).status_code==404


def test_create_inspect_controls_no_private_state(api):
    client,service,sid=api
    h={'Authorization':'Bearer synthetic-token'}
    payload=dict(session_id=sid,goal='Inspect',default_agent='codex',source_id='fixture',source_commit='a'*40,idempotency_key='start')
    response=client.post('/api/orchestrator/runs',headers=h,json=payload)
    assert response.status_code==201
    run=response.json['run']; rid=run['run_id']
    assert not run['writer_enabled'] and 'scope_id' not in run
    assert client.post('/api/orchestrator/runs',headers=h,json=payload).json['run']['run_id']==rid
    view=client.get('/api/orchestrator/runs/'+rid,headers=h).json
    assert view['transcript'][0]['speaker_name']=='user'
    response=client.post('/api/orchestrator/runs/'+rid+'/stop',headers=h,json={'expected_version':run['version'],'idempotency_key':'stop'})
    assert response.status_code==200 and response.json['run']['state']=='STOPPED'
    service.close()


def test_extra_owner_and_unknown_source_rejected(api):
    client,service,sid=api
    p=dict(session_id=sid,goal='Inspect',default_agent='codex',source_id='fixture',source_commit='a'*40,idempotency_key='start',owner='attacker')
    assert client.post('/api/orchestrator/runs',headers={'Authorization':'Bearer synthetic-token'},json=p).status_code==400
