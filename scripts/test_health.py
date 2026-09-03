#!/usr/bin/env python3
"""Regression tests for MNEMOS health-v2 support components."""
from __future__ import annotations
import contextlib,importlib.util,io,json,os,tempfile,unittest
from datetime import date
from pathlib import Path

TODAY=date(2026,9,3)
VALID_BODY=(
 "- **Type:** Gotcha · **Confidence:** VERIFIED · **Salience:** 0.80\n"
 "- **Created:** 2026-09-03 · **Last-Access:** 2026-09-03 · **Freq:** 1\n"
 "- **Tags:** #test #local\n"
 "- **Links:**\n"
 "- **Provenance:** Executed locally.\n"
 "- **Observation:** The behavior was observed.\n"
 "- **Directive:** Use the verified path.\n"
 '- **Evidence:** {"date":"2026-09-02","stance":"SUPPORT",'
 '"source":"health fixture","quote":"PASS"}\n'
)

def load(name):
 p=Path('/tmp')/name; s=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
pre=load('mutation_preflight.py'); scope=load('scope_manifest.py'); note=load('memory_note.py')
health=load('health.py')

class HealthV2Tests(unittest.TestCase):
 def test_preflight_projects_and_rejects_ambiguous_old_text(self):
  text='# MEMORY.md\n\n## 2. Atomic Notes\n\n## 3. Ephemeral Scratchpad\n'
  _,m=pre.project(text,'## 2. Atomic Notes','## 2. Atomic Notes\nextra')
  self.assertEqual(m['delta_bytes'],6); self.assertEqual(m['state'],'GREEN')
  with self.assertRaises(ValueError): pre.project(text,'absent','x')
 def test_scope_selects_required_and_globs(self):
  s={'required':['/MEMORY.md'],'include':['/Memory/lessons/*.md'],'exclude':[]}
  self.assertEqual(scope.select(s,['/MEMORY.md','/Memory/lessons/a.md','/x']),['/MEMORY.md','/Memory/lessons/a.md'])
 def test_scope_fails_closed_on_missing_and_collision(self):
  s={'required':['/MEMORY.md'],'include':['/Memory/lessons/*.md'],'exclude':[]}
  with tempfile.TemporaryDirectory() as d:
   manifest,missing=scope.build(s,['/MEMORY.md'],Path(d)); self.assertEqual(manifest,{}); self.assertEqual(missing,['/MEMORY.md'])
  with tempfile.TemporaryDirectory() as d:
   p=Path(d); (p/'a.md').write_text('x')
   with self.assertRaises(ValueError): scope.build({'required':['/x/a.md','/y/a.md'],'include':[],'exclude':[]},['/x/a.md','/y/a.md'],p)
 def test_note_creation_is_bidirectional(self):
  text='# MEMORY.md\n\n## 2. Atomic Notes\n\n## 3. Ephemeral Scratchpad\n'
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); mem=root/'MEMORY.md'; mem.write_text(text)
   rel=note.create(mem,root,'MEM-2026-9999','Test Note','lessons',VALID_BODY,TODAY)
   self.assertIn('# MEM-2026-9999',(root/rel).read_text()); self.assertIn(rel,mem.read_text())
 def test_health_v2_contract_is_present(self):
  src=Path('/tmp/health.py').read_text()
  for token in ('MPLCONFIGDIR','inputDigest','health-attestation.json','health-scope.json','convention lint'):
   self.assertIn(token,src)

CONTESTED_BODY=(VALID_BODY
 +'- **Evidence:** {"date":"2026-09-03","stance":"CHALLENGE",'
 '"source":"counter-probe","quote":"FAIL"}\n')

class HealthClassifierTests(unittest.TestCase):
 def test_evidence_warnings_are_classified_without_red(self):
  self.assertTrue(hasattr(health,"classified_findings"),
                  "classified_findings is not implemented")
  fails,warnings=health.classified_findings(
   "  [WARN] MEM-2026-0001 contested evidence\n")
  self.assertEqual(fails,[])
  self.assertEqual(len(warnings),1)
  self.assertTrue(hasattr(health,"cross_tier_findings"),
                  "cross_tier_findings is not implemented")
  fails,warnings,orphans=health.cross_tier_findings(
   "  [WARN] /Memory/context/x.md contested evidence\n")
  self.assertEqual((fails,len(warnings),orphans),([],1,[]))
 def test_classified_findings_captures_fail_and_warn(self):
  fails,warnings=health.classified_findings(
   "  [FAIL] a broke\n  [WARN] b contested\n  [OK  ] c fine\n")
  self.assertEqual(len(fails),1)
  self.assertEqual(len(warnings),1)
 def test_cross_tier_findings_collects_orphans(self):
  fails,warnings,orphans=health.cross_tier_findings(
   "  [FAIL] x\n  ! /Memory/lessons/z.md\n")
  self.assertEqual(len(fails),1)
  self.assertEqual(orphans,["/Memory/lessons/z.md"])

class HealthContestedMainTests(unittest.TestCase):
 def _stage(self,mem):
  d=tempfile.mkdtemp(); stage=Path(d)
  for n in ("mnemos.py","evidence.py","secretscan.py"):
   (stage/n).write_bytes((Path('/tmp')/n).read_bytes())
  (stage/"MEMORY.md").write_text(mem,encoding="utf-8")
  (stage/"AGENTS.md").write_text("# AGENTS\n\nGuide.\n",encoding="utf-8")
  manifest={"MEMORY.md":"/MEMORY.md","AGENTS.md":"/AGENTS.md",
            "mnemos.py":"/scripts/mnemos.py","evidence.py":"/scripts/evidence.py",
            "secretscan.py":"/scripts/secretscan.py"}
  (stage/"_manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
  return stage
 def test_contested_l2_note_is_amber_not_red(self):
  mem=("# MEMORY.md\n\n## 2. Atomic Notes\n\n"
       f"### [MEM-2026-0001] Title\n\n{CONTESTED_BODY}\n"
       "## 3. Ephemeral Scratchpad\n")
  stage=self._stage(mem)
  saved=health.STAGE; health.STAGE=stage
  os.environ["MNEMOS_TODAY"]=TODAY.isoformat()
  try:
   buf=io.StringIO()
   with contextlib.redirect_stdout(buf): health.main()
   out=buf.getvalue()
  finally:
   health.STAGE=saved; os.environ.pop("MNEMOS_TODAY",None)
  self.assertIn("[AMBER] L2 audit",out)
  self.assertIn("contested-evidence",out)
  self.assertNotIn("unexpected exit",out)
  red_contested=[l for l in out.splitlines()
                 if "[RED" in l and "contested" in l]
  self.assertEqual(red_contested,[])

if __name__=='__main__': unittest.main(verbosity=2)
