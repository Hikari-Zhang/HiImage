const path = require('node:path');
const { markerMatches } = require('./dev-logic');

function getFrontendInstallCommand({ platform, nodeExecutable, npmExecPath }) {
  if (npmExecPath) {
    return {
      command: nodeExecutable,
      args: [npmExecPath, 'install'],
      shell: false,
    };
  }
  return {
    command: platform === 'win32' ? 'npm.cmd' : 'npm',
    args: ['install'],
    shell: platform === 'win32',
  };
}

function runSetup(dependencies) {
  const {
    root,
    platform,
    nodeExecutable,
    npmExecPath,
    detectTorchConfig,
    findPython,
    getInstallFingerprint,
    getVenvPaths,
    isPythonEnvironmentReady,
    isPythonVersionSupported,
    isTorchCorrect,
    existsSync,
    readFileSync,
    writeFileSync,
    removeDirectory,
    runCommand,
    log,
    now,
  } = dependencies;
  const torchConfig = detectTorchConfig();
  const fingerprint = getInstallFingerprint(root, torchConfig);
  const { venvPath, python, pip } = getVenvPaths(root, platform);
  const markerPath = path.join(venvPath, '.hiimage-setup.json');
  const viteExecutable = platform === 'win32' ? 'vite.cmd' : 'vite';
  const vitePath = path.join(root, 'frontend', 'node_modules', '.bin', viteExecutable);
  let marker = '';
  try {
    marker = readFileSync(markerPath, 'utf8');
  } catch {
    // A missing marker means setup is required.
  }

  if (
    markerMatches(marker, fingerprint)
    && existsSync(python)
    && existsSync(vitePath)
    && isPythonEnvironmentReady(python, torchConfig)
  ) {
    log('Environment fingerprint is current; setup skipped.');
    return { skipped: true };
  }

  const detectedPython = findPython(platform);
  if (!detectedPython) {
    throw new Error('Python 3.10+ not found. Install Python 3.10+ and add it to PATH.');
  }

  log(`Python environment target: ${torchConfig.label}`);
  if (existsSync(python) && !isPythonVersionSupported(python)) {
    const expectedVenvPath = path.resolve(root, 'venv');
    if (path.resolve(venvPath) !== expectedVenvPath) {
      throw new Error(`Refusing to remove unexpected virtual environment: ${venvPath}`);
    }
    log('Existing project virtual environment uses Python <3.10; rebuilding it...');
    removeDirectory(venvPath);
  }
  if (!existsSync(python)) {
    log('Creating Python virtual environment...');
    runCommand(
      detectedPython.command,
      [...detectedPython.prefixArgs, '-m', 'venv', venvPath]
    );
  }

  if (!isTorchCorrect(python, torchConfig)) {
    const args = [
      'install',
      torchConfig.torchSpec,
      'torchvision',
      'torchaudio',
      '--force-reinstall',
    ];
    if (torchConfig.indexUrl) args.push('--index-url', torchConfig.indexUrl);
    log('Installing platform-appropriate PyTorch...');
    runCommand(pip, args);
  } else {
    log('PyTorch already matches this platform; skipping reinstall.');
  }

  // Remove the retired distribution from an existing project venv before resolving.
  runCommand(pip, ['uninstall', '-y', 'iopaint']);
  log('Installing backend requirements...');
  runCommand(pip, ['install', '-r', path.join(root, 'backend', 'requirements.txt'), '-r', path.join(root, 'backend', 'requirements-dev.txt')]);

  runCommand(pip, ['check']);

  log('Applying post-install patches...');
  runCommand(python, [path.join(root, 'scripts', 'post_install.py')]);

  log('Installing frontend dependencies...');
  const npm = getFrontendInstallCommand({ platform, nodeExecutable, npmExecPath });
  runCommand(npm.command, npm.args, {
    cwd: path.join(root, 'frontend'),
    shell: npm.shell,
  });

  writeFileSync(
    markerPath,
    `${JSON.stringify({
      complete: true,
      fingerprint: getInstallFingerprint(root, torchConfig),
      torch: torchConfig.label,
      updatedAt: now().toISOString(),
    }, null, 2)}\n`
  );
  log('HiImage setup complete.');
  return { skipped: false };
}

module.exports = {
  getFrontendInstallCommand,
  runSetup,
};
