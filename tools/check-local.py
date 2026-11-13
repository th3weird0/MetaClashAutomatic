import concurrent.futures
import copy
import hashlib
import http.server
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import requests
import yaml

sys.stdout.reconfigure(encoding='utf-8')
REPO=pathlib.Path(__file__).resolve().parents[1]
ROOT=REPO/'.local'
WORK=ROOT/'validation'
WORK.mkdir(exist_ok=True)
SETTINGS=json.loads((ROOT/'settings.json').read_text(encoding='utf-8'))
EXE=pathlib.Path(SETTINGS['converter'])
CORE=SETTINGS['core_wsl']
DISTRO=SETTINGS.get('wsl_distro','Ubuntu-24.04')
def wslpath(path):
    p=pathlib.Path(path).resolve().as_posix()
    return '/mnt/'+p[0].lower()+p[2:] if len(p)>1 and p[1]==':' else p
WSLROOT=wslpath(WORK)
from policy import prepare
from template_manifest import manifest_for
SESSION=requests.Session()
SESSION.trust_env=False

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]

NAMES=['🇭🇰 香港 01 NF&Disney*','🇭🇰 香港 02','🇹🇼 台湾 01 NF&动画疯*','🇯🇵 日本 01 NF&Abema&Disney*','🇸🇬 新加坡 01 Netflix','🇺🇸 美国 01 [Netflix]','🇰🇷 韩国 01','🇩🇪 德国 01','🇨🇦 加拿大 01','🇺🇸 美国 家宽 0.5x','🇯🇵 日本 02 - NF','🇭🇰 香港 NetflixInfo 02']
FIXTURE={'proxies':[{'name':name,'type':'http','server':'127.0.0.1','port':39001+i} for i,name in enumerate(NAMES)]}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path=='/MetaFullauto_Full.ini':body=(REPO/'MetaFullauto_Full.ini').read_bytes()
        elif path in ['/sub-a.yaml','/sub-b.yaml','/sub-zero.yaml']:
            fixture=FIXTURE
            if path=='/sub-zero.yaml':
                pattern=manifest_for(REPO/'MetaFullauto_Full.ini')['netflix_filter']
                fixture={'proxies':[p for p in FIXTURE['proxies'] if not re.search(pattern,p['name'])]}
            body=yaml.safe_dump(fixture,allow_unicode=True,sort_keys=False).encode()
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def download_provider(item):
    name,provider=item
    url=provider['url']
    response=requests.get(url,timeout=45)
    response.raise_for_status()
    assert response.content,'Empty rule provider: '+name
    target=WORK/'remote-rule-copies'/(hashlib.sha256(url.encode()).hexdigest()+pathlib.PurePosixPath(urllib.parse.urlparse(url).path).suffix)
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(response.content)
    fmt=provider.get('format','yaml')
    if fmt=='yaml':
        parsed=yaml.safe_load(response.content)
        assert isinstance(parsed,dict) and isinstance(parsed.get('payload'),list),('Not YAML provider',name,url,fmt)
    elif fmt=='mrs':
        # MRS is compressed; the installed native parser is authoritative.
        pass
    elif fmt=='text':
        response.content.decode('utf-8-sig')
    else:raise AssertionError('Unexpected format '+fmt)
    return {'name':name,'url':url,'bytes':len(response.content),'sha256':hashlib.sha256(response.content).hexdigest(),'format':fmt,'behavior':provider['behavior'],'local_file':target.name}

def main():
    (ROOT/'template-manifest.json').write_text(json.dumps(manifest_for(REPO/'MetaFullauto_Full.ini'),ensure_ascii=False,indent=2),encoding='utf-8')
    stamp=uuid.uuid4().hex[:10]
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    fixture_port=server.server_port
    converter_port=port()
    (WORK/'base.yaml').write_text('mixed-port: 0\nallow-lan: false\nmode: rule\nlog-level: warning\nproxies: []\nproxy-groups: []\nrules: []\n',encoding='utf-8')
    pref='''version = 1
[common]
default_url = []
enable_insert = false
insert_url = []
default_external_config = ""
fallback_to_default_external_config = false
base_path = %s
clash_rule_base = "base.yaml"
proxy_config = "NONE"
proxy_ruleset = "NONE"
proxy_subscription = "NONE"
append_proxy_type = false
[security]
profile = "lan"
allow_public_upload = false
[proxy_provider]
interval = 3600
proxy_direct = true
[node_pref]
sort_flag = false
clash_use_new_field_name = true
[managed_config]
write_managed_config = false
managed_config_prefix = "http://127.0.0.1:%d"
[surge_external_proxy]
resolve_hostname = false
[emojis]
add_emoji = false
remove_old_emoji = false
[ruleset]
enabled = true
overwrite_original_rules = true
update_ruleset_on_request = false
[template]
template_path = ""
[server]
listen = "127.0.0.1"
port = %d
serve_file_root = ""
[advanced]
log_level = "info"
print_debug_info = false
resource_control = "compat"
max_pending_connections = 8
max_concurrent_threads = 2
max_server_threads = 4
request_deadline_ms = 120000
max_allowed_rulesets = 64
max_allowed_rules = 0
max_allowed_download_size = 26214400
enable_cache = false
async_fetch_ruleset = true
skip_failed_links = false
[statistics]
enabled = false
'''%(json.dumps(str(WORK)),converter_port,converter_port)
    prefpath=WORK/('pref-'+stamp+'.toml');prefpath.write_text(pref,encoding='utf-8')
    logpath=WORK/('converter-live-'+stamp+'.log')
    proc=None
    summary={}
    try:
        with logpath.open('wb') as logfile:
            proc=subprocess.Popen([str(EXE),'-f',str(prefpath)],cwd=EXE.parent,stdout=logfile,stderr=subprocess.STDOUT)
            cmd="$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; $p | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"%proc.pid
            identity=json.loads(subprocess.run(['pwsh','-NoProfile','-Command',cmd],capture_output=True,check=True,timeout=15).stdout.decode('utf-8-sig'))
            assert str(prefpath) in identity['CommandLine'] and identity['ParentProcessId']==__import__('os').getpid(),identity
            summary['converter_identity']=identity
            print(json.dumps({'phase':'converter-started','pid':proc.pid,'log':logpath.name},ensure_ascii=False),flush=True)
            endpoint='http://127.0.0.1:'+str(converter_port)
            for _ in range(60):
                if proc.poll() is not None:raise RuntimeError('Converter exited: '+logpath.read_text(encoding='utf-8',errors='replace')[-1800:])
                try:
                    response=SESSION.get(endpoint+'/version',timeout=1)
                    if response.status_code==200:break
                except requests.RequestException:pass
                time.sleep(.25)
            else:raise RuntimeError('Converter startup timed out')
            summary['converter_version']=response.text[:200]
            print(json.dumps({'phase':'converter-ready','version':summary['converter_version'],'log_tail':logpath.read_text(encoding='utf-8',errors='replace').splitlines()[-2:]},ensure_ascii=False),flush=True)
            response=SESSION.get(endpoint+'/sub',params={'target':'clash','expand':'false','url':'http://127.0.0.1:%d/sub-a.yaml|http://127.0.0.1:%d/sub-b.yaml'%(fixture_port,fixture_port),'config':'http://127.0.0.1:%d/MetaFullauto_Full.ini'%fixture_port},timeout=140)
            if response.status_code!=200:raise RuntimeError('Conversion failed HTTP '+str(response.status_code)+': '+response.text[:1500])
            outpath=WORK/'converted-original.yaml';outpath.write_bytes(response.content)
            zero_response=SESSION.get(endpoint+'/sub',params={'target':'clash','expand':'false','url':'http://127.0.0.1:%d/sub-zero.yaml'%fixture_port,'config':'http://127.0.0.1:%d/MetaFullauto_Full.ini'%fixture_port},timeout=140)
            zero_response.raise_for_status()
            zero=yaml.safe_load(zero_response.content)
            (WORK/'converted-zero-original.yaml').write_bytes(zero_response.content)
            data=yaml.safe_load(response.content)
            assert isinstance(data,dict) and data.get('proxy-groups') and data.get('rules'),response.text[:600]
            manifest=json.loads((ROOT/'template-manifest.json').read_text(encoding='utf-8'))
            # Native mixed rulesets can create domain/ip/classical provider
            # references separately. Verify source coverage, not a false 1:1 count.
            assert manifest['ruleset_count']<=len(data['rules'])<200
            output_urls={provider['url'] for provider in data.get('rule-providers',{}).values()}
            mrs_urls={row['spec'].split(',',1)[0].split(':',1)[1] for row in manifest['rulesets'] if row['spec'].startswith(('clash-domain:','clash-ipcidr:')) and '.mrs,' in row['spec']}
            assert mrs_urls<=output_urls,('MRS sources omitted',sorted(mrs_urls-output_urls))
            for row in manifest['rulesets']:
                spec=row['spec']
                if not spec.startswith('[]'):continue
                rule=spec[2:]
                if rule=='FINAL':expected='MATCH,'+row['group']
                elif rule.endswith(',no-resolve'):expected=rule[:-11]+','+row['group']+',no-resolve'
                else:expected=rule+','+row['group']
                assert expected in data['rules'],('Native rule omitted',expected)
            assert len(data['proxy-groups'])==30,len(data['proxy-groups'])
            groups={g['name']:g for g in data['proxy-groups']}
            assert groups['🎯 全球直连']['proxies']==['DIRECT']
            assert groups['🎥 Netflix']['filter']==manifest['netflix_filter']
            assert groups['🎥 Netflix自动']['filter']==manifest['netflix_filter']
            assert groups['♻️ 自动选择']['type']=='url-test'
            assert all(g['type']!='fallback' for g in groups.values())
            assert data['proxy-groups'][-1]['name']=='🐟 漏网之鱼'
            assert [g['name'] for g in data['proxy-groups'][:5]]==['🚀 手动选择','🇨🇳 本地|国内直连','📦 国内下载更新CDN','🎮📦 游戏下载CDN','💾 OneDrive']
            names=set(groups)|{p['name'] for p in data.get('proxies',[])}|{'DIRECT','REJECT'}
            for group in groups.values():
                for name in group.get('proxies',[]):assert name in names,('Missing group reference',group['name'],name)
            for name,pattern in manifest['region_patterns'].items():assert groups[name]['filter']==pattern
            url_providers=data['rule-providers']
            print(json.dumps({'phase':'converted','rules':len(data['rules']),'groups':len(groups),'remote_rule_providers':len(url_providers),'formats':{k:v.get('format','yaml') for k,v in url_providers.items()}},ensure_ascii=False),flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                downloaded=list(pool.map(download_provider,url_providers.items()))
            summary['remote_rule_providers']=downloaded
            (WORK/'provider-downloads.safe.json').write_text(json.dumps(downloaded,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'phase':'providers-verified','count':len(downloaded),'bytes':sum(v['bytes'] for v in downloaded)},ensure_ascii=False),flush=True)
            # Use exact downloaded source bytes with local transport for WSL validation.
            # Do not send real subscriptions or route validation traffic to the live router.
            for provider in data.get('proxy-providers',{}).values():
                provider['type']='file';provider.pop('url',None);provider.pop('proxy',None);provider.pop('interval',None);provider.pop('health-check',None)
                provider['path']='./synthetic-nodes.yaml'
            (WORK/'runtime-data'/'synthetic-nodes.yaml').write_text(yaml.safe_dump(FIXTURE,allow_unicode=True,sort_keys=False),encoding='utf-8')
            byname={row['name']:row for row in downloaded}
            local_rules=WORK/'runtime-data'/'rule-copies'
            local_rules.mkdir(exist_ok=True)
            for name,provider in data['rule-providers'].items():
                provider['type']='file';provider.pop('url',None);provider.pop('interval',None)
                shutil.copy2(WORK/'remote-rule-copies'/byname[name]['local_file'],local_rules/byname[name]['local_file'])
                provider['path']='./rule-copies/'+byname[name]['local_file']
            data,policy_report=prepare(data,WORK/'runtime-data',first_activation=True)
            summary['policy']=policy_report
            data['external-controller']='127.0.0.1:0'
            data['allow-lan']=False
            zero['rule-providers']=copy.deepcopy(data['rule-providers'])
            zero['rules']=copy.deepcopy(data['rules'])
            zero['profile']={'store-selected':False}
            zero_nodes={'proxies':[p for p in FIXTURE['proxies'] if not re.search(manifest['netflix_filter'],p['name'])]}
            (WORK/'runtime-data'/'zero-nodes.yaml').write_text(yaml.safe_dump(zero_nodes,allow_unicode=True),encoding='utf-8')
            for provider in zero.get('proxy-providers',{}).values():
                provider.clear();provider.update({'type':'file','path':'./zero-nodes.yaml'})
            for group in zero['proxy-groups']:group['empty-fallback']='REJECT'
            zero['external-controller']='127.0.0.1:0';zero['mixed-port']=0;zero['allow-lan']=False
            (WORK/'converted-zero-native.yaml').write_text(yaml.safe_dump(zero,allow_unicode=True,sort_keys=False),encoding='utf-8')
            testpath=WORK/'converted-native-test.yaml';testpath.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
            result=subprocess.run(['wsl.exe','-d',DISTRO,'--exec',CORE,'-t','-d',WSLROOT+'/runtime-data','-f',WSLROOT+'/converted-native-test.yaml'],capture_output=True,timeout=90)
            nativeout=(result.stdout+result.stderr).decode('utf-8','replace')
            (WORK/'native-config-test.log').write_text(nativeout,encoding='utf-8')
            assert result.returncode==0,nativeout[-2500:]
            summary['template_sha256']=hashlib.sha256((REPO/'MetaFullauto_Full.ini').read_bytes()).hexdigest()
            summary.update({'converted_rules':len(data['rules']),'converted_groups':len(groups),'ruleset_limit':64,'native_config_test':nativeout,'native_version':SETTINGS['core_version'],'synthetic_only':True,'router_modified':False})
            print(json.dumps({'phase':'native-config-passed','tail':nativeout.splitlines()[-3:]},ensure_ascii=False),flush=True)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        server.shutdown();server.server_close();thread.join(timeout=3)
        summary['converter_exit_code']=proc.returncode if proc else None
        summary['test_server_stopped']=True
        (WORK/'conversion-verification.safe.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
