const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const {
  detectTorchConfig,
  findPython,
  getInstallFingerprint,
  getVenvPaths,
  isPythonEnvironmentReady,
  isPythonVersionSupported,
  isTorchCorrect,
} = require('./lib/python-env');
const { runSetup } = require('./lib/setup-runner');

const ROOT = path.resolve(__dirname, '..');

function runCommand(command, args, options = {}) {
  console.log(`> ${command} ${args.join(' ')}`);
  const result = spawnSync(command, args, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with code ${result.status}`);
  }
}

try {
  runSetup({
    root: ROOT,
    platform: process.platform,
    nodeExecutable: process.execPath,
    npmExecPath: process.env.npm_execpath || '',
    detectTorchConfig,
    findPython,
    getInstallFingerprint,
    getVenvPaths,
    isPythonEnvironmentReady,
    isPythonVersionSupported,
    isTorchCorrect,
    existsSync: fs.existsSync,
    readFileSync: fs.readFileSync,
    writeFileSync: fs.writeFileSync,
    removeDirectory: (directory) => fs.rmSync(directory, { recursive: true, force: false }),
    runCommand,
    log: console.log,
    now: () => new Date(),
  });
} catch (error) {
  console.error(`Setup failed: ${error.message}`);
  process.exitCode = 1;
}
