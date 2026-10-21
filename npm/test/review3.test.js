'use strict';

// Follow-ups from the third review pass: small contract gaps found by reading, each pinned here.

const test = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');

const { BIN, baseEnv, makeStubDir } = require('./helpers');
const { VERSION } = require('../lib/runner');

function runLauncher(args, env) {
  return spawnSync(process.execPath, [BIN, ...args], { env, encoding: 'utf8', timeout: 60000 });
}

test('UWR_NPM_RUNNER=uv is accepted, like the automatic order', () => {
  const dir = makeStubDir(['uv', 'uvx']);
  const result = runLauncher(['--version'], baseEnv({ PATH: dir, UWR_NPM_RUNNER: 'uv' }));
  assert.strictEqual(result.status, 0, result.stderr);
  const recorded = JSON.parse(result.stdout.trim());
  assert.strictEqual(recorded.tool, 'uv');
  assert.deepStrictEqual(recorded.argv, ['tool', 'run', '--from', `uwr==${VERSION}`, 'uwr', '--version']);
});

test('--launcher-info after `--` is forwarded to the server, not intercepted', () => {
  const dir = makeStubDir(['uvx']);
  const result = runLauncher(['--', '--launcher-info'], baseEnv({ PATH: dir }));
  assert.strictEqual(result.status, 0, result.stderr);
  const recorded = JSON.parse(result.stdout.trim());
  assert.strictEqual(recorded.tool, 'uvx');
  assert.deepStrictEqual(recorded.argv.slice(-2), ['--', '--launcher-info']);
});

test('--launcher-info explains on stderr why nothing resolved (stdout stays pure JSON)', () => {
  const dir = makeStubDir([]);
  const result = runLauncher(['--launcher-info'], baseEnv({ PATH: dir, UWR_NPM_RUNNER: 'nope' }));
  assert.strictEqual(result.status, 0);
  const info = JSON.parse(result.stdout.trim());
  assert.strictEqual(info.runner_resolved, 'none');
  assert.match(result.stderr, /UWR_NPM_RUNNER="nope" is not supported/);
});
