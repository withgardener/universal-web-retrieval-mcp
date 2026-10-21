'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const pkg = require('../package.json');
const { pyprojectVersion, NPM_DIR } = require('./helpers');

test('npm launcher version equals the pyproject version', () => {
  const project = pyprojectVersion();
  assert.match(project, /^\d+\.\d+\.\d+$/);
  assert.strictEqual(pkg.version, project, 'npm/package.json version must equal pyproject [project].version');
});

test('launcher manifest is zero-dependency and npx-friendly', () => {
  assert.strictEqual(pkg.name, 'uwr');
  assert.deepStrictEqual(pkg.bin, { uwr: 'bin/uwr.js' });
  assert.strictEqual(pkg.dependencies, undefined);
  assert.strictEqual(pkg.devDependencies, undefined);
  assert.strictEqual(pkg.license, 'MIT');
  assert.strictEqual(pkg.publishConfig.access, 'public');
  assert.strictEqual(pkg.publishConfig.provenance, true);
  assert.deepStrictEqual(fs.existsSync(path.join(NPM_DIR, 'LICENSE')), true);
  assert.deepStrictEqual(fs.existsSync(path.join(NPM_DIR, 'bin', 'uwr.js')), true);
});
