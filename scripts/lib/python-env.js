const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');

const SETUP_VERSION = 2;
const FLUX_PACKAGES = fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'requirements-inference.txt'), 'utf8')
  .split(/\r?\n/).map((line) => line.split('#')[0].trim()).filter(Boolean);

function parseCudaVersion(output) {
  const match = String(output).match(/CUDA Version:\s*([\d.]+)/);
  return match ? Number.parseFloat(match[1]) : null;
}

function selectTorchConfig({ platform, arch, cudaVersion }) {
  if (platform === 'darwin') {
    const hardware = arch === 'arm64' ? 'Apple Silicon (MPS)' : 'Intel (CPU)';
    return {
      indexUrl: null,
      torchSpec: 'torch',
      cuTag: null,
      label: `macOS ${hardware}`,
    };
  }

  if (cudaVersion >= 12.8) {
    return {
      indexUrl: 'https://download.pytorch.org/whl/cu128',
      torchSpec: 'torch==2.7.0',
      cuTag: 'cu128',
      label: `CUDA ${cudaVersion} → cu128 (RTX 50系 / Blackwell)`,
    };
  }
  if (cudaVersion >= 12) {
    return {
      indexUrl: 'https://download.pytorch.org/whl/cu124',
      torchSpec: 'torch',
      cuTag: 'cu124',
      label: `CUDA ${cudaVersion} → cu124`,
    };
  }
  if (cudaVersion >= 11.8) {
    return {
      indexUrl: 'https://download.pytorch.org/whl/cu118',
      torchSpec: 'torch',
      cuTag: 'cu118',
      label: `CUDA ${cudaVersion} → cu118`,
    };
  }
  return {
    indexUrl: 'https://download.pytorch.org/whl/cpu',
    torchSpec: 'torch',
    cuTag: 'cpu',
    label: 'CPU only (no supported NVIDIA GPU detected)',
  };
}

function getVenvPaths(root, platform = process.platform) {
  const venvPath = path.join(root, 'venv');
  const binDir = platform === 'win32' ? 'Scripts' : 'bin';
  return {
    venvPath,
    python: path.join(venvPath, binDir, platform === 'win32' ? 'python.exe' : 'python'),
    pip: path.join(venvPath, binDir, platform === 'win32' ? 'pip.exe' : 'pip'),
  };
}

function stableSerialize(value) {
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function buildInstallFingerprint(input) {
  return crypto.createHash('sha256').update(stableSerialize(input)).digest('hex');
}

function parsePythonVersion(output) {
  const match = String(output).match(/Python\s+(\d+)\.(\d+)\.(\d+)/i);
  if (!match) return null;
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3], 10),
  };
}

function findPython(platform = process.platform, runProbe = spawnSync) {
  const candidates = platform === 'win32'
    ? [
        ['py', ['-3.13']],
        ['py', ['-3.12']],
        ['py', ['-3.11']],
        ['py', ['-3.10']],
        ['python', []],
        ['python3', []],
      ]
    : [
        ['python3', []],
        ['python', []],
      ];

  for (const [command, prefixArgs] of candidates) {
    const result = runProbe(command, [...prefixArgs, '--version'], {
      encoding: 'utf8',
      shell: false,
    });
    const version = result.status === 0
      ? parsePythonVersion(`${result.stdout || ''}\n${result.stderr || ''}`)
      : null;
    if (version && version.major === 3 && version.minor >= 10) {
      return { command, prefixArgs };
    }
  }
  return null;
}

function detectTorchConfig({ platform = process.platform, arch = process.arch } = {}) {
  let cudaVersion = null;
  if (platform !== 'darwin') {
    try {
      cudaVersion = parseCudaVersion(execFileSync('nvidia-smi', [], { encoding: 'utf8' }));
    } catch {
      // NVIDIA tooling is optional.
    }
  }
  return selectTorchConfig({ platform, arch, cudaVersion });
}

function getInstallFingerprint(root, torchConfig) {
  return buildInstallFingerprint({
    requirements: fs.readFileSync(path.join(root, 'backend', 'requirements.txt'), 'utf8'),
    developmentRequirements: fs.readFileSync(
      path.join(root, 'backend', 'requirements-dev.txt'),
      'utf8'
    ),
    postInstall: fs.readFileSync(path.join(root, 'scripts', 'post_install.py'), 'utf8'),
    frontendLock: fs.readFileSync(path.join(root, 'frontend', 'package-lock.json'), 'utf8'),
    fluxPackages: FLUX_PACKAGES,
    torchConfig: {
      torchSpec: torchConfig.torchSpec,
      indexUrl: torchConfig.indexUrl,
      cuTag: torchConfig.cuTag,
    },
    setupVersion: SETUP_VERSION,
  });
}

function isTorchCorrect(python, config) {
  const result = spawnSync(
    python,
    ['-c', 'import torch; print(torch.__version__)'],
    { encoding: 'utf8', shell: false }
  );
  if (result.status !== 0) return false;
  if (!config.cuTag) return true;
  return result.stdout.trim().includes(`+${config.cuTag}`);
}

function isPythonEnvironmentReady(python, config, runProbe = spawnSync) {
  const result = runProbe(
    python,
    [
      '-c',
        'import sys, fastapi, torch, uvicorn; '
        + 'from importlib.metadata import version; from packaging.requirements import Requirement; '
        + `requirements = [Requirement(spec) for spec in ${JSON.stringify(FLUX_PACKAGES)}]; `
        + 'assert all(version(req.name) in req.specifier for req in requirements), "Inference dependencies are incompatible"; '
      + 'print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{torch.__version__}")',
    ],
    { encoding: 'utf8', shell: false }
  );
  if (result.status !== 0) return false;
  const [pythonVersion, torchVersion = ''] = result.stdout.trim().split('|', 2);
  const parsed = parsePythonVersion(`Python ${pythonVersion}`);
  if (!parsed || parsed.major !== 3 || parsed.minor < 10) return false;
  if (!config.cuTag) return true;
  return torchVersion.includes(`+${config.cuTag}`);
}

function isPythonVersionSupported(python, runProbe = spawnSync) {
  const result = runProbe(
    python,
    ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")'],
    { encoding: 'utf8', shell: false }
  );
  if (result.status !== 0) return false;
  const parsed = parsePythonVersion(`Python ${result.stdout.trim()}`);
  return Boolean(parsed && parsed.major === 3 && parsed.minor >= 10);
}

module.exports = {
  FLUX_PACKAGES,
  SETUP_VERSION,
  buildInstallFingerprint,
  detectTorchConfig,
  findPython,
  getInstallFingerprint,
  getVenvPaths,
  isPythonEnvironmentReady,
  isPythonVersionSupported,
  isTorchCorrect,
  parseCudaVersion,
  parsePythonVersion,
  selectTorchConfig,
};
