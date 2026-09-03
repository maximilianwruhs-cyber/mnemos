#!/usr/bin/env python3
"""Build a deterministic flat-stage manifest from scope policy and store inventory."""
from __future__ import annotations
import argparse,fnmatch,json,os,sys
from pathlib import Path

def select(scope,listing):
 chosen=set(scope.get('required',[]))
 for pat in scope.get('include',[]): chosen.update(p for p in listing if fnmatch.fnmatch(p,pat))
 for pat in scope.get('exclude',[]): chosen={p for p in chosen if not fnmatch.fnmatch(p,pat)}
 return sorted(chosen)
def build(scope,listing,stage):
 paths=select(scope,listing); out={}; missing=[]
 for path in paths:
  name=os.path.basename(path)
  if name in out and out[name]!=path: raise ValueError(f"flat-stage basename collision: {name}: {out[name]} vs {path}")
  if not (stage/name).exists(): missing.append(path)
  else: out[name]=path
 return out,missing
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--scope',required=True); p.add_argument('--listing',required=True); p.add_argument('--stage-dir',default='/tmp'); p.add_argument('--output',default='/tmp/_manifest.json'); a=p.parse_args(argv)
 scope=json.loads(Path(a.scope).read_text()); listing=json.loads(Path(a.listing).read_text()); manifest,missing=build(scope,listing,Path(a.stage_dir))
 Path(a.output).write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
 print(f"selected={len(select(scope,listing))} staged={len(manifest)} missing={len(missing)}")
 for x in missing: print('MISSING',x)
 return 2 if missing else 0
if __name__=='__main__': raise SystemExit(main())
