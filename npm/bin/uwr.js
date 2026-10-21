#!/usr/bin/env node
'use strict';

// Thin entry point: all logic lives in lib/ so it can be unit-tested.
// stdout is reserved for the MCP JSON-RPC stream (and --launcher-info).

const { run } = require('../lib/launch');

run(process.argv.slice(2)).then(
  (code) => {
    if (typeof code === 'number') process.exit(code);
  },
  (err) => {
    process.stderr.write(`uwr: ${err && err.message ? err.message : String(err)}\n`);
    process.exit(1);
  }
);
