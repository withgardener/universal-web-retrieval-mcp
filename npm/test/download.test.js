'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

const { ensureUv } = require('../lib/download');
const { resolveAsset, PIN } = require('../lib/platform');
const { tempDir } = require('./helpers');

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

// Tiny in-process server: never talks to the real network.
function serve(routes) {
  const server = http.createServer((req, res) => {
    const entry = routes[req.url];
    if (!entry) {
      res.statusCode = 404;
      return res.end('not found');
    }
    if (entry.redirect) {
      res.statusCode = 302;
      res.setHeader('location', entry.redirect);
      return res.end();
    }
    res.statusCode = 200;
    res.end(entry.body);
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({ port: server.address().port, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

function makeFakeUvArchive(dir) {
  const stem = 'uv-x86_64-unknown-linux-gnu';
  const src = path.join(dir, 'src');
  const inner = path.join(src, stem);
  fs.mkdirSync(inner, { recursive: true });
  const script = '#!/bin/sh\necho fake-uv-0\n';
  fs.writeFileSync(path.join(inner, 'uv'), script);
  fs.chmodSync(path.join(inner, 'uv'), 0o755);
  const archive = path.join(dir, `${stem}.tar.gz`);
  const result = spawnSync('tar', ['-czf', archive, '-C', src, stem], { encoding: 'utf8' });
  assert.strictEqual(result.status, 0, `tar failed: ${result.stderr}`);
  return { archive, stem, script };
}

test('every supported host maps to one pinned uv asset with a real sha256', () => {
  const expected = {
    'linux-x64': 'uv-x86_64-unknown-linux-gnu.tar.gz',
    'linux-arm64': 'uv-aarch64-unknown-linux-gnu.tar.gz',
    'darwin-x64': 'uv-x86_64-apple-darwin.tar.gz',
    'darwin-arm64': 'uv-aarch64-apple-darwin.tar.gz',
    'win32-x64': 'uv-x86_64-pc-windows-msvc.zip',
  };
  for (const [key, asset] of Object.entries(expected)) {
    const [platform, arch] = key.split('-');
    const target = resolveAsset(platform, arch);
    assert.ok(target, `no asset for ${key}`);
    assert.strictEqual(target.asset, asset);
    assert.match(target.sha256, /^[0-9a-f]{64}$/);
  }
  assert.strictEqual(resolveAsset('linux', 'ia32'), null);
  assert.strictEqual(resolveAsset('freebsd', 'x64'), null);
  assert.match(PIN.version, /^\d+\.\d+\.\d+$/);
});

test('a sha256 mismatch aborts before unpacking and deletes the partial file', async () => {
  const dir = tempDir('uwr-sha-');
  const cacheDir = path.join(dir, 'cache');
  const { archive } = makeFakeUvArchive(dir);
  const asset = path.basename(archive);
  const server = await serve({ [`/${asset}`]: { body: fs.readFileSync(archive) } });

  try {
    await assert.rejects(
      ensureUv({
        cacheDir,
        platform: 'linux',
        arch: 'x64',
        env: { XDG_CACHE_HOME: dir },
        baseUrl: `http://127.0.0.1:${server.port}`,
        target: { asset, sha256: '0'.repeat(64) },
      }),
      /sha256 mismatch/
    );
  } finally {
    await server.close();
  }

  const leftovers = fs.existsSync(cacheDir) ? fs.readdirSync(cacheDir) : [];
  assert.deepStrictEqual(leftovers, [], `cache must be empty after a bad hash, found: ${leftovers.join(', ')}`);
});

test('a verified download is unpacked and cached, following redirects', async () => {
  const dir = tempDir('uwr-ok-');
  const cacheDir = path.join(dir, 'cache');
  const { archive, stem, script } = makeFakeUvArchive(dir);
  const asset = path.basename(archive);
  const good = sha256(archive);
  const server = await serve({
    [`/${asset}`]: { redirect: `/real/${asset}` },
    [`/real/${asset}`]: { body: fs.readFileSync(archive) },
  });

  let binary;
  try {
    binary = await ensureUv({
      cacheDir,
      platform: 'linux',
      arch: 'x64',
      env: { XDG_CACHE_HOME: dir },
      baseUrl: `http://127.0.0.1:${server.port}`,
      target: { asset, sha256: good },
    });
  } finally {
    await server.close();
  }

  assert.strictEqual(binary, path.join(cacheDir, 'uv'));
  assert.strictEqual(fs.readFileSync(binary, 'utf8'), script);
  assert.ok(fs.statSync(binary).mode & 0o100, 'binary must be executable');
  assert.deepStrictEqual(fs.readdirSync(cacheDir), ['uv'], 'no archive or unpacked directory left behind');
  assert.ok(!fs.existsSync(path.join(cacheDir, stem)));
});
