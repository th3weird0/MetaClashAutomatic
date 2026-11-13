"""Compose the outlet modules locally and validate with the verified core.

Only the documented YAML operators used by these modules are implemented here.
OpenClash General settings and shell/Ruby execution are outside this offline check.
"""
import concurrent.futures
import copy
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import yaml

REPO=pathlib.Path(__file__).resolve().parents[1]
MODULES=['Tiktok-selector','clash-fullauto','WiFi_Calling','MetaFullautoSafety','PrivateNet']

def section(path):
    text=path.read_text(encoding='utf-8')
    match=re.search(r'^\[YAML\]\s*\n(.*?)(?=^\[(?:General|Overwrite)\]|\Z)',text,re.M|re.S)
    if not match:raise ValueError('Missing YAML section: '+path.name)
    return yaml.safe_load(match[1])

def merge(target,patch):
    for key,value in patch.items():
        operation=''
        if key.startswith('+'):key=key[1:];operation='prepend'
        elif key.endswith(('*','+','-','!')):
            key,operation=key[:-1],{'*':'update','+':'append','-':'remove','!':'replace'}[key[-1]]
        if operation=='update':
            count=0
            for row in target.get(key,[]):
                if not isinstance(row,dict):raise ValueError('Only object-array conditions are supported')
                def matches(field,wanted):
                    actual=row.get(field)
                    if isinstance(wanted,str) and wanted.startswith('/') and wanted.endswith('/'):
                        return isinstance(actual,str) and re.search(wanted[1:-1],actual) is not None
                    return actual==wanted
                if all(matches(field,wanted) for field,wanted in value['where'].items()):
                    merge(row,value['set']);count+=1
            if not count:raise ValueError('Conditional outlet update matched no groups')
        elif operation=='remove':
            if isinstance(target.get(key),list) and isinstance(value,list):target[key]=[v for v in target[key] if v not in value]
            else:target.pop(key,None)
        elif operation in ('prepend','append') and isinstance(value,list):
            old=target.get(key,[])
            if not isinstance(old,list):raise ValueError('Array operator on a non-array')
            target[key]=copy.deepcopy(value)+old if operation=='prepend' else old+copy.deepcopy(value)
        elif operation!='replace' and isinstance(value,dict):
            if key not in target:target[key]={}
            if not isinstance(target[key],dict):raise ValueError('Map merge on a non-map')
            merge(target[key],value)
        else:target[key]=copy.deepcopy(value)

def runtime(work,core):
    """Check actual core group expansion after its rule providers are ready."""
    def free_port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    data=yaml.safe_load((work/'module-native.yaml').read_text(encoding='utf-8'))
    controller=free_port();data['external-controller']='127.0.0.1:'+str(controller)
    data['mixed-port']=0;data['secret']='';data['profile']={'store-selected':False}
    config=work/'module-runtime.yaml';config.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path):
        with opener.open('http://127.0.0.1:'+str(controller)+path,timeout=2) as r:return json.load(r)
    proc=None
    try:
        with (work/'module-runtime.log').open('wb') as log:
            proc=subprocess.Popen([core,'-d',str(work/'module-data'),'-f',str(config)],stdout=log,stderr=subprocess.STDOUT)
            actual=pathlib.Path('/proc/'+str(proc.pid)+'/cmdline').read_bytes().split(b'\0')
            assert str(config).encode() in actual and core.encode() in actual,'Core command identity mismatch'
            deadline=time.time()+40
            while time.time()<deadline:
                if proc.poll() is not None:raise RuntimeError('Module test core exited; see module-runtime.log')
                try:
                    providers=api('/providers/rules')['providers']
                    proxies=api('/proxies')['proxies']
                    if all(p.get('ruleCount',0)>0 for p in providers.values()) and all(proxies[g['name']].get('all') for g in data['proxy-groups']):break
                except (OSError,ValueError,KeyError):pass
                time.sleep(.2)
            else:raise RuntimeError('Module providers/groups did not become ready')
            counts={}
            for group in data['proxy-groups']:
                candidates=proxies[group['name']]['all']
                assert 'COMPATIBLE' not in candidates,('Unexpected direct compatibility fallback',group['name'])
                counts[group['name']]=len(candidates)
            report={'passed':True,'groups':len(counts),'group_candidates':counts,'rule_providers':len(providers),'yaml_composition_only':True,'general_shell_ruby_tested':False,'live_router_changed':False}
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
    report['core_stopped']=True
    (work/'module-runtime.safe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'phase':'module-runtime-passed','groups':report['groups'],'rule_providers':len(providers),'core_stopped':True},ensure_ascii=False),flush=True)

def main(static_only=False):
    spec=importlib.util.spec_from_file_location('local_converter_check',REPO/'tools'/'check-local.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    from policy import prepare
    work=helper.WORK;home=work/('module-static-data' if static_only else 'module-data')
    template_digest=hashlib.sha256((REPO/'MetaFullauto_Full.ini').read_bytes()).hexdigest()
    conversion=json.loads((work/'conversion-verification.safe.json').read_text(encoding='utf-8'))
    assert conversion['template_sha256']==template_digest,'Template changed; a new conversion is required'
    for row in json.loads((work/'provider-downloads.safe.json').read_text(encoding='utf-8')):
        blob=work/'runtime-data'/'rule-copies'/row['local_file']
        assert hashlib.sha256(blob.read_bytes()).hexdigest()==row['sha256'],'Template rule cache changed'
    # Preserve each run's input cache in its own home, never share cache.db.
    shutil.copytree(work/'runtime-data',home,dirs_exist_ok=True,ignore=shutil.ignore_patterns('cache.db','.cache.db','behavior-nodes.yaml'))
    data=yaml.safe_load((work/'converted-native-test.yaml').read_text(encoding='utf-8'))
    added_rules=0
    for name in MODULES:
        patch=section(REPO/'overwrite'/name)
        if name not in ('PrivateNet','MetaFullautoSafety'):added_rules+=len(patch.get('+rules',[]))
        merge(data,patch)
    # The TikTok module keeps its existing post-merge position instruction.
    social=next(g for g in data['proxy-groups'] if g['name']=='🌐 社交媒体')
    data['proxy-groups'].remove(social);data['proxy-groups'].insert(6,social)
    http_providers={name:p for name,p in data['rule-providers'].items() if p.get('type')=='http'}
    assert all(p['interval']==172800 for p in http_providers.values()),'Legacy provider interval is not 48h'
    if static_only:
        previous=json.loads((work/'module-verification.safe.json').read_text(encoding='utf-8'))
        cached={row['url']:row for row in previous['additional_http_sources']}
        downloads=[]
        for name,provider in http_providers.items():
            row=copy.deepcopy(cached[provider['url']]);row['name']=name
            blob=work/'remote-rule-copies'/row['local_file']
            assert hashlib.sha256(blob.read_bytes()).hexdigest()==row['sha256'],'Cached source changed'
            downloads.append(row)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:downloads=list(pool.map(helper.download_provider,http_providers.items()))
    byname={row['name']:row for row in downloads}
    dest=home/'module-rules';dest.mkdir(exist_ok=True)
    for name,p in http_providers.items():
        filename=byname[name]['local_file'];shutil.copy2(work/'remote-rule-copies'/filename,dest/filename)
        p['type']='file';p.pop('url',None);p.pop('interval',None);p['path']='./module-rules/'+filename
    data,policy_report=prepare(data,home,first_activation=True)
    names={g['name'] for g in data['proxy-groups']}|{'DIRECT','REJECT','REJECT-DROP','PASS'}
    for rule in data['rules']:
        parts=rule.split(',');outlet=parts[-2] if parts[-1]=='no-resolve' else parts[-1]
        assert outlet in names,('Missing rule outlet',outlet)
        if parts[0]=='RULE-SET':assert parts[1] in data['rule-providers'],('Missing rule provider',parts[1])
    path=work/('module-static.yaml' if static_only else 'module-native.yaml');path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    if static_only:
        report={'passed':True,'template_sha256':template_digest,'module_order':MODULES,'retained_legacy_rules':added_rules,'groups':len(data['proxy-groups']),'rule_providers':len(data['rule-providers']),'merged_rules':len(data['rules']),'source_cache_sha256_verified':True,'policy':policy_report,'core_started':False,'native_config_tested':False,'runtime_tested':False}
        (work/'module-static.safe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    command=['wsl.exe','-d',helper.DISTRO,'--exec',helper.CORE,'-t','-d',helper.wslpath(home),'-f',helper.wslpath(path)]
    result=subprocess.run(command,capture_output=True,timeout=90)
    output=(result.stdout+result.stderr).decode('utf-8','replace');(work/'module-native-test.log').write_text(output,encoding='utf-8')
    if result.returncode:raise RuntimeError(output[-2500:])
    subprocess.run(['wsl.exe','-d',helper.DISTRO,'--exec','/usr/bin/python3','-u',helper.wslpath(pathlib.Path(__file__)),helper.wslpath(work),helper.CORE],check=True,timeout=60)
    report={'passed':True,'module_order':MODULES,'legacy_prepend_rules':added_rules,'merged_rules':len(data['rules']),'additional_http_sources':downloads,'policy':policy_report,'general_shell_ruby_tested':False}
    (work/'module-verification.safe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'phase':'module-composition-passed','retained_legacy_rules':added_rules,'merged_rules':len(data['rules']),'extra_sources':len(downloads)},ensure_ascii=False),flush=True)

if __name__=='__main__':
    if len(sys.argv)==3:runtime(pathlib.Path(sys.argv[1]),sys.argv[2])
    elif sys.argv[1:]==['--static-only']:main(static_only=True)
    elif len(sys.argv)==1:main()
    else:raise SystemExit('Use no arguments for native checks, or --static-only to avoid starting a core')
