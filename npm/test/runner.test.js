'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { resolvePlan, cachedUvPath, MIN_CACHED_UV_BYTES, VERSION, PIN } = require('../lib/runner');
const { baseEnv, makeStubDir, tempDir, REPO_ROOT } = require('./helpers');

const PLATFORM = 'linux';
const EXPECTED = `uwr==${VERSION}`;

test('uvx wins when uvx, uv and pipx are all on PATH', () => {
  const dir = makeStubDir(['uvx', 'uv', 'pipx']);
  const plan = resolvePlan({ env: { PATH: dir }, platform: PLATFORM });
  assert.ok(plan);
  assert.strictEqual(plan.runner, 'uvx');
  assert.strictEqual(plan.command, path.join(dir, 'uvx'));
  assert.deepStrictEqual(plan.args, ['--from', EXPECTED, 'uwr']);
  assert.strictEqual(plan.needsDownload, false);
});

test('uv is used when only uv is available', () => {
  const dir = makeStubDir(['uv']);
  const plan = resolvePlan({ env: { PATH: dir }, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'uv');
  assert.deepStrictEqual(plan.args, ['tool', 'run', '--from', EXPECTED, 'uwr']);
});

test('pipx is the last PATH-based option', () => {
  const dir = makeStubDir(['pipx']);
  const plan = resolvePlan({ env: { PATH: dir }, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'pipx');
  assert.deepStrictEqual(plan.args, ['run', '--spec', EXPECTED, 'uwr']);
});

test('nothing on PATH and no cached uv resolves to null (download path)', () => {
  const env = baseEnv();
  assert.strictEqual(resolvePlan({ env, platform: PLATFORM }), null);
});

test('a uv already present in the launcher cache is reused', () => {
  const env = baseEnv();
  const cacheDir = path.join(env.XDG_CACHE_HOME, 'uwr-npm', `uv-${PIN.version}`);
  fs.mkdirSync(cacheDir, { recursive: true });
  const binary = path.join(cacheDir, 'uv');
  // A real uv is tens of megabytes; pad past the sanity floor the launcher now enforces.
  fs.writeFileSync(binary, Buffer.alloc(MIN_CACHED_UV_BYTES, 0x20));
  fs.chmodSync(binary, 0o755);

  const plan = resolvePlan({ env, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'bundled-uv');
  assert.strictEqual(plan.command, binary);
});

test('a zero-byte or truncated uv in the cache is not trusted', () => {
  // Regression: the cache check only tested the executable bit, so a 0-byte or half-written `uv`
  // (an interrupted download from an older launcher unpacked straight into the cache) was trusted
  // forever. For a file without ELF magic libuv falls back to /bin/sh, so the launcher ran an empty
  // shell script: every `npx uwr` printed nothing and exited 0, with no way out but manual deletion.
  for (const size of [0, 4096, MIN_CACHED_UV_BYTES - 1]) {
    const env = baseEnv();
    const cacheDir = path.join(env.XDG_CACHE_HOME, 'uwr-npm', `uv-${PIN.version}`);
    fs.mkdirSync(cacheDir, { recursive: true });
    const binary = path.join(cacheDir, 'uv');
    fs.writeFileSync(binary, Buffer.alloc(size, 0x41));
    fs.chmodSync(binary, 0o755);

    assert.strictEqual(cachedUvPath(env, PLATFORM), null, `${size}-byte uv must not be trusted`);
    assert.strictEqual(
      resolvePlan({ env, platform: PLATFORM }),
      null,
      `${size}-byte uv must fall through to a fresh download`
    );
  }
});

test('UWR_NPM_RUNNER=uvx overrides a uv-first PATH', () => {
  const dir = makeStubDir(['uvx', 'uv']);
  const plan = resolvePlan({ env: { PATH: dir, UWR_NPM_RUNNER: 'uvx' }, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'uvx');
});

test('UWR_NPM_RUNNER=python runs the CLI module so forwarded arguments are honoured', () => {
  // Regression: `-m universal_web_retrieval` lands on __main__.py, which ignores sys.argv, so
  // `npx -y uwr --version` printed nothing and exited 0 (the --version flag was silently dropped).
  // The `uwr` console script points at cli:main and cli.py has a __main__ guard, so
  // `-m universal_web_retrieval.cli` behaves exactly like the console script.
  const dir = makeStubDir(['python3', 'uvx']);
  const plan = resolvePlan({ env: { PATH: dir, UWR_NPM_RUNNER: 'python' }, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'python');
  assert.deepStrictEqual(plan.args, ['-m', 'universal_web_retrieval.cli']);

  const cli = fs.readFileSync(path.join(REPO_ROOT, 'src', 'universal_web_retrieval', 'cli.py'), 'utf8');
  assert.match(cli, /if __name__ == ["']__main__["']/, 'cli.py must stay runnable through `python -m`');
  assert.match(cli, /--version/, 'the module the python runner starts must handle --version');
});

test('UWR_NPM_RUNNER=bundled-uv asks for a download when the cache is empty', () => {
  const env = baseEnv({ UWR_NPM_RUNNER: 'bundled-uv' });
  const plan = resolvePlan({ env, platform: PLATFORM });
  assert.strictEqual(plan.runner, 'bundled-uv');
  assert.strictEqual(plan.command, null);
  assert.strictEqual(plan.needsDownload, true);
  assert.deepStrictEqual(plan.args, ['tool', 'run', '--from', EXPECTED, 'uwr']);
});

test('an unknown UWR_NPM_RUNNER value is rejected', () => {
  const env = baseEnv({ UWR_NPM_RUNNER: 'deno' });
  assert.throws(() => resolvePlan({ env, platform: PLATFORM }), /UWR_NPM_RUNNER="deno"/);
});

test('an override whose tool is missing fails loudly', () => {
  const env = baseEnv({ UWR_NPM_RUNNER: 'uvx' });
  assert.throws(() => resolvePlan({ env, platform: PLATFORM }), /not on PATH/);
});

test('the cache root honours XDG_CACHE_HOME', () => {
  const xdg = tempDir('uwr-xdg-');
  const env = { XDG_CACHE_HOME: xdg, PATH: '' };
  const { cacheRoot } = require('../lib/runner');
  assert.strictEqual(cacheRoot(env, PLATFORM), path.join(xdg, 'uwr-npm', `uv-${PIN.version}`));
});
