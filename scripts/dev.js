const fs = require('node:fs');
const path = require('node:path');
const { execFileSync, spawn, spawnSync } = require('node:child_process');
const { buildServiceSpecs, buildSetupCommand, markerMatches } = require('./lib/dev-logic');
const { runApp } = require('./lib/dev-app');
const {
  detectTorchConfig,
  getInstallFingerprint,
  getVenvPaths,
  isPythonEnvironmentReady,
} = require('./lib/python-env');

const ROOT = path.resolve(__dirname, '..');
const MARKER = path.join(ROOT, 'venv', '.hiimage-setup.json');

function hasFrontendDependencies() {
  const executable = process.platform === 'win32' ? 'vite.cmd' : 'vite';
  return fs.existsSync(path.join(ROOT, 'frontend', 'node_modules', '.bin', executable));
}

function readMarker() {
  try {
    return fs.readFileSync(MARKER, 'utf8');
  } catch {
    return '';
  }
}

function getEnvironmentStatus() {
  const torchConfig = detectTorchConfig();
  const fingerprint = getInstallFingerprint(ROOT, torchConfig);
  const { python } = getVenvPaths(ROOT);
  const pythonReady = fs.existsSync(python)
    && isPythonEnvironmentReady(python, torchConfig);
  const frontendReady = hasFrontendDependencies();
  const markerReady = markerMatches(readMarker(), fingerprint);
  return {
    ready: pythonReady && frontendReady && markerReady,
    pythonReady,
    frontendReady,
    markerReady,
    torchConfig,
  };
}

function runSetupCommand() {
  const command = buildSetupCommand(ROOT);
  const result = spawnSync(command.command, command.args, {
    cwd: command.cwd,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('Environment setup failed.');
}

function printCheck(options, specs, status) {
  console.log(`Mode: ${options.mode}`);
  console.log(`Environment: ${status.ready ? 'ready' : 'setup required'}`);
  console.log(`  Python dependencies: ${status.pythonReady ? 'ready' : 'missing/mismatched'}`);
  console.log(`  Frontend dependencies: ${status.frontendReady ? 'ready' : 'missing'}`);
  console.log(`  Install fingerprint: ${status.markerReady ? 'current' : 'missing/stale'}`);
  console.log(`  PyTorch target: ${status.torchConfig.label}`);
  if (!options.frontendOnly) {
    console.log(`Backend command: ${specs.backend.command} ${specs.backend.args.join(' ')}`);
  }
  if (!options.backendOnly) {
    console.log(`Frontend command: ${specs.frontend.command} ${specs.frontend.args.join(' ')}`);
  }
  console.log('Check complete; no services were started.');
}

async function waitForBackend(child) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before becoming ready (code ${child.exitCode}).`);
    }
    try {
      const response = await fetch('http://127.0.0.1:8787/api/health');
      if (response.ok) return;
    } catch {
      // Keep polling until the timeout.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('Backend did not become ready within 30 seconds.');
}

function spawnService(spec) {
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    stdio: 'inherit',
    shell: false,
    detached: process.platform !== 'win32',
  });
  child.on('error', (error) => {
    console.error(`Failed to start ${spec.command}: ${error.message}`);
  });
  return child;
}

function terminateTree(child) {
  if (!child || child.exitCode !== null || !child.pid) return;
  if (process.platform === 'win32') {
    try {
      execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } catch {
      // The process may already be gone.
    }
    return;
  }
  try {
    process.kill(-child.pid, 'SIGTERM');
  } catch {
    try {
      child.kill('SIGTERM');
    } catch {
      // The process may already be gone.
    }
  }
}

runApp(process.argv.slice(2), {
  root: ROOT,
  buildServiceSpecs,
  getEnvironmentStatus,
  runSetup: runSetupCommand,
  spawnService,
  waitForBackend,
  terminateTree,
  printCheck,
  existsSync: fs.existsSync,
  onSignal: (signal, handler) => process.once(signal, handler),
  setExitCode: (code) => { process.exitCode = code; },
  log: console.log,
}).catch((error) => {
  console.error(`Startup failed: ${error.message}`);
  process.exitCode = 1;
});
