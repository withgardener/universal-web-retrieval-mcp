'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');

const { BIN, baseEnv, makeStubDir } = require('./helpers');
const { VERSION } = require('../lib/runner');

function runLauncher(args, env) {
  return spawnSync(process.execPath, [BIN, ...args], { env, encoding: 'utf8', timeout: 60000 });
}

test('arguments are forwarded untouched and exit codes propagate (7)', () => {
  const dir = makeStubDir(['uvx']);
  const env = baseEnv({ PATH: dir, STUB_EXIT: '7' });
  const result = runLauncher(['--version', '--foo', 'bar baz'], env);

  assert.strictEqual(result.status, 7);
  const recorded = JSON.parse(result.stdout.trim());
  assert.strictEqual(recorded.tool, 'uvx');
  assert.deepStrictEqual(recorded.argv, ['--from', `uwr==${VERSION}`, 'uwr', '--version', '--foo', 'bar baz']);
  assert.strictEqual(result.stderr, '');
});

test('a zero exit from the server is a zero exit from the launcher', () => {
  const dir = makeStubDir(['uv']);
  const env = baseEnv({ PATH: dir });
  const result = runLauncher([], env);
  assert.strictEqual(result.status, 0);
  assert.deepStrictEqual(JSON.parse(result.stdout.trim()).argv, ['tool', 'run', '--from', `uwr==${VERSION}`, 'uwr']);
});

test('--launcher-info prints JSON to stdout and never starts the server', () => {
  const dir = makeStubDir(['uvx']);
  const env = baseEnv({ PATH: dir });
  const result = runLauncher(['--launcher-info'], env);

  assert.strictEqual(result.status, 0);
  const lines = result.stdout.trim().split('\n');
  assert.strictEqual(lines.length, 1, 'only the info JSON may reach stdout');
  const info = JSON.parse(lines[0]);
  assert.deepStrictEqual(Object.keys(info).sort(), ['launcher_version', 'pinned_pypi_version', 'runner_resolved']);
  assert.strictEqual(info.launcher_version, VERSION);
  assert.strictEqual(info.pinned_pypi_version, VERSION);
  assert.strictEqual(info.runner_resolved, 'uvx');
});

test('UWR_NPM_NO_DOWNLOAD=1 exits 1 with hints on stderr and nothing on stdout', () => {
  const env = baseEnv({ UWR_NPM_NO_DOWNLOAD: '1' });
  const result = runLauncher(['--version'], env);

  assert.strictEqual(result.status, 1);
  assert.strictEqual(result.stdout, '');
  assert.match(result.stderr, /curl -LsSf https:\/\/astral\.sh\/uv\/install\.sh/);
  assert.match(result.stderr, /pipx install uwr/);
});

test('an unknown UWR_NPM_RUNNER fails with a clear stderr message', () => {
  const env = baseEnv({ UWR_NPM_RUNNER: 'cargo' });
  const result = runLauncher([], env);
  assert.strictEqual(result.status, 1);
  assert.strictEqual(result.stdout, '');
  assert.match(result.stderr, /UWR_NPM_RUNNER="cargo"/);
});
