"""Run only against an isolated local core with synthetic HTTP proxy fixtures.

Rule bytes, order and filters are unchanged. For routing observation only,
selectors receive a local stub and DIRECT is replaced by that stub; therefore
the probes test rule classification without accessing the named Internet hosts.
Health-test intervals/URLs are accelerated only in the disposable test YAML.
"""
import concurrent.futures
import copy
import http.server
import json
import os
import pathlib
import socket
import sys
import http.client
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import yaml

ROOT=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else pathlib.Path(__file__).resolve().parents[1]/'.local'
WORK=ROOT/'validation'
HOME=WORK/'runtime-data'
CORE=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'tools'/'clash_meta'
LOCK=threading.Lock()
HOLDS={}
BACKENDS=[]

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

class Proxy(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_HEAD(self):
        self.do_GET()
    def do_CONNECT(self):
        if not self.server.alive:
            self.close_connection=True
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            return
        self.send_response(200,'Connection Established');self.end_headers()
        self.close_connection=False
        self.handle_one_request()
        self.close_connection=True
    def do_GET(self):
        if not self.server.alive:
            self.close_connection=True
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            return
        parsed=urllib.parse.urlsplit(self.path)
        if parsed.path.startswith('/__mf_probe/'):
            token=parsed.path.rsplit('/',1)[1]
            with LOCK:hold=HOLDS.get(token)
            if hold:
                hold[0].set();hold[1].wait(timeout=8)
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'ok')
        else:
            time.sleep(self.server.delay)
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()

def start_backend(delay):
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Proxy);server.daemon_threads=True
    server.alive=True;server.delay=delay
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    BACKENDS.append((server,thread));return server

def main():
    backend_a=start_backend(.02);backend_b=start_backend(.18)
    original=yaml.safe_load((WORK/'converted-native-test.yaml').read_text())
    data=copy.deepcopy(original)
    nodes=yaml.safe_load((HOME/'synthetic-nodes.yaml').read_text())
    for index,node in enumerate(nodes['proxies']):node['port']=backend_a.server_port if index%2==0 else backend_b.server_port
    fixture=HOME/'behavior-nodes.yaml';fixture.write_text(yaml.safe_dump(nodes,allow_unicode=True,sort_keys=False))
    data['proxies']=[{'name':'LOCAL_TEST_PROXY','type':'http','server':'127.0.0.1','port':backend_a.server_port}]
    for provider in data['proxy-providers'].values():
        provider['path']='./behavior-nodes.yaml'
        provider['health-check']={'enable':True,'url':'http://delay.invalid/health','interval':3600,'lazy':False}
    for group in data['proxy-groups']:
        if group['type']=='select' and group['name']!='🎥 Netflix':group['proxies']=['LOCAL_TEST_PROXY']+group.get('proxies',[])
        if group['type']=='url-test':group.update({'url':'http://delay.invalid/health','interval':3600,'tolerance':0,'lazy':False})
    ctl=free_port();mixed=free_port()
    data['external-controller']='127.0.0.1:'+str(ctl)
    data['mixed-port']=mixed;data['allow-lan']=False;data['bind-address']='127.0.0.1'
    data['log-level']='info';data['profile']={'store-selected':False,'store-fake-ip':False}
    path=WORK/('behavior-'+uuid.uuid4().hex[:8]+'.yaml');path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
    logpath=WORK/(path.stem+'.log')
    direct=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(method,path,payload=None,timeout=12):
        body=json.dumps(payload).encode() if payload is not None else None
        request=urllib.request.Request('http://127.0.0.1:'+str(ctl)+path,data=body,method=method,headers={'Content-Type':'application/json'})
        with direct.open(request,timeout=timeout) as r:
            raw=r.read();return json.loads(raw) if raw else None
    proc=None
    report={'scope':'local synthetic routing; no production changes','routing':[]}
    try:
        with logpath.open('wb') as log:
            proc=subprocess.Popen([str(CORE),'-d',str(HOME),'-f',str(path)],stdout=log,stderr=subprocess.STDOUT)
            actual=(pathlib.Path('/proc')/str(proc.pid)/'cmdline').read_bytes().split(b'\0')
            assert str(path).encode() in actual and str(CORE).encode() in actual
            status=(pathlib.Path('/proc')/str(proc.pid)/'status').read_text()
            assert '\nPPid:\t'+str(os.getpid())+'\n' in status
            report['identity']={'pid':proc.pid,'ppid':os.getpid(),'config':path.name}
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError(logpath.read_text()[-3000:])
                try:
                    state=api('GET','/proxies')
                    rule_state=api('GET','/providers/rules').get('providers') or {}
                    rule_ready=len(rule_state)==len(data['rule-providers']) and all(p.get('ruleCount',0)>0 for p in rule_state.values())
                    group_ready=all(state['proxies'].get(g['name'],{}).get('all') and 'COMPATIBLE' not in state['proxies'][g['name']]['all'] for g in data['proxy-groups'] if g['type']=='url-test')
                    if rule_ready and group_ready:
                        probe=http.client.HTTPConnection('127.0.0.1',mixed,timeout=1)
                        try:
                            probe.request('GET','http://localhost:'+str(backend_a.server_port)+'/ready')
                            response=probe.getresponse();response.read()
                            if response.status==204:break
                        finally:probe.close()
                except (OSError,ValueError):pass
                time.sleep(.1)
            else:raise RuntimeError('Native startup timed out')
            print(json.dumps({'phase':'native-running','pid':proc.pid,'rule_providers':len(data['rule-providers'])},ensure_ascii=False),flush=True)
            groups=state['proxies']
            manifest=json.loads((ROOT/'template-manifest.json').read_text())
            import re
            source_names={n['name'] for n in nodes['proxies']}
            coverage={}
            for name,pattern in manifest['region_patterns'].items():
                expected={n for n in source_names if re.search(pattern,n)}
                actual=set(groups[name]['all'])-{'REJECT'};assert expected==actual,(name,expected,actual)
                coverage[name]=len(actual)
            expected={n for n in source_names if re.search(manifest['other_pattern'],n)}
            assert set(groups['🌐 其他地区']['all'])-{'REJECT'}==expected
            expected={n for n in source_names if re.search(manifest['netflix_filter'],n)}
            assert expected and set(groups['🎥 Netflix自动']['all'])==expected|{'REJECT'}
            assert set(groups['🎥 Netflix']['all'])==expected|{'🎥 Netflix自动'}
            assert not any('NetflixInfo' in n for n in expected)
            ai_expected={n for n in source_names if re.search(manifest['ai_filter'],n)}
            assert set(groups['🤖 AI稳定出口']['all'])==ai_expected|{'REJECT'}
            assert not any(n.startswith(('🇭🇰','🇹🇼','🇰🇷','🇩🇪','🇨🇦')) for n in ai_expected)
            report['filter_counts']=coverage|{'🌐 其他地区':len(set(groups['🌐 其他地区']['all'])),'Netflix':len(expected)}
            print(json.dumps({'phase':'filters-passed','counts':report['filter_counts']},ensure_ascii=False),flush=True)
            cases=[
              ('chatgpt.com','🤖Ai服务'),('grok.com','🤖Ai服务'),('gemini.google.com','🤖Ai服务'),('claude.ai','🤖Ai服务'),('copilot.microsoft.com','🤖Ai服务'),('apple-relay.apple.com','🤖Ai服务'),('cp4.cloudflare.com','🤖Ai服务'),
              ('onedrive.live.com','💾 OneDrive'),('tenant.sharepoint.cn','💾 OneDrive'),('tenant.sharepoint.com','💾 OneDrive'),
              ('tlu.dl.delivery.mp.microsoft.com','📦 国内下载更新CDN'),('download.windowsupdate.com','📦 国内下载更新CDN'),('download.microsoft.com','📦 国内下载更新CDN'),('officecdn.microsoft.com','📦 国内下载更新CDN'),
              ('cdn.steamcontent.com','🎮📦 游戏下载CDN'),('download.epicgames.com','🎮📦 游戏下载CDN'),('origin-a.akamaihd.net','🎮📦 游戏下载CDN'),('gamedownloads-rockstargames-com.akamaized.net','🎮📦 游戏下载CDN'),('st.dl.pinyuncloud.com','🎮📦 游戏下载CDN'),
              ('steamcommunity.com','🎮 Steam社区|网络|Chat'),('steam-chat.com','🎮 Steam社区|网络|Chat'),('api.steampowered.com','🎮 Steam社区|网络|Chat'),('cm.steampowered.com','🎮 Steam社区|网络|Chat'),
              ('x.com','🌐 社交媒体'),('facebook.com','🌐 社交媒体'),('instagram.com','🌐 社交媒体'),('tiktok.com','🌐 社交媒体'),
              ('youtube.com','📹 国外媒体'),('telegram.org','📹 国外媒体'),('discord.com','📹 国外媒体'),('tv.apple.com','📹 国外媒体'),('disneyplus.com','📹 国外媒体'),('primevideo.com','📹 国外媒体'),('netflix.com','🎥 Netflix'),
              ('open.spotify.com','🎧 国外音乐服务'),('music.apple.com','🎧 国外音乐服务'),('api.soundcloud.com','🎧 国外音乐服务'),
              ('play.google.com','🇬 谷歌服务'),('mtalk.google.com','🇬 谷歌服务'),('android.googleapis.com','🇬 谷歌服务'),('apps.microsoft.com','Ⓜ️ 微软服务'),
              ('github.com','🚀 GitHub及开发者服务'),('registry-1.docker.io','🚀 GitHub及开发者服务'),('repo.maven.apache.org','🚀 GitHub及开发者服务'),
              ('paypal.com','🪙 支付服务及加密币'),('stripe.com','🪙 支付服务及加密币'),('wise.com','🪙 支付服务及加密币'),('www.amazon.com','🪙 支付服务及加密币'),('binance.com','🪙 支付服务及加密币'),
              ('www.amazon.co.jp','🪙 支付服务及加密币'),('www.ebay.com','🪙 支付服务及加密币'),('www.taobao.com','🇨🇳 本地|国内直连'),('www.jd.com','🇨🇳 本地|国内直连'),
              ('localhost','DIRECT'),('127.0.0.1','DIRECT'),('www.baidu.com','🇨🇳 本地|国内直连'),('speedtest.net','🚀 测速工具'),('fast.com','🚀 测速工具'),('1010.hkspeedtest.com','🚀 测速工具'),('zz-meta-fullauto-probe-20261114.com','🐟 漏网之鱼'),
            ]
            proxy_opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://127.0.0.1:'+str(mixed)}))
            def request_probe(url):
                connection=http.client.HTTPConnection('127.0.0.1',mixed,timeout=12)
                try:
                    connection.request('GET',url);return connection.getresponse().read()
                finally:connection.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                for domain,target in cases:
                    token=uuid.uuid4().hex;seen=threading.Event();release=threading.Event()
                    with LOCK:HOLDS[token]=(seen,release)
                    f=pool.submit(request_probe,'http://'+domain+(':'+str(backend_a.server_port) if target=='DIRECT' else '')+'/__mf_probe/'+token)
                    try:
                        if not seen.wait(5):
                            if f.done():raise AssertionError(('Probe ended before reaching local stub',domain,f.result()))
                            raise AssertionError(('Probe did not reach local stub',domain))
                        connections=api('GET','/connections')['connections']
                        connection=next((c for c in connections if c['metadata'].get('host')==domain or (target=='DIRECT' and c['metadata'].get('destinationIP')==domain)),None)
                        assert connection,('Connection not observed',domain)
                        row={'domain':domain,'expected':target,'chains':connection['chains'],'rule':connection['rule'],'payload':connection['rulePayload']}
                        report['routing'].append(row)
                        assert target in connection['chains'],row
                    finally:
                        release.set()
                        with LOCK:HOLDS.pop(token,None)
                    assert f.result(timeout=4)==b'ok'
            print(json.dumps({'phase':'routing-passed','cases':len(cases)},ensure_ascii=False),flush=True)
            # Test lock/failover on exactly the same core as production.
            name='🇭🇰 香港节点';encoded=urllib.parse.quote(name,safe='')
            url='/group/'+encoded+'/delay?'+urllib.parse.urlencode({'url':'http://delay.invalid/health','timeout':1000})
            delays=api('GET',url)
            fast='🇭🇰 香港 01 NF&Disney*';slow='🇭🇰 香港 02'
            assert 0<delays[fast]<delays[slow],delays
            report['urltest_initial_delays_ms']=delays
            print(json.dumps({'phase':'health-delays-observed','delays':delays},ensure_ascii=False),flush=True)
            def wait_now(expected,seconds=13):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    s=api('GET','/proxies/'+encoded)
                    if s['now']==expected:return s
                    time.sleep(.2)
                raise AssertionError({'expected_now':expected,'actual':s})
            initial=wait_now(fast)
            ai_name=urllib.parse.quote('🤖 AI稳定出口',safe='')
            ai_fixed='🇯🇵 日本 01 NF&Abema&Disney*'
            api('PUT','/proxies/'+ai_name,{'name':ai_fixed})
            api('PUT','/proxies/'+urllib.parse.quote('🇯🇵 日本节点',safe=''),{'name':'🇯🇵 日本 02 - NF'})
            assert api('GET','/proxies/'+ai_name)['now']==ai_fixed
            def check_providers():
                # The pinned core coalesces provider health requests for one second.
                time.sleep(1.2)
                for p in data['proxy-providers']:api('GET','/providers/proxies/'+urllib.parse.quote(p,safe='')+'/healthcheck')
            api('PUT','/proxies/'+encoded,{'name':slow})
            fixed=wait_now(slow);assert fixed['fixed']==slow
            check_providers()
            locked=wait_now(slow);assert locked['fixed']==slow
            backend_b.alive=False
            check_providers()
            failed=wait_now(fast)
            assert failed['fixed']==slow
            ai_failed=api('GET','/proxies/'+ai_name)
            assert ai_failed['now'] in ai_expected-{ai_fixed} and ai_failed['fixed']==ai_fixed,ai_failed
            backend_b.alive=True
            check_providers()
            recovered=wait_now(slow)
            ai_recovered=api('GET','/proxies/'+ai_name)
            assert ai_recovered['now']==ai_fixed and ai_recovered['fixed']==ai_fixed,ai_recovered
            report['ai_independent_pool']={'candidates':len(ai_expected),'independent_from_region_selection':True,'fixed_failure_stays_in_own_candidates':True,'recovers_fixed':True,'business_verification_pending':True}
            api('DELETE','/proxies/'+encoded)
            cleared=wait_now(fast);assert not cleared['fixed']
            api('PUT','/proxies/'+encoded,{'name':slow})
            assert wait_now(slow)['fixed']==slow
            api('GET',url)
            retested=wait_now(fast);assert not retested['fixed']
            report['urltest_behavior']={'auto_fastest':initial['now'],'manual_fixed_healthy':fixed['now'],'provider_health_preserves_fixed':locked['fixed'],'failed_fixed_falls_back_to':failed['now'],'healthy_fixed_returns_to':recovered['now'],'unfix_returns_to':cleared['now'],'manual_full_group_delay_clears_fixed':not retested['fixed'],'all_passed':True,'note':'Manual full-group delay request clears fixed; periodic/provider health checks preserve fixed.'}
            print(json.dumps({'phase':'urltest-behavior-passed','result':report['urltest_behavior']},ensure_ascii=False),flush=True)
            report['passed']=True
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        for server,thread in BACKENDS:server.shutdown();server.server_close();thread.join(timeout=2)
        report['core_stopped']=proc.poll() is not None if proc else True
        (WORK/'native-behavior.safe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
