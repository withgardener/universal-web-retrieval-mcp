'use strict';

// Orchestrates one launch: resolve a runner, download uv if allowed, spawn the
// Python MCP server with inherited stdio, forward signals and the exit code.
//
// Contract: NOTHING is ever written to stdout except the --launcher-info JSON.
// stdout belongs to the MCP JSON-RPC stream.

const os = require('node:os');
const { spawn } = require('node:child_process');

const { VERSION, PIN, baseArgs, resolvePlan } = require('./runner');
const { ensureUv, installHints, noDownloadHint } = require('./download');

const INFO_FLAG = '--launcher-info';
const FORWARDED_SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP'];

function quoteWindowsArg(arg) {
  if (arg === '') return '""';
  if (!/[\s"]/.test(arg)) return arg;
  return `"${arg.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\*)$/, '$1$1')}"`;
}

function spawnServer(plan, userArgs, io) {
  const { env, platform, stderr } = io;
  const args = plan.args.concat(userArgs);
  let command = plan.command;
  let spawnArgs = args;
  let windowsVerbatimArguments = false;

  // .cmd/.bat shims (npm/pipx on Windows) cannot be spawned directly by Node.
  if (platform === 'win32' && /\.(cmd|bat)$/i.test(command)) {
    const line = [plan.command].concat(args).map(quoteWindowsArg).join(' ');
    command = env.ComSpec || 'cmd.exe';
    spawnArgs = ['/d', '/s', '/c', line];
    windowsVerbatimArguments = true;
  }

  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, spawnArgs, {
        stdio: 'inherit',
        env,
        windowsVerbatimArguments,
      });
    } catch (err) {
      stderr.write(`uwr: failed to start ${plan.command}: ${err.message}\n`);
      return resolve(1);
    }

    const handlers = {};
    for (const signal of FORWARDED_SIGNALS) {
      handlers[signal] = () => {
        try {
          child.kill(signal);
        } catch {
          /* child already gone */
        }
      };
      process.on(signal, handlers[signal]);
    }

    const cleanup = () => {
      for (const signal of FORWARDED_SIGNALS) process.removeListener(signal, handlers[signal]);
    };

    child.on('error', (err) => {
      cleanup();
      stderr.write(`uwr: failed to start ${plan.command}: ${err.message}\n`);
      resolve(1);
    });

    child.on('exit', (code, signal) => {
      cleanup();
      if (signal) {
        const number = os.constants.signals[signal] || 0;
        return resolve(number ? 128 + number : 1);
      }
      resolve(code == null ? 1 : code);
    });
  });
}

async function run(argv, opts = {}) {
  const env = opts.env || process.env;
  const platform = opts.platform || process.platform;
  const stdout = opts.stdout || process.stdout;
  const stderr = opts.stderr || process.stderr;
  const io = { env, platform, stdout, stderr };

  if (argv.includes(INFO_FLAG)) {
    let resolved = 'none';
    try {
      const plan = resolvePlan({ env, platform });
      resolved = plan ? plan.label : 'none';
    } catch {
      resolved = 'none';
    }
    stdout.write(
      `${JSON.stringify({
        launcher_version: VERSION,
        pinned_pypi_version: VERSION,
        runner_resolved: resolved,
      })}\n`
    );
    return 0;
  }

  let plan;
  try {
    plan = resolvePlan({ env, platform });
  } catch (err) {
    stderr.write(`uwr: ${err.message}\n`);
    return 1;
  }

  if (!plan || plan.needsDownload) {
    if (env.UWR_NPM_NO_DOWNLOAD === '1') {
      stderr.write(noDownloadHint());
      return 1;
    }
    try {
      const binary = await ensureUv({ env, platform });
      plan = {
        runner: 'bundled-uv',
        label: 'bundled-uv',
        command: binary,
        args: baseArgs('bundled-uv', VERSION),
        needsDownload: false,
      };
    } catch (err) {
      stderr.write(`uwr: ${err.message}\n${installHints()}`);
      return 1;
    }
  }

  return spawnServer(plan, argv, io);
}

module.exports = { run, spawnServer, quoteWindowsArg, INFO_FLAG, FORWARDED_SIGNALS, PIN };
