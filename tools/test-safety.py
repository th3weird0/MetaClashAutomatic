"""Exercise real-core saved choices, failed proxies and empty Netflix pools."""
import contextlib
import copy
import hashlib
import http.client
import http.server
import json
import os
import pathlib
import socket
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import yaml
from policy import FISH,MANUAL,AI,NF,NF_AUTO,load,migrate_state

ROOT=pathlib.Path(sys.argv[1])
CORE=pathlib.Path(sys.argv[2])
WORK=ROOT/'validation';HOME=WORK/('safety-home-'+uuid.uuid4().hex[:8])

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

class Canary(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.server.hits+=1
        self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'ok')

def request(mixed,url):
    c=http.client.HTTPConnection('127.0.0.1',mixed,timeout=4)
    try:c.request('GET',url);r=c.getresponse();return r.status,r.read()
    except (OSError,http.client.HTTPException):return 0,b''
    finally:c.close()

@contextlib.contextmanager
def running(config,label):
    d=copy.deepcopy(config);ctl=port();mixed=port()
    d['external-controller']='127.0.0.1:'+str(ctl);d['mixed-port']=mixed
    d['bind-address']='127.0.0.1';d['allow-lan']=False
    for p in d.get('proxy-providers',{}).values():p['health-check']={'enable':False}
    for g in d['proxy-groups']:
        if g['type']=='url-test':g['url']='http://delay.invalid/health';g['interval']=3600
    path=WORK/(label+'-'+uuid.uuid4().hex[:8]+'.yaml');path.write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(method,path,payload=None):
        body=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request('http://127.0.0.1:'+str(ctl)+path,data=body,method=method,headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=8) as r:
            raw=r.read();return json.loads(raw) if raw else None
    proc=None
    try:
        with path.with_suffix('.log').open('wb') as log:
            proc=subprocess.Popen([str(CORE),'-d',str(HOME),'-f',str(path)],stdout=log,stderr=subprocess.STDOUT)
            actual=(pathlib.Path('/proc')/str(proc.pid)/'cmdline').read_bytes().split(b'\0')
            assert str(path).encode() in actual and str(CORE).encode() in actual
            assert '\nPPid:\t'+str(os.getpid())+'\n' in (pathlib.Path('/proc')/str(proc.pid)/'status').read_text()
            for _ in range(100):
                if proc.poll() is not None:raise AssertionError({'phase':label,'exit_code':proc.returncode,'log':path.with_suffix('.log').read_text(errors='replace')[-3000:]})
                try:
                    state=api('GET','/proxies')['proxies']
                    if NF_AUTO in state and len(api('GET','/providers/rules').get('providers') or {})==len(d['rule-providers']):
                        time.sleep(.5);break
                except OSError:pass
                time.sleep(.1)
            else:raise AssertionError('Isolated core not ready')
            yield api,mixed,'http://127.0.0.1:'+str(ctl)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)

def main():
    HOME.mkdir()
    for name in ['GeoSite.dat','Country.mmdb','ASN.mmdb','synthetic-nodes.yaml','zero-nodes.yaml']:
        shutil.copy2(WORK/'runtime-data'/name,HOME/name)
    shutil.copytree(WORK/'runtime-data'/'rule-copies',HOME/'rule-copies')
    report={};config=load(WORK/'converted-native-test.yaml')
    zero=load(WORK/'converted-zero-native.yaml')
    # Use the actual converter's zero-eligible result. The destination must survive.
    sentinel=WORK/'active-config-sentinel.yaml';sentinel.write_bytes(b'active-config: preserve\n')
    before=sentinel.read_bytes()
    result=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('policy.py')),'prepare',str(WORK/'converted-zero-native.yaml'),'--data-dir',str(HOME),'--output',str(sentinel)],capture_output=True)
    assert result.returncode!=0 and b'Netflix has zero eligible nodes' in result.stderr
    assert sentinel.read_bytes()==before
    report['actual_converter_zero_candidates_refused_without_replacement']=True
    stored=copy.deepcopy(config);stored['profile']={'store-selected':True,'store-fake-ip':False}
    encoded=urllib.parse.quote(FISH,safe='')
    with running(stored,'save-old-direct') as (api,mixed,base):
        assert api('GET','/proxies')[ 'proxies'][FISH]['now']==MANUAL
        api('PUT','/proxies/'+encoded,{'name':'🎯 全球直连'})
        assert api('GET','/proxies')['proxies'][FISH]['now']=='🎯 全球直连'
    with running(stored,'inspect-old-choice') as (api,mixed,base):
        assert api('GET','/proxies')['proxies'][FISH]['now']=='🎯 全球直连'
        status=migrate_state(base,apply=False)
        assert status['migration_required']
        migrated=migrate_state(base,apply=True)
        assert migrated['catchall_is_manual_proxy']
        report['persisted_old_direct_detected_and_migrated']=True
        api('PUT','/proxies/'+encoded,{'name':'🎯 全球直连'})
    first=copy.deepcopy(config);first['profile']={'store-selected':False,'store-fake-ip':False}
    with running(first,'first-activation') as (api,mixed,base):
        assert api('GET','/proxies')['proxies'][FISH]['now']==MANUAL
        report['first_activation_ignores_old_saved_direct']=True
        # Deliberately kill all synthetic candidates: every configured port is closed.
        for provider in first['proxy-providers']:
            api('GET','/providers/proxies/'+urllib.parse.quote(provider,safe='')+'/healthcheck')
        status,_=request(mixed,'http://zz-meta-fullauto-probe-20261114.com/failure')
        state=api('GET','/proxies')['proxies']
        assert status!=200 and state[FISH]['now']==MANUAL
        assert state[MANUAL]['now']!='DIRECT'
        report['failed_proxy_does_not_select_direct']=True
    canary=http.server.ThreadingHTTPServer(('127.0.0.1',0),Canary);canary.hits=0
    thread=threading.Thread(target=canary.serve_forever,daemon=True);thread.start()
    zero['hosts']={'netflix.com':'127.0.0.1'}
    # Route this single loopback canary explicitly into Netflix. Without this
    # test-only rule, hosts resolution correctly triggers the private IP rules.
    # Production rule ordering is checked separately by the routing suite.
    zero['rules']=['DOMAIN,netflix.com,'+NF]+zero['rules']
    try:
        with running(zero,'runtime-empty-netflix') as (api,mixed,base):
            state=api('GET','/proxies')['proxies']
            assert set(state[NF_AUTO]['all'])=={'REJECT'},state[NF_AUTO]
            assert state[NF_AUTO]['now']=='REJECT' and state[NF]['now']==NF_AUTO
            status,_=request(mixed,'http://localhost:'+str(canary.server_port)+'/positive-control')
            assert status==200 and canary.hits==1
            status,_=request(mixed,'http://netflix.com:'+str(canary.server_port)+'/must-not-go-direct')
            assert status!=200 and canary.hits==1
            report['runtime_empty_netflix_rejects']=True
            report['empty_netflix_did_not_reach_direct_canary']=True
            report['canary_uses_explicit_test_route_to_netflix']=True
    finally:canary.shutdown();canary.server_close();thread.join(timeout=2)
    report['passed']=True
    report['template_sha256']=hashlib.sha256((pathlib.Path(__file__).resolve().parents[1]/'MetaFullauto_Full.ini').read_bytes()).hexdigest()
    report['production_changes']=False
    (WORK/'safety-tests.safe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
