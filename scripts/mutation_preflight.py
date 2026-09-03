#!/usr/bin/env python3
"""Project a MEMORY.md mutation before committing it.

Usage: mutation_preflight.py FILE --old-file OLD --new-file NEW
Exit 0: projected state within hard caps; 1: invalid/ambiguous replacement or cap breach.
"""
from __future__ import annotations
import argparse,re,sys
from pathlib import Path
BYTE_CAP=12288; LINE_CAP=200; NOTE_CAP=12; WARN=.85

def project(text:str,old:str,new:str):
 count=text.count(old)
 if count!=1: raise ValueError(f"old text must occur exactly once; found {count}")
 result=text.replace(old,new,1)
 b=len(result.encode()); lines=len(result.splitlines()); notes=len(re.findall(r'^### \[MEM-\d{4}-\d{4}\]',result,re.M))
 state="RED" if b>BYTE_CAP or lines>LINE_CAP or notes>NOTE_CAP else ("AMBER" if b/BYTE_CAP>=WARN or lines/LINE_CAP>=WARN or notes/NOTE_CAP>=WARN else "GREEN")
 return result,{"current_bytes":len(text.encode()),"delta_bytes":len(new.encode())-len(old.encode()),"projected_bytes":b,"byte_ratio":b/BYTE_CAP,"lines":lines,"notes":notes,"state":state}
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('file'); p.add_argument('--old-file',required=True); p.add_argument('--new-file',required=True); p.add_argument('--write',action='store_true'); a=p.parse_args(argv)
 try:
  path=Path(a.file); result,m=project(path.read_text(),Path(a.old_file).read_text(),Path(a.new_file).read_text())
 except Exception as e: print("ERROR",e); return 1
 for k,v in m.items(): print(f"{k}: {v:.1%}" if k=='byte_ratio' else f"{k}: {v}")
 if a.write and m['state']!='RED': path.write_text(result); print('written:',path)
 return 1 if m['state']=='RED' else 0
if __name__=='__main__': raise SystemExit(main())
