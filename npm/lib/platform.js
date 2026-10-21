'use strict';

// Platform -> official Astral uv release asset. Kept in one place so the
// mapping can be unit-tested without touching the network.

const PIN = require('./uv-pin.json');

const PLATFORM_KEYS = {
  'linux-x64': 'linux-x64',
  'linux-arm64': 'linux-arm64',
  'darwin-x64': 'darwin-x64',
  'darwin-arm64': 'darwin-arm64',
  'win32-x64': 'win32-x64',
};

function platformKey(platform, arch) {
  return `${platform}-${arch}`;
}

// Returns {asset, sha256} or null when there is no official build for this host.
function resolveAsset(platform, arch) {
  const key = PLATFORM_KEYS[platformKey(platform, arch)];
  if (!key) return null;
  const entry = PIN.assets[key];
  if (!entry) return null;
  return { asset: entry.asset, sha256: entry.sha256, platform, arch };
}

function uvBinaryName(platform) {
  return platform === 'win32' ? 'uv.exe' : 'uv';
}

function isWindows(platform) {
  return platform === 'win32';
}

module.exports = { PIN, resolveAsset, uvBinaryName, isWindows, platformKey };
