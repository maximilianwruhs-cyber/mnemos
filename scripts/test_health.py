#!/usr/bin/env python3
"""Regression tests for MNEMOS health-v2 support components."""
from __future__ import annotations
import importlib.util,tempfile,unittest
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
if __name__=='__main__': unittest.main(verbosity=2)
