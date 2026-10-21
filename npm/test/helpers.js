'use strict';

// Shared helpers for the launcher tests. Not a test file itself (`node --test`
// only picks up *.test.js), so it is never executed directly.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const NPM_DIR = path.join(__dirname, '..');
const REPO_ROOT = path.join(NPM_DIR, '..');
const BIN = path.join(NPM_DIR, 'bin', 'uwr.js');

function tempDir(prefix = 'uwr-npm-test-') {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

// Writes stub executables named after real tools. Each stub records the tool
// name plus the arguments it received as JSON on stdout (which the launcher
// inherits, exactly like the MCP server would on the JSON-RPC stream) and exits
// with $STUB_EXIT.
function makeStubDir(tools, dir = tempDir('uwr-stubs-')) {
  fs.mkdirSync(dir, { recursive: true });
  const recorder = path.join(dir, '_recorder.js');
  fs.writeFileSync(
    recorder,
    [
      "'use strict';",
      'const [tool, ...argv] = process.argv.slice(2);',
      'process.stdout.write(JSON.stringify({ tool, argv }) + "\\n");',
      'process.exit(Number(process.env.STUB_EXIT || 0));',
      '',
    ].join('\n')
  );
  for (const tool of tools) {
    const file = path.join(dir, tool);
    fs.writeFileSync(
      file,
      `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(recorder)} ${tool} "$@"\n`
    );
    fs.chmodSync(file, 0o755);
  }
  return dir;
}

// A base env with a clean PATH so the host's own uvx/uv/pipx never leak in.
function baseEnv(extra = {}) {
  const home = tempDir('uwr-home-');
  const env = {
    PATH: path.join(home, 'empty-bin'),
    HOME: home,
    XDG_CACHE_HOME: path.join(home, '.cache'),
    ...extra,
  };
  fs.mkdirSync(env.PATH, { recursive: true });
  return env;
}

function pyprojectVersion() {
  const text = fs.readFileSync(path.join(REPO_ROOT, 'pyproject.toml'), 'utf8');
  const match = text.match(/^version\s*=\s*"([^"]+)"/m);
  if (!match) throw new Error('could not find project version in pyproject.toml');
  return match[1];
}

module.exports = { BIN, NPM_DIR, REPO_ROOT, baseEnv, makeStubDir, pyprojectVersion, tempDir };
