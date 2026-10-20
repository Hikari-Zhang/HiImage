const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');

const {
  buildInstallFingerprint,
  findPython,
  getVenvPaths,
  isPythonEnvironmentReady,
  parseCudaVersion,
  parsePythonVersion,
  selectTorchConfig,
} = require('../lib/python-env');

test('isPythonEnvironmentReady probes torch, FastAPI and Uvicorn together', () => {
  let probeArgs;
  const ready = isPythonEnvironmentReady(
    'python',
    { cuTag: 'cpu' },
    (_command, args) => {
      probeArgs = args;
      return { status: 0, stdout: '3.12.10|2.7.0+cpu\n' };
    }
  );

  assert.equal(ready, true);
  assert.match(probeArgs[1], /fastapi, torch, uvicorn/);
  assert.match(probeArgs[1], /sys\.version_info/);
});

test('isPythonEnvironmentReady rejects a Python 3.9 virtual environment', () => {
  assert.equal(isPythonEnvironmentReady(
    'python',
    { cuTag: 'cpu' },
    () => ({ status: 0, stdout: '3.9.19|2.7.0+cpu\n' })
  ), false);
});

test('isPythonEnvironmentReady rejects a failed dependency import probe', () => {
  assert.equal(isPythonEnvironmentReady(
    'python',
    { cuTag: 'cpu' },
    () => ({ status: 1, stdout: '' })
  ), false);
});

test('parseCudaVersion extracts CUDA version from nvidia-smi output', () => {
  assert.equal(parseCudaVersion('Driver Version: 572.83  CUDA Version: 12.8'), 12.8);
  assert.equal(parseCudaVersion('NVIDIA-SMI has no CUDA field'), null);
});

test('parsePythonVersion accepts supported versions and rejects malformed output', () => {
  assert.deepEqual(parsePythonVersion('Python 3.10.14'), { major: 3, minor: 10, patch: 14 });
  assert.deepEqual(parsePythonVersion('Python 3.12.0'), { major: 3, minor: 12, patch: 0 });
  assert.equal(parsePythonVersion('not python'), null);
});

test('findPython requests Python 3.10+ explicitly and rejects Python 3.9', () => {
  const calls = [];
  const found = findPython('win32', (command, args) => {
    calls.push([command, args]);
    if (command === 'py') {
      return { status: 0, stdout: 'Python 3.9.13\n' };
    }
    if (command === 'python') {
      return { status: 0, stdout: 'Python 3.11.9\n' };
    }
    return { status: 1, stdout: '' };
  });

  assert.deepEqual(found, { command: 'python', prefixArgs: [] });
  assert.deepEqual(calls[0], ['py', ['-3.13', '--version']]);
});

test('selectTorchConfig chooses CUDA 12.8 packages for Blackwell-capable drivers', () => {
  assert.deepEqual(selectTorchConfig({ platform: 'win32', arch: 'x64', cudaVersion: 12.8 }), {
    indexUrl: 'https://download.pytorch.org/whl/cu128',
    torchSpec: 'torch==2.7.0',
    cuTag: 'cu128',
    label: 'CUDA 12.8 → cu128 (RTX 50系 / Blackwell)',
  });
});

test('selectTorchConfig chooses MPS-compatible default packages on Apple Silicon', () => {
  assert.deepEqual(selectTorchConfig({ platform: 'darwin', arch: 'arm64', cudaVersion: null }), {
    indexUrl: null,
    torchSpec: 'torch',
    cuTag: null,
    label: 'macOS Apple Silicon (MPS)',
  });
});

test('selectTorchConfig falls back to CPU packages without supported CUDA', () => {
  assert.equal(selectTorchConfig({ platform: 'linux', arch: 'x64', cudaVersion: 11.7 }).cuTag, 'cpu');
  assert.equal(selectTorchConfig({ platform: 'linux', arch: 'x64', cudaVersion: null }).cuTag, 'cpu');
});

test('getVenvPaths returns platform-specific executable paths', () => {
  const root = path.resolve('project');
  assert.deepEqual(getVenvPaths(root, 'win32'), {
    venvPath: path.join(root, 'venv'),
    python: path.join(root, 'venv', 'Scripts', 'python.exe'),
    pip: path.join(root, 'venv', 'Scripts', 'pip.exe'),
  });
  assert.equal(getVenvPaths(root, 'linux').python, path.join(root, 'venv', 'bin', 'python'));
});

test('buildInstallFingerprint is stable and changes with requirements or torch target', () => {
  const base = buildInstallFingerprint({
    requirements: 'fastapi==1',
    postInstall: 'patch',
    torchConfig: { torchSpec: 'torch', indexUrl: null, cuTag: null },
    setupVersion: 1,
  });
  const same = buildInstallFingerprint({
    setupVersion: 1,
    torchConfig: { cuTag: null, indexUrl: null, torchSpec: 'torch' },
    postInstall: 'patch',
    requirements: 'fastapi==1',
  });
  const changed = buildInstallFingerprint({
    requirements: 'fastapi==2',
    postInstall: 'patch',
    torchConfig: { torchSpec: 'torch', indexUrl: null, cuTag: null },
    setupVersion: 1,
  });

  assert.equal(base, same);
  assert.notEqual(base, changed);
});
