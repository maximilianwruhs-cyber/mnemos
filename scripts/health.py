#!/usr/bin/env python3
"""MNEMOS health v2: content-gated, scoped, side-effect-isolated health verdict.

Staged files are flat under /tmp. Preferred inputs:
  _manifest.json       mapping staged filename -> real store path
  _store_listing.json list of every real store path
  health-scope.json    canonical scope policy
Legacy _paths.json is accepted and converted fail-closed.

The script never mutates the FileStore. It emits an explicit action plan including
verbatim derived indexes and, only for a clean stable state, a health attestation.
"""
from __future__ import annotations
import atexit, contextlib, fnmatch, hashlib, io, json, os, re, runpy, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

STAGE=Path("/tmp")
MEMORY_BYTE_CAP=12288
BYTE_WARN=.85
os.environ["PYTHONDONTWRITEBYTECODE"]="1"
sys.dont_write_bytecode=True
# Keep matplotlib caches and debug files inside ephemeral /tmp, never a publishable cwd.
_RUNTIME=Path(tempfile.mkdtemp(prefix="mnemos-health-runtime-",dir="/tmp"))
os.environ.setdefault("MPLCONFIGDIR",str(_RUNTIME/"mpl"))
os.environ.setdefault("MPLBACKEND","Agg")
os.environ.setdefault("XDG_CACHE_HOME",str(_RUNTIME/"cache"))
os.environ.setdefault("HOME",str(_RUNTIME/"home"))
for p in (Path(os.environ["MPLCONFIGDIR"]),Path(os.environ["XDG_CACHE_HOME"]),Path(os.environ["HOME"])): p.mkdir(parents=True,exist_ok=True)
atexit.register(lambda: shutil.rmtree(_RUNTIME, ignore_errors=True))

JUNK=[re.compile(x) for x in [r"\.pyc$",r"__pycache__",r"^/code-interpreter-output/.*\.manifest\.json$",r"^/code-interpreter-output/.*\.audit\.jsonl$",r"^/code-interpreter-output/INDEX\..*\.md$",r"^/code-interpreter-output/mat-debug.*\.log$",r"^/code-interpreter-output/fontlist-.*\.json$",r"^/code-interpreter-output/_paths_\d+\.json$",r"^/code-interpreter-output/_store_listing_\d+\.json$"]]
GERMAN_MARKERS=re.compile(r"\b(und|oder|aber|nicht|keine|einen|einer|wird|werden|muss|sind|für|über|zwischen|stattdessen|Erreichbare|Vorlage|Zuweisung)\b",re.I)
checks=[]; writes=[]; deletes=[]; escalations=[]
def note(state,area,detail): checks.append((state,area,detail))
def load(name):
 p=STAGE/name
 return p.read_text(encoding="utf-8") if p.exists() else ""
def run(script,argv):
 saved=sys.argv[:]; sys.argv=argv; buf=io.StringIO(); code=0
 try:
  with contextlib.redirect_stdout(buf),contextlib.redirect_stderr(buf): runpy.run_path(script,run_name="__main__")
 except SystemExit as e: code=e.code
 except Exception as e: code=f"{type(e).__name__}: {e}"
 finally: sys.argv=saved
 return code,buf.getvalue()
def manifest():
 p=STAGE/"_manifest.json"
 if p.exists():
  data=json.loads(p.read_text());
  if not isinstance(data,dict): raise ValueError("_manifest.json must be object staged-name -> real path")
  return data
 p=STAGE/"_paths.json"
 if not p.exists(): raise ValueError("_manifest.json or _paths.json required")
 paths=json.loads(p.read_text())
 if not isinstance(paths,list): raise ValueError("_paths.json must be list[str]")
 out={}
 for path in paths:
  name=os.path.basename(path)
  if name in out and out[name]!=path: raise ValueError(f"flat-stage basename collision: {name}")
  if (STAGE/name).exists(): out[name]=path
 return out
def scope_paths(scope,listing):
 chosen=set(scope.get("required",[]))
 for pat in scope.get("include",[]): chosen.update(p for p in listing if fnmatch.fnmatch(p,pat))
 for pat in scope.get("exclude",[]): chosen={p for p in chosen if not fnmatch.fnmatch(p,pat)}
 return sorted(chosen)
def canonical_digest(paths,by_path):
 rows=[]
 for path in sorted(paths):
  name=by_path.get(path)
  if not name or not (STAGE/name).exists(): continue
  data=(STAGE/name).read_bytes(); rows.append({"path":path,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
 raw=json.dumps(rows,sort_keys=True,separators=(",",":")).encode()
 return hashlib.sha256(raw).hexdigest(),rows
def strip_code_paths(text):
 text=re.sub(r"```.*?```","",text,flags=re.S)
 text=re.sub(r"`[^`]*`","",text)
 text=re.sub(r"https?://\S+|/[-\w./]+","",text)
 return text
def classified_findings(output):
 fails=[line.strip() for line in output.splitlines() if "[FAIL]" in line]
 warnings=[line.strip() for line in output.splitlines() if "[WARN]" in line]
 return fails,warnings
def cross_tier_findings(output):
 fails,warnings=classified_findings(output)
 orphans=[line.strip(" !") for line in output.splitlines()
          if line.startswith("  !") and line.strip(" !").startswith("/")]
 return fails,warnings,orphans

def main():
 global writes
 try: staged=manifest()
 except Exception as e: print("FATAL:",e,file=sys.stderr); return 2
 by_path={v:k for k,v in staged.items()}
 listing_file=STAGE/"_store_listing.json"
 listing=json.loads(listing_file.read_text()) if listing_file.exists() else []
 scope_name=by_path.get("/autonomy/config/health-scope.json","health-scope.json")
 scope=json.loads(load(scope_name)) if load(scope_name) else {"required":["/MEMORY.md","/AGENTS.md","/Memory/INDEX.md","/Memory/INDEX-L3.md","/Memory/PROTOCOL.md"],"include":["/Memory/context/*.md","/Memory/lessons/*.md","/Memory/decisions/*.md","/Memory/daily/*.md","/scripts/*.py"]}
 scoped=scope_paths(scope,listing or list(by_path))
 missing=sorted(set(scoped)-set(by_path))
 input_digest,rows=canonical_digest(scoped,by_path)
 tool_paths=scope.get("tool_inputs",["/scripts/health.py","/scripts/mnemos.py","/scripts/graphcheck.py","/scripts/doctor.py"])
 tool_digest,_=canonical_digest(tool_paths,by_path)
 fingerprint=input_digest[:16]
 memory=load(by_path.get("/MEMORY.md","MEMORY.md")); agents=load(by_path.get("/AGENTS.md","AGENTS.md"))
 # L2
 if (STAGE/"mnemos.py").exists():
  gen=STAGE/"_health_l2.md"; code,out=run(str(STAGE/"mnemos.py"),["mnemos.py","--memory",str(STAGE/by_path.get("/MEMORY.md","MEMORY.md")),"--agents",str(STAGE/by_path.get("/AGENTS.md","AGENTS.md")),"--index",str(STAGE/by_path.get("/Memory/INDEX.md","INDEX.md")),"--output",str(gen),"--today",os.environ.get("MNEMOS_TODAY","2026-08-30")])
  canonical=gen.read_text() if gen.exists() else ""; gen.unlink(missing_ok=True)
  fails,warns=classified_findings(out)
  hard=[x for x in fails if "INDEX.md" not in x]
  drift="persisted index differs" in out
  if hard: note("RED","L2 audit",f"{len(hard)} integrity failure(s)"); escalations.extend("L2: "+x for x in hard)
  if drift: note("AMBER","L2 audit","INDEX.md stale - regenerable"); writes.append(("/Memory/INDEX.md",canonical))
  if warns: note("AMBER","L2 audit",f"{len(warns)} contested-evidence warning(s)"); escalations.extend("evidence: "+x for x in warns)
  if not hard and not drift and not warns and code==0: note("GREEN","L2 audit","schema, caps and index all clean")
 else: note("RED","L2 audit","mnemos.py not staged")
 # cross-tier
 if (STAGE/"graphcheck.py").exists():
  (STAGE/"_manifest.json").write_text(json.dumps(staged))
  code,out=run(str(STAGE/"graphcheck.py"),["graphcheck.py"])
  gen=STAGE/"INDEX-L3.out.md"; canonical=gen.read_text() if gen.exists() else ""; gen.unlink(missing_ok=True)
  fails,warns,orphans=cross_tier_findings(out)
  if fails: note("RED","cross-tier",f"{len(fails)} broken link(s)"); escalations.extend("link: "+x for x in fails)
  if orphans: note("RED","cross-tier",f"{len(orphans)} orphan L3 file(s)"); escalations.extend("orphan: "+x for x in orphans)
  if warns: note("AMBER","cross-tier",f"{len(warns)} contested-evidence warning(s)"); escalations.extend("evidence: "+x for x in warns)
  if not fails and not orphans and not warns: note("GREEN","cross-tier","no orphans, all links resolve")
  if canonical and load(by_path.get("/Memory/INDEX-L3.md","INDEX-L3.md")).strip()!=canonical.strip(): note("AMBER","L3 registry","INDEX-L3.md stale - regenerable"); writes.append(("/Memory/INDEX-L3.md",canonical))
 else: note("RED","cross-tier","graphcheck.py not staged")
 # environment, isolated
 if (STAGE/"doctor.py").exists():
  previous_cwd=os.getcwd(); previous_mode=os.environ.get("DOCTOR_PRESENCE_ONLY")
  try:
   os.chdir(_RUNTIME); os.environ["DOCTOR_PRESENCE_ONLY"]="1"
   code,out=run(str(STAGE/"doctor.py"),["doctor.py"])
  finally:
   os.chdir(previous_cwd)
   if previous_mode is None: os.environ.pop("DOCTOR_PRESENCE_ONLY",None)
   else: os.environ["DOCTOR_PRESENCE_ONLY"]=previous_mode
  if "DOCTOR: GREEN" in out: note("GREEN","environment","libraries, binaries and self-tests OK")
  else: note("RED","environment","doctor is not green"); escalations.append("doctor.py reported non-green")
 else: note("AMBER","environment","doctor.py not staged")
 # pressure
 used=len(memory.encode()); ratio=used/MEMORY_BYTE_CAP
 if ratio>=1: note("RED","byte pressure",f"MEMORY.md OVER CAP {used}/{MEMORY_BYTE_CAP}")
 elif ratio>=BYTE_WARN: note("AMBER","byte pressure",f"MEMORY.md {ratio:.1%} of cap")
 else: note("GREEN","byte pressure",f"MEMORY.md {ratio:.1%} of cap")
 # junk
 if listing_file.exists():
  found=sorted(p for p in listing if any(rx.search(p) for rx in JUNK))
  if found: note("AMBER","junk",f"{len(found)} machine artifact(s) to purge"); deletes.extend(found)
  else: note("GREEN","junk","no machine artifacts in the store")
 else: note("AMBER","junk","NOT CHECKED - full inventory missing")
 # scope coverage
 if missing: note("RED","coverage",f"{len(missing)} scoped file(s) NOT staged"); escalations.extend("under-checked: "+p for p in missing)
 else: note("GREEN","coverage",f"{len(scoped)} scoped file(s) all staged")
 # deterministic convention checks; heuristic prose detection is advisory only
 violations=[]
 for path in ("/MEMORY.md","/AGENTS.md"):
  text=strip_code_paths(load(by_path.get(path,os.path.basename(path))))
  hits=sorted(set(m.group(0) for m in GERMAN_MARKERS.finditer(text)))
  if len(hits)>=2: violations.append(f"{path}: likely non-English internal prose ({', '.join(hits[:6])})")
 if violations: note("AMBER","convention lint",f"{len(violations)} advisory language finding(s)"); escalations.extend("advisory: "+x for x in violations)
 else: note("GREEN","convention lint","canonical headings/statuses and internal language clean")
 if missing and writes: escalations.append(f"{len(writes)} derived write(s) SUPPRESSED due incomplete scope"); writes=[]
 states=[x[0] for x in checks]; verdict="RED" if "RED" in states else ("AMBER" if "AMBER" in states else "GREEN")
 # Attest only stable clean state: no pending derived writes, no deletes, no RED/AMBER.
 if verdict=="GREEN" and not writes and not deletes:
  att={"schemaVersion":1,"verdict":verdict,"observedAt":datetime.now(timezone.utc).isoformat(),"scope":"full","inputDigest":"sha256:"+input_digest,"toolDigest":"sha256:"+tool_digest,"fingerprint":fingerprint,"fileCount":len(rows),"checks":{a:s for s,a,_ in checks}}
  writes.append((scope.get("attestation_path","/autonomy/state/health-attestation.json"),json.dumps(att,indent=2,sort_keys=True)+"\n"))
 w=78; print("="*w); print(f"SUBSTRATE HEALTH V2   fingerprint {fingerprint}"); print("="*w)
 for s,a,d in checks: print(f"  [{s:<5}] {a:<18} {d}")
 print("\n"+"-"*w); print(f"ACTION PLAN   {len(writes)} write(s), {len(deletes)} delete(s), {len(escalations)} escalation(s)"); print("-"*w)
 for p,_ in writes: print("  WRITE",p)
 for p in deletes: print("  DELETE",p)
 for e in escalations: print("  !",e)
 for p,c in writes:
  print("\n"+"="*w); print(f"CONTENT FOR {p} ({len(c)} chars) - copy verbatim"); print("="*w); print(c,end="" if c.endswith("\n") else "\n")
 print("="*w); print("  VERDICT:",verdict); print("="*w)
 return {"GREEN":0,"AMBER":1,"RED":2}[verdict]
if __name__=="__main__": raise SystemExit(main())
