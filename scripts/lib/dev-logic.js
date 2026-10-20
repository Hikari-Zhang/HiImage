const path = require('node:path');
const { getVenvPaths } = require('./python-env');

function buildSetupCommand(root, nodeExecutable = process.execPath) {
  return {
    command: nodeExecutable,
    args: [path.join(root, 'scripts', 'setup.js')],
    cwd: root,
  };
}

function buildServiceSpecs(root, mode, platform = process.platform) {
  const { python } = getVenvPaths(root, platform);
  const viteCli = path.join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js');
  const backendArgs = [
    '-m',
    'uvicorn',
    'app.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    '8787',
  ];
  if (mode === 'dev') backendArgs.push('--reload');

  return {
    backend: {
      command: python,
      args: backendArgs,
      cwd: path.join(root, 'backend'),
    },
    frontend: {
      command: process.execPath,
      args: mode === 'dev'
        ? [viteCli]
        : [viteCli, 'preview', '--host', '127.0.0.1'],
      cwd: path.join(root, 'frontend'),
    },
  };
}

function markerMatches(contents, fingerprint) {
  try {
    const marker = JSON.parse(contents);
    return marker.complete === true && marker.fingerprint === fingerprint;
  } catch {
    return false;
  }
}

function parseCliArgs(argv) {
  const result = {
    mode: 'dev',
    check: false,
    backendOnly: false,
    frontendOnly: false,
    skipSetup: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--mode') {
      result.mode = argv[index + 1];
      index += 1;
    } else if (arg === '--check') {
      result.check = true;
    } else if (arg === '--backend-only') {
      result.backendOnly = true;
    } else if (arg === '--frontend-only') {
      result.frontendOnly = true;
    } else if (arg === '--skip-setup') {
      result.skipSetup = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!['dev', 'preview'].includes(result.mode)) {
    throw new Error(`Unknown mode: ${result.mode}`);
  }
  if (result.backendOnly && result.frontendOnly) {
    throw new Error('--backend-only and --frontend-only cannot be combined');
  }
  return result;
}

module.exports = {
  buildSetupCommand,
  buildServiceSpecs,
  markerMatches,
  parseCliArgs,
};
