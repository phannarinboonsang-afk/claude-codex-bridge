#!/usr/bin/env python3
"""Local owner tool. Credentials are prompted without echo, never written."""
import argparse
import base64
import datetime
import getpass
import gzip
import hashlib
import ipaddress
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

BUNDLE=pathlib.Path(__file__).resolve().parent
BASE='https://api.digitalocean.com/v2/'
NAME='agentbridge-writer-v1'
PACKAGE_VERSION='1.0.2'

def verify_bundle():
    listed=set()
    for line in (BUNDLE/'SHA256SUMS').read_text().splitlines():
        sha,name=line.split('  ',1);p=BUNDLE/name
        if pathlib.PurePosixPath(name).is_absolute() or '..' in pathlib.PurePosixPath(name).parts or p.is_symlink():raise ValueError('unsafe_manifest_path')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('bundle_hash_mismatch:'+name)
        listed.add(name)
    actual={p.relative_to(BUNDLE).as_posix() for p in BUNDLE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts and p.name not in {'SHA256SUMS','release-validation-manifest.json'}}
    if listed!=actual:raise ValueError('unreviewed_bundle_files')

def verify_release_validation():
    path=BUNDLE/'release-validation-manifest.json'
    if not path.is_file():raise ValueError('release_validation_manifest_missing')
    try:m=json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError):raise ValueError('release_validation_manifest_invalid') from None
    fingerprint=hashlib.sha256((BUNDLE/'SHA256SUMS').read_bytes()).hexdigest()
    expected={'package_version','package_sha256s_fingerprint','git_commit','runner_os','systemd_version','cloud_init_version','validation_counts','timestamp_utc','verdict','github_workflow_run_id','shellcheck_version'}
    if not isinstance(m,dict) or set(m)!=expected:raise ValueError('release_validation_manifest_invalid')
    if m.get('verdict')!='PASS' or m.get('package_version')!=PACKAGE_VERSION or m.get('package_sha256s_fingerprint')!=fingerprint or m.get('runner_os')!='ubuntu-24.04':
        raise ValueError('release_validation_manifest_mismatch')
    if not isinstance(m.get('git_commit'),str) or not __import__('re').fullmatch(r'[0-9a-f]{40}',m['git_commit']):raise ValueError('release_validation_manifest_invalid')
    if type(m['github_workflow_run_id']) is not int or m['github_workflow_run_id']<=0 or not isinstance(m['shellcheck_version'],str) or not m['shellcheck_version']:raise ValueError('release_validation_manifest_invalid')
    if not isinstance(m.get('systemd_version'),str) or not m['systemd_version'] or not isinstance(m.get('cloud_init_version'),str) or not m['cloud_init_version']:raise ValueError('release_validation_manifest_invalid')
    counts=m.get('validation_counts')
    required={'python_unit_tests','systemd_units','cloud_init_documents','yaml_documents','shell_files','provisioning_safety_cases'}
    if not isinstance(counts,dict) or set(counts)!=required or any(type(v) is not int or v<0 for v in counts.values()):raise ValueError('release_validation_manifest_invalid')
    if counts['python_unit_tests']<1 or counts['systemd_units']<1 or counts['cloud_init_documents']<1 or counts['provisioning_safety_cases']<4:raise ValueError('release_validation_manifest_invalid')
    if not isinstance(m.get('timestamp_utc'),str):raise ValueError('release_validation_manifest_invalid')
    try:timestamp=datetime.datetime.fromisoformat(m['timestamp_utc'].replace('Z','+00:00'))
    except (TypeError,ValueError):raise ValueError('release_validation_manifest_invalid') from None
    if timestamp.tzinfo is None or not m['timestamp_utc'].endswith('Z'):raise ValueError('release_validation_manifest_invalid')
    return m

def validate_owner(c):
    if set(c)!={'ssh_key','admin_cidr'}:raise ValueError('owner_config_fields')
    key=c['ssh_key']
    if not isinstance(key,(str,int)) or not (str(key).isdigit() or __import__('re').fullmatch(r'(?:[a-f0-9]{2}:){15}[a-f0-9]{2}',str(key))):raise ValueError('registered_ssh_key_id_or_fingerprint_required')
    net=ipaddress.ip_network(c['admin_cidr'],strict=True)
    if net.version!=4 or net.prefixlen!=32 or not net.network_address.is_global:
        # Documentation-range IP is permitted only in offline tests/examples.
        if not (net.version==4 and net.prefixlen==32 and str(net.network_address).startswith('203.0.113.')):raise ValueError('single_admin_public_ipv4_required')
    return c

def resource_spec(owner,tag):
    validate_owner(owner)
    return dict(name=NAME,region='sgp1',size='s-4vcpu-8gb',image='ubuntu-24-04-x64',
        ssh_keys=[int(owner['ssh_key']) if str(owner['ssh_key']).isdigit() else owner['ssh_key']],
        ipv6=False,backups=False,monitoring=False,tags=['agentbridge','writer','v1',tag])

def firewall_spec(owner,tag):
    return dict(name=NAME+'-'+tag,tags=[tag],inbound_rules=[dict(protocol='tcp',ports='22',sources={'addresses':[owner['admin_cidr']]})],
        outbound_rules=[dict(protocol='tcp',ports='80',destinations={'addresses':['0.0.0.0/0']}),
                        dict(protocol='tcp',ports='443',destinations={'addresses':['0.0.0.0/0']}),
                        dict(protocol='udp',ports='53',destinations={'addresses':['0.0.0.0/0']})])

def destruction_ids(m,d,f):
    tag=m['deployment_tag']
    if d is not None:
        if d.get('id')!=m.get('droplet_id') or d.get('name')!=NAME or tag not in d.get('tags',[]) or d.get('size_slug')!='s-4vcpu-8gb' or d.get('region',{}).get('slug')!='sgp1':raise ValueError('destroy_droplet_scope_mismatch')
    if f is not None:
        if f.get('id')!=m.get('firewall_id') or f.get('name')!=NAME+'-'+tag or f.get('tags')!=[tag]:raise ValueError('destroy_firewall_scope_mismatch')
    return (m.get('droplet_id') if d else None,m.get('firewall_id') if f else None)

def cloud_init():
    files=[]
    mapping={'worker.py':'/opt/agentbridge-worker/worker.py','runner.py':'/opt/agentbridge-worker/runner.py',
        'acceptance.py':'/opt/agentbridge-worker/acceptance.py','guest/bootstrap.py':'/opt/agentbridge-worker/bootstrap.py',
        'config/policy.json':'/etc/agentbridge-worker/policy.json','units/agentbridge-worker.service':'/etc/systemd/system/agentbridge-worker.service',
        'config/egress.nft':'/etc/agentbridge-worker/egress.nft',
        'guest/toolchain.py':'/opt/agentbridge-worker/toolchain.py',
        'config/toolchain.json':'/etc/agentbridge-worker/toolchain.json',
        'proxy.py':'/opt/agentbridge-worker/proxy.py',
        'config/egress-policy.json':'/etc/agentbridge-worker/egress-policy.json',
        'units/agentbridge-egress.service':'/etc/systemd/system/agentbridge-egress.service',
        'guest/egress_policy.py':'/opt/agentbridge-worker/egress_policy.py',
        'units/agentbridge-egress-policy.service':'/etc/systemd/system/agentbridge-egress-policy.service'}
    mapping.update({'guest/configure-tunnel.py':'/opt/agentbridge-worker/configure-tunnel.py',
                    'export-evidence.py':'/opt/agentbridge-worker/export-evidence.py'})
    for src,dst in mapping.items():
        private=src=='config/policy.json'
        files.append(dict(path=dst,owner='root:root',permissions='0600' if private else '0644',encoding='gz+b64',content=base64.b64encode(gzip.compress((BUNDLE/src).read_bytes(),mtime=0)).decode()))
    text='#cloud-config\n'+json.dumps(dict(ssh_pwauth=False,disable_root=False,write_files=files,
        runcmd=[['/usr/bin/python3','/opt/agentbridge-worker/bootstrap.py']]),indent=2)
    if len(text.encode())>65536:raise ValueError('cloud_init_size_limit')
    return text

class API:
    def __init__(self,token):self.token=token
    def call(self,method,path,data=None,missing=False):
        if not path or path.startswith('/') or '..' in path:raise ValueError('invalid_api_path')
        req=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),method=method,
             headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=45) as response:
                raw=response.read(2097152);return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if missing and e.code==404:return None
            raise ValueError('digitalocean_http_'+str(e.code)) from None
        except urllib.error.URLError:raise ValueError('digitalocean_network_error_reconcile_before_retry') from None

def save(path,data):
    temp=path.with_suffix('.tmp')
    with open(temp,'w',encoding='utf8') as f:json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    if os.name=='posix':
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)

def create(api,owner,state):
    if state.exists():raise ValueError('state_exists_reconcile_do_not_repeat_create')
    if ipaddress.ip_address(owner['admin_cidr'].split('/')[0]).is_global is False:raise ValueError('example_address_not_deployable')
    # Preflight public catalog and registered key before mutations.
    sizes=api.call('GET','sizes?per_page=200')['sizes']
    if not any(x['slug']=='s-4vcpu-8gb' and x.get('available') and 'sgp1' in x.get('regions',[]) and x['price_monthly']<=48 and x['price_hourly']<=0.07143 for x in sizes):raise ValueError('approved_size_region_budget_unavailable')
    image=api.call('GET','images/ubuntu-24-04-x64')['image']
    if image.get('distribution')!='Ubuntu' or not image.get('public'):raise ValueError('image_mismatch')
    api.call('GET','account/keys/'+str(owner['ssh_key']))
    tag='abwriter-'+secrets.token_hex(8)
    m=dict(deployment_tag=tag,droplet_id=None,firewall_id=None,stage='INITIAL',created_at=time.time());save(state,m)
    # Tag-specific firewall exists before the Droplet so API ports are never public.
    api.call('POST','tags',{'name':tag})
    m['stage']='FIREWALL_CREATE_INTENT';save(state,m)
    fw=api.call('POST','firewalls',firewall_spec(owner,tag))['firewall'];m['firewall_id']=fw['id'];m['stage']='FIREWALL_CREATED';save(state,m)
    r=resource_spec(owner,tag);r['user_data']=cloud_init()
    m['stage']='DROPLET_CREATE_INTENT';save(state,m)
    d=api.call('POST','droplets',r)['droplet'];m['droplet_id']=d['id'];m['stage']='DROPLET_CREATED_WRITERS_DISABLED';save(state,m)
    print('DROPLET_REQUESTED_WRITERS_DISABLED; track resource IDs in local state; inspect cloud-init completion before acceptance')

def destroy(api,state,confirmed):
    m=json.loads(state.read_text());tag=m['deployment_tag']
    if confirmed!=tag:raise ValueError('confirm_exact_deployment_tag_required')
    # Ambiguous POST recovery must be reconciled by owner; no blind deletion/search by generic tag.
    if m['stage'] in {'DROPLET_CREATE_INTENT','FIREWALL_CREATE_INTENT'}:raise ValueError('ambiguous_creation_requires_reconciliation')
    d=api.call('GET','droplets/'+str(m['droplet_id']),missing=True) if m.get('droplet_id') else None
    f=api.call('GET','firewalls/'+m['firewall_id'],missing=True) if m.get('firewall_id') else None
    did,fid=destruction_ids(m,None if d is None else d['droplet'],None if f is None else f['firewall'])
    if did:api.call('DELETE','droplets/'+str(did))
    m['stage']='DROPLET_DELETED';save(state,m)
    if did and api.call('GET','droplets/'+str(did),missing=True) is not None:raise ValueError('deletion_pending_verify_billing_stop')
    if fid:api.call('DELETE','firewalls/'+fid)
    m['stage']='DESTROYED';save(state,m)
    print('WORKER_RESOURCES_DELETED; generic/shared tags and owner SSH keys retained')

def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['plan','create','destroy']);p.add_argument('--owner-config',default='owner.json');p.add_argument('--state',default='worker-resources.json');p.add_argument('--confirm-deployment-tag');p.add_argument('--approve-create',action='store_true');a=p.parse_args()
    verify_bundle()
    if a.operation=='plan':
        owner=validate_owner(json.loads(pathlib.Path(a.owner_config).read_text()));print(json.dumps({'droplet':resource_spec(owner,'UNASSIGNED'),'firewall':firewall_spec(owner,'UNASSIGNED'),'writers_enabled':False},indent=2));return
    if a.operation=='create':
        # An exact package fingerprint from the dedicated Linux workflow is required.
        verify_release_validation()
        if not a.approve_create:raise ValueError('explicit_approve_create_required')
        owner=validate_owner(json.loads(pathlib.Path(a.owner_config).read_text()))
        if os.name!='posix':raise ValueError('create_requires_posix_durable_state_filesystem')
    token=getpass.getpass('DigitalOcean scoped token (local only, not saved): ')
    if not token:raise ValueError('token_required')
    api=API(token)
    if a.operation=='create':
        state=pathlib.Path(a.state).resolve();lock=state.with_suffix('.create-lock')
        fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:create(api,owner,state)
        finally:os.close(fd);lock.unlink()
    else:destroy(api,pathlib.Path(a.state).resolve(),a.confirm_deployment_tag)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError) as e:
        # Never print exception bodies from network requests or cloud-init.
        reason=str(e) if isinstance(e,ValueError) else 'local_configuration_or_state_error'
        print('PROVISION_ERROR:'+reason,file=sys.stderr);sys.exit(1)
