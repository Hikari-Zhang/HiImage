const assert = require('node:assert/strict');
const test = require('node:test');

const { runApp } = require('../lib/dev-app');

function createChild(pid) {
  return {
    pid,
    exitCode: null,
    once() {},
  };
}

function createEventChild(pid) {
  const handlers = {};
  return {
    pid,
    exitCode: null,
    once(event, handler) {
      handlers[event] = handler;
    },
    emit(event, value) {
      handlers[event]?.(value);
    },
  };
}

function createDependencies(overrides = {}) {
  return {
    existsSync: () => true,
    getEnvironmentStatus: () => ({
      ready: true,
      pythonReady: true,
      frontendReady: true,
      markerReady: true,
      torchConfig: { label: 'CPU' },
    }),
    log() {},
    onSignal() {},
    printCheck() {},
    runSetup() {},
    setExitCode() {},
    spawnService: () => createChild(1),
    terminateTree() {},
    waitForBackend: async () => {},
    ...overrides,
  };
}

test('--check reports status without setup or spawning services', async () => {
  let setupCalls = 0;
  let spawnCalls = 0;
  let checkCalls = 0;
  const dependencies = createDependencies({
    getEnvironmentStatus: () => ({
      ready: false,
      pythonReady: false,
      frontendReady: false,
      markerReady: false,
      torchConfig: { label: 'CPU' },
    }),
    printCheck: () => { checkCalls += 1; },
    runSetup: () => { setupCalls += 1; },
    spawnService: () => {
      spawnCalls += 1;
      return createChild(1);
    },
  });

  await runApp(['--check'], dependencies);

  assert.equal(checkCalls, 1);
  assert.equal(setupCalls, 0);
  assert.equal(spawnCalls, 0);
});

test('a current fingerprint skips setup during normal startup', async () => {
  let setupCalls = 0;
  await runApp(['--backend-only'], createDependencies({
    runSetup: () => { setupCalls += 1; },
  }));
  assert.equal(setupCalls, 0);
});

test('a stale fingerprint invokes setup through the injected command', async () => {
  let statusCalls = 0;
  let setupCalls = 0;
  const dependencies = createDependencies({
    getEnvironmentStatus: () => {
      statusCalls += 1;
      return {
        ready: statusCalls > 1,
        pythonReady: true,
        frontendReady: true,
        markerReady: statusCalls > 1,
        torchConfig: { label: 'CPU' },
      };
    },
    runSetup: () => { setupCalls += 1; },
  });

  await runApp(['--backend-only'], dependencies);
  assert.equal(setupCalls, 1);
});

test('backend health timeout terminates the started backend', async () => {
  const backend = createChild(10);
  const terminated = [];
  await assert.rejects(
    runApp([], createDependencies({
      spawnService: () => backend,
      waitForBackend: async () => { throw new Error('health timeout'); },
      terminateTree: (child) => terminated.push(child),
    })),
    /health timeout/
  );
  assert.deepEqual(terminated, [backend]);
});

test('frontend startup failure terminates the already started backend', async () => {
  const backend = createChild(20);
  const terminated = [];
  let spawnCalls = 0;
  await assert.rejects(
    runApp([], createDependencies({
      spawnService: () => {
        spawnCalls += 1;
        if (spawnCalls === 1) return backend;
        throw new Error('frontend failed');
      },
      terminateTree: (child) => terminated.push(child),
    })),
    /frontend failed/
  );
  assert.deepEqual(terminated, [backend]);
});

test('an asynchronous frontend error terminates both created child processes', async () => {
  const backend = createEventChild(30);
  const frontend = createEventChild(31);
  const terminated = [];
  let spawnCalls = 0;
  await runApp([], createDependencies({
    spawnService: () => {
      spawnCalls += 1;
      return spawnCalls === 1 ? backend : frontend;
    },
    terminateTree: (child) => terminated.push(child),
  }));

  frontend.emit('error', new Error('async frontend failure'));

  assert.deepEqual(terminated, [backend, frontend]);
});
