"""Validate converted candidates before replacement; secrets are read only from env.

The input is a staged YAML, never the running configuration. A failed check does
not open the output. Runtime state migration is a separate, explicit API action.
"""
import argparse
import copy
import importlib.util
import json
import os
import pathlib
import re
import tempfile
import urllib.parse
import urllib.request
import yaml

FISH='🐟 漏网之鱼'
MANUAL='🚀 手动选择'
AI='🤖 AI稳定出口'
NF='🎥 Netflix'
NF_AUTO='🎥 Netflix自动'
BUILTINS={'DIRECT','REJECT','REJECT-DROP','PASS','COMPATIBLE'}

_spec=importlib.util.spec_from_file_location('metaclash_checks',
    pathlib.Path(__file__).resolve().parents[1]/'tool-for-testing'/'metaclash_checks.py')
_checks=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_checks)
load=_checks.load
inventory=_checks.inventory
members=_checks.members
validate_ruleset_references=_checks.validate_ruleset_references

def prepare(data,home,first_activation=False):
    result=copy.deepcopy(data)
    validate_ruleset_references(result)
    nodes,providers=inventory(result,home)
    groups={g['name']:g for g in result.get('proxy-groups',[])}
    if len(groups)!=len(result.get('proxy-groups',[])):raise ValueError('Duplicate group names')
    if not {FISH,MANUAL,AI,NF,NF_AUTO}<=groups.keys():raise ValueError('Required policy group missing')
    nf=groups[NF_AUTO]
    eligible=members(nf,nodes,providers)&nodes.keys()-BUILTINS
    if not eligible:raise ValueError('Netflix has zero eligible nodes; active configuration must remain unchanged')
    if any(not re.search(nf['filter'],name) for name in eligible):raise ValueError('Netflix candidate bypasses suffix filter')
    ai=members(groups[AI],nodes,providers)&nodes.keys()-BUILTINS
    if not ai:raise ValueError('AI stable pool has zero eligible nodes')
    if groups['🤖Ai服务'].get('proxies')!=[AI]:raise ValueError('AI selector bypasses independent pool')
    counts={}
    for name,group in groups.items():
        values=members(group,nodes,providers)
        for ref in values:
            if ref not in nodes and ref not in groups and ref not in BUILTINS:raise ValueError('Unknown outbound reference')
        actual=values-BUILTINS
        if group['type'] in ('url-test','fallback','load-balance') and not actual:
            raise ValueError('Unexpected empty automatic group: '+name)
        counts[name]=len(actual)
        group['empty-fallback']='REJECT'
        if name in (NF_AUTO,AI):
            group['proxies']=[p for p in group.get('proxies',[]) if p!='DIRECT']
            if 'REJECT' not in group['proxies']:group['proxies'].append('REJECT')
    # Reject cycles before a prospective candidate can replace any destination.
    def visit(name,stack):
        if name in stack:raise ValueError('Cyclic outbound reference')
        for child in members(groups[name],nodes,providers)&groups.keys():visit(child,stack|{name})
    for name in groups:visit(name,set())
    group=groups[FISH]
    group['proxies']=[MANUAL]+[n for n in group.get('proxies',[]) if n!=MANUAL]
    rules=result.get('rules',[])
    module=pathlib.Path(__file__).resolve().parents[1]/'overwrite'/'PrivateNet'
    frozen=yaml.safe_load(module.read_text(encoding='utf-8').split('[YAML]',1)[1])['+rules']
    result['rules']=frozen+[r for r in rules if r not in frozen and not r.startswith(('GEOSITE,private,','GEOIP,private,'))]
    for p in result.get('rule-providers',{}).values():
        if p.get('type')=='http':p['interval']=172800
    if first_activation:result.setdefault('profile',{})['store-selected']=False
    return result,{'eligible_netflix':len(eligible),'eligible_ai':len(ai),'group_counts':counts,'first_activation_ignores_saved_choices':first_activation,'business_unlock_tested':False}

def atomic_write(path,data):
    path=pathlib.Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False,newline='\n') as f:
            temp=pathlib.Path(f.name);yaml.safe_dump(data,f,allow_unicode=True,sort_keys=False)
        os.replace(temp,path)
    finally:
        if temp and temp.exists():temp.unlink()

def migrate_state(base,apply=False,pin_ai=False):
    secret=os.environ.get('MIHOMO_API_SECRET','')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(method,path,payload=None):
        body=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request(base.rstrip('/')+path,data=body,method=method,headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'})
        with opener.open(req,timeout=15) as r:
            raw=r.read();return json.loads(raw) if raw else None
    state=api('GET','/proxies')['proxies']
    if FISH not in state or MANUAL not in state:raise ValueError('New template is not active; state migration refused')
    before=state[FISH].get('now')
    needs=before!=MANUAL
    if needs and apply:api('PUT','/proxies/'+urllib.parse.quote(FISH,safe=''),{'name':MANUAL})
    pinned=False
    if pin_ai:
        pool=state.get(AI,{})
        candidates=[n for n in pool.get('all',[]) if n not in BUILTINS and n in state and state[n].get('alive')]
        if not candidates:raise ValueError('No healthy AI node to pin; business verification is still required')
        preferred=pool.get('fixed') or pool.get('now')
        chosen=preferred if preferred in candidates else candidates[0]
        if apply:
            api('PUT','/proxies/'+urllib.parse.quote(AI,safe=''),{'name':chosen});pinned=True
    after=api('GET','/proxies')['proxies']
    if apply and after[FISH]['now']!=MANUAL:raise ValueError('Catchall state migration did not take effect')
    return {'catchall_was_manual_proxy':not needs,'migration_required':needs,'applied':apply,'catchall_is_manual_proxy':after[FISH]['now']==MANUAL,'ai_pinned':pinned}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('prepare');p.add_argument('input');p.add_argument('--data-dir',required=True);p.add_argument('--output');p.add_argument('--first-activation',action='store_true')
    s=subs.add_parser('state');s.add_argument('--controller',required=True);s.add_argument('--apply',action='store_true');s.add_argument('--pin-ai',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':
        candidate,report=prepare(load(args.input),args.data_dir,args.first_activation)
        if args.output:
            if pathlib.Path(args.input).resolve()==pathlib.Path(args.output).resolve():raise ValueError('Use a separate staged output, never modify the input in place')
            atomic_write(args.output,candidate)
    else:report=migrate_state(args.controller,args.apply,args.pin_ai)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
