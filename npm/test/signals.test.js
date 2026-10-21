'use strict';

// SIGINT/SIGTERM must reach the Python server, and a child killed by a signal
// must surface as 128+n so a wrapping MCP host sees the real reason.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const { BIN, baseEnv, tempDir } = require('./helpers');

const skipWindows = process.platform === 'win32' ? 'POSIX signals only' : false;

function writeUvx(dir, body) {
  const file = path.join(dir, 'uvx');
  fs.writeFileSync(file, body);
  fs.chmodSync(file, 0o755);
}

function launch(env) {
  const child = spawn(process.execPath, [BIN], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => resolve({ code, signal })));
  return { child, exited, stderr: () => stderr };
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('SIGTERM is forwarded to the server process', { skip: skipWindows }, async () => {
  const dir = tempDir('uwr-sig-');
  const mark = path.join(dir, 'mark');
  writeUvx(dir, `#!/bin/sh\ntrap 'echo got-term > "${mark}"; exit 0' TERM\nwhile :; do /bin/sleep 0.1; done\n`);
  const env = { ...baseEnv({ PATH: dir }), MARK: mark };

  const { child, exited } = launch(env);
  await delay(900);
  child.kill('SIGTERM');
  const result = await exited;

  assert.strictEqual(result.code, 0, 'child handled the signal and exited 0');
  assert.match(fs.readFileSync(mark, 'utf8'), /got-term/);
});

test('a server killed by a signal makes the launcher exit 128+n', { skip: skipWindows }, async () => {
  const dir = tempDir('uwr-sig-');
  writeUvx(dir, '#!/bin/sh\nexec /bin/sleep 30\n');
  const env = baseEnv({ PATH: dir });

  const { child, exited, stderr } = launch(env);
  await delay(900);
  child.kill('SIGTERM');
  const result = await exited;

  assert.strictEqual(result.code, 143, `expected 143 (128+SIGTERM), got ${result.code}/${result.signal}`);
  assert.strictEqual(stderr(), '');
});
