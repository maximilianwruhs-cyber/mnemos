#!/usr/bin/env python3
"""Atomically create a graph-safe L3 note and matching L2 stub in a local workspace."""
from __future__ import annotations
import argparse,os,re,tempfile
from pathlib import Path

def create(memory:Path,root:Path,nid:str,title:str,category:str,body:str):
 text=memory.read_text()
 if nid in text: raise ValueError(f"ID already exists: {nid}")
 if not re.fullmatch(r'MEM-\d{4}-\d{4}',nid): raise ValueError('invalid MEM ID')
 if category not in {'context','lessons','decisions','preferences'}: raise ValueError('invalid category')
 slug=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')
 rel=f"Memory/{category}/{nid}-{slug}.md"; target=root/rel
 if target.exists(): raise ValueError(f"target exists: {rel}")
 heading=f"# {nid} — {title}\n\n"; note=heading+body.rstrip()+"\n"
 marker='\n## 3. Ephemeral Scratchpad'
 if marker not in text: raise ValueError('Atomic Notes insertion marker missing')
 stub=f"\n### [{nid}] {title} — demoted to L3\n\n- **Stub.** Full note: `{rel}`\n"
 updated=text.replace(marker,stub+marker,1)
 # prepare both files before replacing either; rollback MEMORY if note replace fails
 target.parent.mkdir(parents=True,exist_ok=True)
 old=text
 with tempfile.NamedTemporaryFile('w',delete=False,dir=memory.parent,encoding='utf-8') as f: f.write(updated); memtmp=Path(f.name)
 with tempfile.NamedTemporaryFile('w',delete=False,dir=target.parent,encoding='utf-8') as f: f.write(note); notetmp=Path(f.name)
 try:
  os.replace(notetmp,target); os.replace(memtmp,memory)
 except Exception:
  if target.exists(): target.unlink()
  memory.write_text(old); raise
 return rel

def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--memory',required=True); p.add_argument('--root',required=True); p.add_argument('--id',required=True); p.add_argument('--title',required=True); p.add_argument('--category',required=True); p.add_argument('--body-file',required=True); a=p.parse_args(argv)
 print(create(Path(a.memory),Path(a.root),a.id,a.title,a.category,Path(a.body_file).read_text()))
if __name__=='__main__': main()
