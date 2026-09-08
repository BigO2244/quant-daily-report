#!/usr/bin/env python3
"""One bounded, read-only PAPER snapshot; no trading-runtime imports or orders."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import datetime as dt, hashlib, json, pathlib, re, subprocess, urllib.request
r=pathlib.Path('/home/brettolson/quant-daily-report')
c=CONTRACT
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=r,text=True).strip()
assert head==c['expected_deployed_sha'], 'deployed source changed'
e={}
for line in (r/'.env').read_text().splitlines():
 match=re.match(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$',line.strip())
 if match:e[match.group(1)]=match.group(2).strip().strip("'\"")
base=str(e.get('ALPACA_BASE_URL') or '').rstrip('/')
assert base=='https://paper-api.alpaca.markets', 'PAPER endpoint required'
key=e.get('ALPACA_API_KEY_ID') or e.get('ALPACA_KEY_ID')
secret=e.get('ALPACA_API_SECRET_KEY') or e.get('ALPACA_SECRET_KEY')
assert key and secret, 'PAPER credentials unavailable'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise RuntimeError('redirect forbidden')
opener=urllib.request.build_opener(NoRedirect())
count=0
def get(path):
 global count
 count+=1
 assert count<=3
 request=urllib.request.Request(base+path, headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret},method='GET')
 with opener.open(request,timeout=10) as response:
  raw=response.read(1048577)
  assert len(raw)<=1048576, 'response limit'
  return json.loads(raw)
def h(body):return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
started=dt.datetime.now(dt.timezone.utc).isoformat()
account=get('/v2/account');positions=get('/v2/positions');orders=get('/v2/orders?status=open&limit=100&nested=false')
assert isinstance(account,dict) and isinstance(positions,list) and isinstance(orders,list)
identity=hashlib.sha256(str(account['id']).strip().encode()).hexdigest()
assert identity==c['expected_account_id_hash'], 'PAPER account identity changed'
assert account.get('status')=='ACTIVE', 'PAPER account not active'
pulled=dt.datetime.now(dt.timezone.utc).isoformat()
account_row={k:account.get(k) for k in ['equity','cash','long_market_value','short_market_value','buying_power','status','created_at']}
account_row.update(pulled_at_utc=pulled,account_number_last4=str(account.get('account_number',''))[-4:],account_id_hash=identity)
position_row={'pulled_at_utc':pulled,'positions':positions}
opening={'schema_version':'caerus.paper_opening_capture.v1','account_scope':'PAPER','account_id_hash':identity,
 'pulled_at_utc':pulled,'account_snapshot_hash':h(account_row),'positions_snapshot_hash':h(position_row),
 'open_orders':[{k:o.get(k) for k in ['id','symbol','side','qty','status']} for o in orders]}
opening['content_hash']=h(opening)
files=[];total=0
for name in c['files']:
 p=r/name
 assert p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(r)
 raw=p.read_bytes();total+=len(raw)
 assert len(raw)<=2097152 and total<=6291456, 'file capture limit'
 files.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'text':raw.decode()})
print(json.dumps({'status':'CAPTURED','started_at_utc':started,'completed_at_utc':pulled,'deployed_sha':head,
 'broker_get_calls':count,'account_snapshot':account_row,'positions_snapshot':position_row,'opening_capture':opening,
 'files':files,'broker_orders':0,'remote_writes':0},allow_nan=False))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--contract',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    assert manifest['active_stage']=='AQP2'
    assert [s['stage_id'] for s in manifest['stages'] if s.get('execution_ready')]==['AQP2']
    assert manifest['limits']['broker_api_calls']==3 and manifest['limits']['vm_connections']==1
    assert manifest['limits']['broker_orders']==0 and manifest['limits']['production_mutations']==0
    assert dt.datetime.now(dt.timezone.utc)<dt.datetime.fromisoformat(manifest['stage_clock']['deadline_utc'])
    root=args.manifest.resolve().parents[3]
    for source in manifest['bound_sources']:
        assert hashlib.sha256((root/source['path']).read_bytes()).hexdigest()==source['sha256']
    contract=json.loads(args.contract.read_text())
    assert str(args.output.resolve())==str((root/contract['output']).resolve())
    assert not args.output.exists()
    code=REMOTE.replace('CONTRACT',repr(contract),1)
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','caerus-vm',
        '/home/brettolson/.venvs/quant-daily-report/bin/python -'],input=code,text=True,capture_output=True,timeout=75)
    if result.returncode:
        # Do not echo remote exceptions or response bodies near credentials.
        raise RuntimeError('read-only PAPER capture failed; no retry or remote writes')
    assert len(result.stdout.encode())<=8388608
    capture=json.loads(result.stdout)
    capture['manifest_sha256']=hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as handle:json.dump(capture,handle,indent=2,allow_nan=False)
    print(json.dumps({'status':'CAPTURED','broker_get_calls':capture['broker_get_calls'],
                      'open_orders':len(capture['opening_capture']['open_orders']),'files':len(capture['files'])}))

if __name__=='__main__':main()
