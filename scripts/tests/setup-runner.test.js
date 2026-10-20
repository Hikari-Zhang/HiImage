const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');

const {
  getFrontendInstallCommand,
  runSetup,
} = require('../lib/setup-runner');

test('Windows npm execution uses Node and npm_execpath without a command shell', () => {
  assert.deepEqual(getFrontendInstallCommand({
    platform: 'win32',
    nodeExecutable: 'C:\\Node\\node.exe',
    npmExecPath: 'C:\\Node\\node_modules\\npm\\bin\\npm-cli.js',
  }), {
    command: 'C:\\Node\\node.exe',
    args: ['C:\\Node\\node_modules\\npm\\bin\\npm-cli.js', 'install'],
    shell: false,
  });
});

test('Windows npm fallback explicitly enables the command shell', () => {
  assert.deepEqual(getFrontendInstallCommand({
    platform: 'win32',
    nodeExecutable: 'node.exe',
    npmExecPath: '',
  }), {
    command: 'npm.cmd',
    args: ['install'],
    shell: true,
  });
});

test('setup skips every install command when the completed fingerprint is current', () => {
  const root = path.resolve('project');
  const commands = [];
  const result = runSetup({
    root,
    platform: 'win32',
    nodeExecutable: 'node.exe',
    npmExecPath: 'npm-cli.js',
    detectTorchConfig: () => ({ label: 'CPU', cuTag: 'cpu' }),
    findPython: () => ({ command: 'py', prefixArgs: ['-3'] }),
    getInstallFingerprint: () => 'current',
    getVenvPaths: () => ({
      venvPath: path.join(root, 'venv'),
      python: path.join(root, 'venv', 'Scripts', 'python.exe'),
      pip: path.join(root, 'venv', 'Scripts', 'pip.exe'),
    }),
    isTorchCorrect: () => true,
    isPythonEnvironmentReady: () => true,
    existsSync: () => true,
    readFileSync: () => '{"complete":true,"fingerprint":"current"}',
    writeFileSync: () => { throw new Error('marker must not be rewritten'); },
    runCommand: (...args) => commands.push(args),
    log() {},
    now: () => new Date(0),
  });

  assert.deepEqual(result, { skipped: true });
  assert.deepEqual(commands, []);
});

test('setup installs dependencies when fingerprint is current but dependency probe fails', () => {
  const root = path.resolve('project');
  const commands = [];
  let markerWrites = 0;
  const result = runSetup({
    root,
    platform: 'win32',
    nodeExecutable: 'node.exe',
    npmExecPath: 'npm-cli.js',
    detectTorchConfig: () => ({
      label: 'CPU',
      cuTag: 'cpu',
      torchSpec: 'torch',
      indexUrl: 'https://download.pytorch.org/whl/cpu',
    }),
    findPython: () => ({ command: 'py', prefixArgs: ['-3'] }),
    getInstallFingerprint: () => 'current',
    getVenvPaths: () => ({
      venvPath: path.join(root, 'venv'),
      python: path.join(root, 'venv', 'Scripts', 'python.exe'),
      pip: path.join(root, 'venv', 'Scripts', 'pip.exe'),
    }),
    isTorchCorrect: () => true,
    isPythonVersionSupported: () => true,
    isPythonEnvironmentReady: () => false,
    existsSync: () => true,
    readFileSync: () => '{"complete":true,"fingerprint":"current"}',
    writeFileSync: () => { markerWrites += 1; },
    removeDirectory: () => { throw new Error('supported venv must not be removed'); },
    runCommand: (...args) => commands.push(args),
    log() {},
    now: () => new Date(0),
  });

  assert.deepEqual(result, { skipped: false });
  assert.ok(commands.some(([, args]) => args.some((arg) => arg.endsWith('requirements.txt'))));
  assert.ok(commands.some(([, args]) => args.some((arg) => arg.endsWith('requirements-dev.txt'))));
  assert.equal(markerWrites, 1);
});

test('setup removes and recreates an existing Python 3.9 project venv', () => {
  const root = path.resolve('project');
  const venvPath = path.join(root, 'venv');
  const commands = [];
  const removed = [];
  let venvExists = true;

  runSetup({
    root,
    platform: 'win32',
    nodeExecutable: 'node.exe',
    npmExecPath: 'npm-cli.js',
    detectTorchConfig: () => ({
      label: 'CPU',
      cuTag: 'cpu',
      torchSpec: 'torch',
      indexUrl: 'https://download.pytorch.org/whl/cpu',
    }),
    findPython: () => ({ command: 'py', prefixArgs: ['-3.12'] }),
    getInstallFingerprint: () => 'next',
    getVenvPaths: () => ({
      venvPath,
      python: path.join(venvPath, 'Scripts', 'python.exe'),
      pip: path.join(venvPath, 'Scripts', 'pip.exe'),
    }),
    isPythonEnvironmentReady: () => false,
    isPythonVersionSupported: () => false,
    isTorchCorrect: () => true,
    existsSync: (candidate) => candidate.includes('vite') || (candidate.includes('venv') && venvExists),
    readFileSync: () => '',
    writeFileSync() {},
    removeDirectory: (candidate) => {
      removed.push(candidate);
      venvExists = false;
    },
    runCommand: (...args) => commands.push(args),
    log() {},
    now: () => new Date(0),
  });

  assert.deepEqual(removed, [venvPath]);
  assert.ok(commands.some(([command, args]) =>
    command === 'py' && args.join(' ').includes('-3.12 -m venv')
  ));
});
