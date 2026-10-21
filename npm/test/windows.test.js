'use strict';

// Windows-only code paths, verified by reading the constructed command line (no Windows runner here).
// `cmd /d /s /c <line>` strips exactly one leading and one trailing quote from <line>, so a line whose
// first token is a quoted path ("C:\Program Files\...\x.cmd") loses its opening quote and cmd tries to
// run `C:\Program`. The whole line must be wrapped in an extra pair of quotes (cross-spawn does the
// same); this is what `npm`-style .cmd shims on PATH need.

const test = require('node:test');
const assert = require('node:assert');

const { buildInvocation } = require('../lib/launch');

const PLAN = (command, args) => ({ runner: 'pipx', label: 'pipx', command, args });

test('a .cmd shim with spaces in its path is wrapped so cmd /s /c keeps it intact', () => {
  const command = 'C:\\Users\\Jane Doe\\scoop\\shims\\pipx.cmd';
  const inv = buildInvocation(
    PLAN(command, ['run', '--spec', 'uwr==0.2.0', 'uwr']),
    ['--version'],
    'win32',
    { ComSpec: 'C:\\Windows\\system32\\cmd.exe' }
  );

  assert.strictEqual(inv.command, 'C:\\Windows\\system32\\cmd.exe');
  assert.deepStrictEqual(inv.args.slice(0, 3), ['/d', '/s', '/c']);
  assert.strictEqual(inv.windowsVerbatimArguments, true);

  const line = inv.args[3];
  assert.ok(line.startsWith('"') && line.endsWith('"'), `line must be wrapped in quotes, got: ${line}`);
  // cmd strips one leading and one trailing quote: what is left must be the exact command line.
  assert.strictEqual(
    line.slice(1, -1),
    `"${command}" run --spec uwr==0.2.0 uwr --version`
  );
});

test('a .cmd shim without spaces is wrapped the same way (single code path)', () => {
  const inv = buildInvocation(PLAN('C:\\shims\\uv.cmd', ['tool', 'run']), [], 'win32', {});
  assert.strictEqual(inv.command, 'cmd.exe', 'falls back to cmd.exe when ComSpec is unset');
  // The path needs no quoting of its own; cmd strips the wrapper pair and runs the shim with its args.
  assert.strictEqual(inv.args[3], '"C:\\shims\\uv.cmd tool run"');
});

test('an .exe or POSIX command is spawned directly, with no cmd wrapper', () => {
  const exe = buildInvocation(PLAN('C:\\tools\\uv.exe', ['tool']), ['--version'], 'win32', {});
  assert.strictEqual(exe.command, 'C:\\tools\\uv.exe');
  assert.deepStrictEqual(exe.args, ['tool', '--version']);
  assert.strictEqual(exe.windowsVerbatimArguments, false);

  const posix = buildInvocation(PLAN('/usr/bin/uvx', ['--from', 'uwr==0.2.0', 'uwr']), [], 'linux', {});
  assert.strictEqual(posix.command, '/usr/bin/uvx');
  assert.strictEqual(posix.windowsVerbatimArguments, false);
  assert.deepStrictEqual(posix.args, ['--from', 'uwr==0.2.0', 'uwr']);
});

test('arguments with spaces and quotes survive the cmd escaping', () => {
  const inv = buildInvocation(PLAN('C:\\shims\\pipx.cmd', []), ['a b', 'say "hi"', 'back\\slash\\'], 'win32', {});
  const line = inv.args[3];
  // cmd strips the outer pair; what is handed to the .cmd is the standard MSVCRT-escaped argv.
  assert.strictEqual(
    line.slice(1, -1),
    'C:\\shims\\pipx.cmd "a b" "say \\"hi\\"" back\\slash\\'
  );
  assert.ok(line.startsWith('"') && line.endsWith('"'));
});
