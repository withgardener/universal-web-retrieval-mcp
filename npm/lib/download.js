'use strict';

// Downloads the official Astral uv standalone build, verifies its sha256 from
// lib/uv-pin.json, then unpacks it into the per-user cache. No npm deps: only
// node built-ins plus the system `tar`.

const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

const { PIN, resolveAsset, uvBinaryName } = require('./platform');
const { cacheRoot, isExecutableFile, isUsableUvBinary } = require('./runner');
const VERSION = require('../package.json').version;

const REDIRECTS = new Set([301, 302, 303, 307, 308]);

function request(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return reject(new Error(`invalid download URL: ${url}`));
    }
    const mod = parsed.protocol === 'https:' ? https : http;
    const req = mod.get(
      url,
      { headers: { 'user-agent': `uwr-npm/${VERSION}` } },
      (res) => {
        const status = res.statusCode || 0;
        if (REDIRECTS.has(status) && res.headers.location) {
          res.resume();
          if (redirects <= 0) return reject(new Error(`too many redirects for ${url}`));
          const next = new URL(res.headers.location, url).toString();
          // Never follow an https -> http downgrade (the sha256 check would still catch tampering,
          // but there is no reason to fetch the archive in clear text).
          if (parsed.protocol === 'https:' && !next.startsWith('https:')) {
            return reject(new Error(`refusing insecure redirect from ${url} to ${next}`));
          }
          return resolve(request(next, redirects - 1));
        }
        if (status !== 200) {
          res.resume();
          return reject(new Error(`download failed: HTTP ${status} for ${url}`));
        }
        resolve(res);
      }
    );
    req.on('error', reject);
    req.setTimeout(120000, () => req.destroy(new Error(`download timed out: ${url}`)));
  });
}

function downloadTo(url, file) {
  return request(url).then(
    (res) =>
      new Promise((resolve, reject) => {
        const hash = crypto.createHash('sha256');
        const out = fs.createWriteStream(file);
        res.on('data', (chunk) => hash.update(chunk));
        res.on('error', reject);
        out.on('error', reject);
        out.on('finish', () => resolve(hash.digest('hex')));
        res.pipe(out);
      })
  );
}

function removeQuietly(file) {
  try {
    fs.rmSync(file, { force: true, recursive: true });
  } catch {
    /* best effort */
  }
}

function assetStem(asset) {
  return asset.replace(/\.(tar\.gz|tgz|zip)$/i, '');
}

function extractArchive(archive, destDir, platform) {
  // --no-same-owner: as root, GNU tar otherwise tries to chown to the archive's uid/gid (1001 for
  // uv's CI builds) and fails on overlay/rootless filesystems, which is common in Docker and CI.
  const owner = platform === 'win32' ? [] : ['--no-same-owner'];
  const args = /\.zip$/i.test(archive)
    ? ['-xf', archive, '-C', destDir]
    : ['-xzf', archive, ...owner, '-C', destDir];
  const result = spawnSync('tar', args, { stdio: ['ignore', 'ignore', 'pipe'], encoding: 'utf8' });
  if (result.error) {
    throw new Error(`could not run \`tar\` to unpack uv: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = (result.stderr || '').trim().split('\n').slice(-3).join(' ');
    throw new Error(`\`tar\` failed to unpack ${path.basename(archive)} (exit ${result.status})${detail ? `: ${detail}` : ''}`);
  }
}

// Each run downloads into its own temp names and only renames the finished binary into place, so
// two launchers starting at the same time cannot corrupt each other's download/unpack, and a killed
// unpack can never leave a half-written `uv` that the next run would trust as cached.
function uniqueSuffix() {
  return `${process.pid}-${crypto.randomBytes(6).toString('hex')}`;
}

// Download (if needed), verify, unpack and return the absolute path of uv.
async function ensureUv(opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const arch = opts.arch || process.arch;
  const cacheDir = opts.cacheDir || cacheRoot(env, platform);
  const binary = opts.binaryPath || path.join(cacheDir, uvBinaryName(platform));

  // A cache entry too small to be a real uv is stale rubbish (interrupted download, disk-full kill,
  // older launcher). Drop it so the verified download can take its place — renaming over an existing
  // uv.exe fails on Windows, and trusting it made every later launch fail the same way.
  if (!opts.force) {
    if (isUsableUvBinary(binary, platform)) return binary;
    removeQuietly(binary);
  }

  const target = opts.target || resolveAsset(platform, arch);
  if (!target) {
    throw new Error(
      `no official uv build is published for ${platform}-${arch}; install uv or pipx manually.`
    );
  }

  const version = opts.uvVersion || PIN.version;
  const baseUrl = (opts.baseUrl || `https://github.com/astral-sh/uv/releases/download/${version}`).replace(/\/+$/, '');
  const url = `${baseUrl}/${target.asset}`;
  const id = uniqueSuffix();
  const part = path.join(cacheDir, `${target.asset}.part-${id}`);
  const staging = path.join(cacheDir, `unpack-${id}`);

  fs.mkdirSync(cacheDir, { recursive: true });

  try {
    let actual;
    try {
      actual = await downloadTo(url, part);
    } catch (err) {
      removeQuietly(part);
      throw err;
    }

    if (actual !== target.sha256) {
      throw new Error(
        `sha256 mismatch for ${target.asset}: expected ${target.sha256}, got ${actual}. ` +
          'Refusing to unpack the download (file deleted).'
      );
    }

    fs.mkdirSync(staging, { recursive: true });
    extractArchive(part, staging, platform);

    // tarballs nest the binary in `<asset-stem>/`, the Windows zip has it at the archive root.
    const staged = path.join(staging, uvBinaryName(platform));
    const nested = path.join(staging, assetStem(target.asset), uvBinaryName(platform));
    const source = [staged, nested].find((candidate) => fs.existsSync(candidate));
    if (!source) {
      throw new Error(`uv archive ${target.asset} did not contain a \`${uvBinaryName(platform)}\` binary.`);
    }
    if (platform !== 'win32') fs.chmodSync(source, 0o755);

    try {
      fs.renameSync(source, binary);
    } catch (err) {
      // A concurrent launcher may have won the race and be running the binary; its copy is the same
      // verified build, so treat that as success instead of failing the second launch.
      if (!isExecutableFile(binary, platform)) throw err;
    }
  } finally {
    removeQuietly(part);
    removeQuietly(staging);
  }

  return binary;
}

function installHints() {
  return [
    'uwr: no way to run the Python package was found.',
    '  Install uv (recommended):  curl -LsSf https://astral.sh/uv/install.sh | sh',
    '  or pipx:                   pipx install uwr',
    '  or pip:                    pip install uwr',
    '',
  ].join('\n');
}

function noDownloadHint() {
  return [
    'uwr: uv/uvx/pipx were not found on PATH and UWR_NPM_NO_DOWNLOAD=1 forbids downloading uv.',
    '  Install uv:    curl -LsSf https://astral.sh/uv/install.sh | sh',
    '  or pipx:       pipx install uwr',
    '  or pip:        pip install uwr',
    '  Unset UWR_NPM_NO_DOWNLOAD to let the launcher fetch uv automatically.',
    '',
  ].join('\n');
}

module.exports = {
  ensureUv,
  installHints,
  noDownloadHint,
  request,
  downloadTo,
  assetStem,
  extractArchive,
  PIN,
  VERSION,
};
