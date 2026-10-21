'use strict';

// Documentation guards for the npm launcher: every switch the README documents in its
// "Environment switches" table must actually be read somewhere in the launcher source.
// A row nobody implements is worse than no row at all — users set it and nothing happens.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { NPM_DIR } = require('./helpers');

function readmeTableVars() {
  const readme = fs.readFileSync(path.join(NPM_DIR, 'README.md'), 'utf8');
  const start = readme.indexOf('### Environment switches');
  assert.notStrictEqual(start, -1, 'README.md must keep an "### Environment switches" section');
  const rest = readme.slice(start + '### Environment switches'.length);
  const end = rest.search(/^#{2,3} /m);
  const section = end === -1 ? rest : rest.slice(0, end);
  const vars = [];
  for (const line of section.split('\n')) {
    const match = line.match(/^\|\s*`([A-Z][A-Z0-9_]*)(?:=[^`]*)?`\s*\|/);
    if (match) vars.push(match[1]);
  }
  return vars;
}

function launcherSources() {
  const files = [
    path.join(NPM_DIR, 'bin', 'uwr.js'),
    ...fs.readdirSync(path.join(NPM_DIR, 'lib')).map((f) => path.join(NPM_DIR, 'lib', f)),
  ];
  return files.map((f) => fs.readFileSync(f, 'utf8')).join('\n');
}

test('every environment switch in the README table is read by the launcher', () => {
  const vars = readmeTableVars();
  assert.ok(vars.length >= 3, `expected to parse the README table, got: ${vars.join(', ')}`);
  const sources = launcherSources();
  const unimplemented = vars.filter((name) => !sources.includes(name));
  assert.deepStrictEqual(
    unimplemented,
    [],
    `README documents switches the launcher never reads: ${unimplemented.join(', ')}. ` +
      'Implement them or delete the rows (a documented no-op wastes hours of debugging).'
  );
});
