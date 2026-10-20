const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');

const {
  buildSetupCommand,
  buildServiceSpecs,
  markerMatches,
  parseCliArgs,
} = require('../lib/dev-logic');

test('buildSetupCommand uses the current Node executable and absolute setup path', () => {
  const root = path.resolve('project');
  assert.deepEqual(buildSetupCommand(root, 'C:\\Node\\node.exe'), {
    command: 'C:\\Node\\node.exe',
    args: [path.join(root, 'scripts', 'setup.js')],
    cwd: root,
  });
});

test('buildServiceSpecs uses reload FastAPI and standard Vite in development', () => {
  const root = path.resolve('project');
  const specs = buildServiceSpecs(root, 'dev', 'win32');

  assert.equal(specs.backend.command, path.join(root, 'venv', 'Scripts', 'python.exe'));
  assert.deepEqual(specs.backend.args, [
    '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8787', '--reload',
  ]);
  assert.deepEqual(specs.frontend, {
    command: process.execPath,
    args: [path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js')],
    cwd: path.join(root, 'frontend'),
  });
});

test('buildServiceSpecs uses non-reload FastAPI and Vite preview in production mode', () => {
  const root = path.resolve('project');
  const specs = buildServiceSpecs(root, 'preview', 'linux');

  assert.equal(specs.backend.args.includes('--reload'), false);
  assert.deepEqual(specs.frontend.args, [
    path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js'),
    'preview',
    '--host',
    '127.0.0.1',
  ]);
  assert.equal(specs.frontend.command, process.execPath);
});

test('markerMatches only accepts the current completed fingerprint', () => {
  assert.equal(markerMatches('{"fingerprint":"abc","complete":true}', 'abc'), true);
  assert.equal(markerMatches('{"fingerprint":"old","complete":true}', 'abc'), false);
  assert.equal(markerMatches('{"fingerprint":"abc","complete":false}', 'abc'), false);
  assert.equal(markerMatches('not json', 'abc'), false);
});

test('parseCliArgs supports check and individual service modes', () => {
  assert.deepEqual(parseCliArgs(['--mode', 'preview', '--check', '--backend-only']), {
    mode: 'preview',
    check: true,
    backendOnly: true,
    frontendOnly: false,
    skipSetup: false,
  });
  assert.throws(() => parseCliArgs(['--backend-only', '--frontend-only']), /cannot be combined/);
  assert.throws(() => parseCliArgs(['--mode', 'invalid']), /Unknown mode/);
});
