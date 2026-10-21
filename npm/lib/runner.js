'use strict';

// Runner resolution: decide HOW to reach the PyPI package `uwr`.
// Order (first hit wins):
//   UWR_NPM_RUNNER override -> uvx -> uv -> pipx -> auto-downloaded uv -> give up.
// Resolution never spawns anything and never hits the network; it only inspects
// PATH and the uv cache so it can be unit-tested and printed by --launcher-info.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const pkg = require('../package.json');
const { PIN, uvBinaryName } = require('./platform');

const VERSION = pkg.version;
const OVERRIDES = ['uvx', 'pipx', 'python', 'bundled-uv'];

function pathSeparator(platform) {
  return platform === 'win32' ? ';' : ':';
}

function pathValue(env, platform) {
  if (platform === 'win32') return env.PATH || env.Path || '';
  return env.PATH || '';
}

function isExecutableFile(file, platform) {
  try {
    const st = fs.statSync(file);
    if (!st.isFile()) return false;
    if (platform === 'win32') return true;
    fs.accessSync(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Minimal `which`: absolute path of an executable on PATH, or null.
function findOnPath(name, opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const sep = pathSeparator(platform);
  const dirs = pathValue(env, platform).split(sep);
  const exts = platform === 'win32'
    ? (env.PATHEXT || '.EXE;.CMD;.BAT').split(';').filter(Boolean)
    : [''];

  for (const dir of dirs) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext);
      if (isExecutableFile(candidate, platform)) return candidate;
    }
  }
  return null;
}

// $XDG_CACHE_HOME/uwr-npm/uv-<ver> with the usual per-platform fallbacks.
function cacheRoot(env = process.env, platform = process.platform) {
  let base;
  if (env.XDG_CACHE_HOME) base = env.XDG_CACHE_HOME;
  else if (platform === 'win32') base = env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  else base = path.join(os.homedir(), '.cache');
  return path.join(base, 'uwr-npm', `uv-${PIN.version}`);
}

function cachedUvPath(env = process.env, platform = process.platform) {
  const bin = path.join(cacheRoot(env, platform), uvBinaryName(platform));
  return isExecutableFile(bin, platform) ? bin : null;
}

// Arguments for a given runner, WITHOUT the user's own arguments.
function baseArgs(runner, version = VERSION) {
  switch (runner) {
    case 'uvx':
      return ['--from', `uwr==${version}`, 'uwr'];
    case 'uv':
      return ['tool', 'run', '--from', `uwr==${version}`, 'uwr'];
    case 'pipx':
      return ['run', '--spec', `uwr==${version}`, 'uwr'];
    case 'python':
      // Must be the CLI module, not `universal_web_retrieval`: running the package lands on
      // __main__.py, which ignores sys.argv and silently drops every forwarded flag (so
      // `npx -y uwr --version` printed nothing and exited 0). cli.py has a __main__ guard,
      // so `-m universal_web_retrieval.cli` behaves exactly like the `uwr` console script.
      return ['-m', 'universal_web_retrieval.cli'];
    case 'bundled-uv':
      return ['tool', 'run', '--from', `uwr==${version}`, 'uwr'];
    default:
      throw new Error(`unknown runner: ${runner}`);
  }
}

function invalidOverride(value) {
  return new Error(
    `UWR_NPM_RUNNER="${value}" is not supported; use one of: ${OVERRIDES.join(', ')}.`
  );
}

function makePlan(runner, command, opts = {}) {
  const version = opts.version || VERSION;
  return {
    runner,
    label: runner,
    command,
    args: baseArgs(runner, version),
    needsDownload: false,
  };
}

// Resolve the runner. Returns null when nothing is available and no cached uv
// exists yet — the caller may then download uv (or bail out).
function resolvePlan(opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const override = (env.UWR_NPM_RUNNER || '').trim();

  if (override) {
    if (!OVERRIDES.includes(override)) throw invalidOverride(override);
    if (override === 'bundled-uv') {
      const cached = cachedUvPath(env, platform);
      if (cached) return makePlan('bundled-uv', cached, opts);
      return { runner: 'bundled-uv', label: 'bundled-uv', command: null, args: baseArgs('bundled-uv', opts.version || VERSION), needsDownload: true };
    }
    const tool = override === 'python' ? findOnPath('python3', { env, platform }) || findOnPath('python', { env, platform }) : findOnPath(override, { env, platform });
    if (!tool) {
      throw new Error(
        `UWR_NPM_RUNNER="${override}" was requested but \`${override}\` is not on PATH.`
      );
    }
    return makePlan(override, tool, opts);
  }

  for (const name of ['uvx', 'uv', 'pipx']) {
    const found = findOnPath(name, { env, platform });
    if (found) return makePlan(name, found, opts);
  }

  const cached = cachedUvPath(env, platform);
  if (cached) return makePlan('bundled-uv', cached, opts);

  return null;
}

module.exports = {
  VERSION,
  OVERRIDES,
  PIN,
  baseArgs,
  cacheRoot,
  cachedUvPath,
  findOnPath,
  isExecutableFile,
  resolvePlan,
  pathSeparator,
};
