/**
 * Purpose: Assertion script for src/logisticsSections.ts (the pure helpers that
 *   merge "Research with Claude" results into the shore-logistics textarea).
 * Version: v2 (2026-09-22) - added removeSection() checks.
 * Scope: STANDALONE UTILITY - not imported by the app, not part of the build
 *   (lives outside src/ so tsc -b ignores it); safe to delete.
 * Architecture: The frontend has no test runner, so this runs directly under
 *   Node's built-in TypeScript support: `node checks/logisticsSections.check.ts`
 *   from  Prints a line and exits 0 when everything holds.
 */
import assert from 'node:assert/strict'
import {
  allSections,
  removeSection,
  replaceOrAppend,
  sectionFor,
  splitPorts,
} from '../src/logisticsSections.ts'
const md = '# Shore logistics — draft\n\nintro\n\n### A\n\n- **Fuel:** x\n\n### B\n\n- **Fuel:** y\n'
assert.equal(sectionFor(md, 'a'), '### A\n\n- **Fuel:** x')
assert.equal(sectionFor(md, 'B'), '### B\n\n- **Fuel:** y')
assert.equal(sectionFor(md, 'C'), null)
assert.deepEqual(Object.keys(allSections(md)), ['A', 'B'])
// replace a middle section: others untouched, one blank line kept
assert.equal(replaceOrAppend(md, 'A', '### A\n\n- **Fuel:** NEW\n'),
  '# Shore logistics — draft\n\nintro\n\n### A\n\n- **Fuel:** NEW\n\n### B\n\n- **Fuel:** y\n')
// replace the last section
assert.equal(replaceOrAppend(md, 'B', '### B\n- **Fuel:** NEW'),
  '# Shore logistics — draft\n\nintro\n\n### A\n\n- **Fuel:** x\n\n### B\n- **Fuel:** NEW\n')
// append when missing, and into empty text
assert.equal(replaceOrAppend(md, 'C', '### C\n- **Fuel:** z'), md.trimEnd() + '\n\n### C\n- **Fuel:** z\n')
assert.equal(replaceOrAppend('', 'C', '### C\n- z\n'), '### C\n- z\n')
// idempotent
const once = replaceOrAppend(md, 'A', '### A\n- **Fuel:** NEW')
assert.equal(replaceOrAppend(once, 'A', '### A\n- **Fuel:** NEW'), once)
// CRLF text (Windows textareas can carry it)
assert.equal(sectionFor('### A\r\n- x\r\n### B\r\n- y', 'A'), '### A\r\n- x')
// port lists: commas by default, newline/semicolon when present (picked names have commas)
assert.deepEqual(splitPorts('West End, George Town'), ['West End', 'George Town'])
assert.deepEqual(
  splitPorts('Tyrell Bay, Carriacou (Grenada)\nNassau, New Providence (Bahamas)'),
  ['Tyrell Bay, Carriacou (Grenada)', 'Nassau, New Providence (Bahamas)'],
)
assert.deepEqual(splitPorts('A; B ;A\n'), ['A', 'B'])
// one picked stop whose label has a comma stays whole when newline-terminated
assert.deepEqual(splitPorts('Salt Pond (Thompson Bay), Long Island (Bahamas)\n'), [
  'Salt Pond (Thompson Bay), Long Island (Bahamas)',
])
assert.deepEqual(splitPorts(' , '), [])
// removeSection: middle, last, only section, and a no-op for an absent port
assert.equal(removeSection(md, 'A'),
  '# Shore logistics — draft\n\nintro\n\n### B\n\n- **Fuel:** y\n')
assert.equal(removeSection(md, 'B'),
  '# Shore logistics — draft\n\nintro\n\n### A\n\n- **Fuel:** x\n')
assert.equal(removeSection('### A\n- x\n', 'A'), '')
assert.equal(removeSection(md, 'C'), md)
console.log('sections: all checks passed')
