const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

function log(msg) {
  console.log(msg);
}

function findPython() {
  // Windows 常见路径
  const winPaths = [
    'C:\\Python312\\python.exe',
    'C:\\Python311\\python.exe',
    'C:\\Python310\\python.exe',
    'C:\\Python39\\python.exe',
  ];
  for (const p of winPaths) {
    if (fs.existsSync(p)) return p;
  }
  // py launcher
  try {
    execSync('py -3 --version', { stdio: 'pipe' });
    return 'py -3';
  } catch {}
  // PATH 里的 python
  for (const cmd of ['python.exe', 'python3.exe', 'python']) {
    try {
      execSync(`${cmd} --version`, { stdio: 'pipe' });
      return cmd;
    } catch {}
  }
  return null;
}

function run(cmd, args, opts) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { ...opts, shell: true, stdio: 'inherit' });
    child.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited ${code}`))));
    child.on('error', reject);
  });
}

function runSync(cmd, args, opts) {
  const safeArgs = args.map(a => (a.includes('<') || a.includes('>') || a.includes(' ')) ? `"${a}"` : a);
  return execSync(cmd + ' ' + safeArgs.join(' '), { ...opts, stdio: 'inherit', shell: true });
}

/**
 * 检测当前平台应该安装哪个版本的 PyTorch。
 * 返回 { indexUrl, torchSpec, label }
 *   indexUrl  : pip --index-url 参数，null 表示用默认源（macOS）
 *   torchSpec : pip install 的 torch 包名，如 "torch==2.7.0" 或 "torch"
 *   cuTag     : 期望版本字符串中包含的标签，如 "cu128"、"cpu"，null 表示不检查
 *   label     : 供日志显示的描述
 */
function detectTorchConfig() {
  // macOS → 标准 pip（Apple Silicon 走 MPS，Intel Mac 走 CPU，torch 自动处理）
  if (process.platform === 'darwin') {
    const arch = process.arch === 'arm64' ? 'Apple Silicon (MPS)' : 'Intel (CPU)';
    return { indexUrl: null, torchSpec: 'torch', cuTag: null, label: `macOS ${arch}` };
  }

  // Windows / Linux：尝试用 nvidia-smi 检测 CUDA 版本
  try {
    const out = execSync('nvidia-smi', { stdio: 'pipe', encoding: 'utf8' });
    const match = out.match(/CUDA Version:\s*([\d.]+)/);
    if (match) {
      const cuda = parseFloat(match[1]);
      log(`   nvidia-smi detected CUDA ${match[1]}`);

      if (cuda >= 12.8) {
        // Blackwell (RTX 50 系) 需要 cu128 + torch 2.7.0+
        return {
          indexUrl: 'https://download.pytorch.org/whl/cu128',
          torchSpec: 'torch==2.7.0',
          cuTag: 'cu128',
          label: `CUDA ${match[1]} → cu128 (RTX 50系 / Blackwell)`,
        };
      } else if (cuda >= 12.0) {
        return {
          indexUrl: 'https://download.pytorch.org/whl/cu124',
          torchSpec: 'torch',
          cuTag: 'cu124',
          label: `CUDA ${match[1]} → cu124`,
        };
      } else if (cuda >= 11.8) {
        return {
          indexUrl: 'https://download.pytorch.org/whl/cu118',
          torchSpec: 'torch',
          cuTag: 'cu118',
          label: `CUDA ${match[1]} → cu118`,
        };
      }
    }
  } catch {
    // nvidia-smi 不存在或执行失败 → 没有 NVIDIA GPU
  }

  // 没有检测到 GPU → CPU only
  return {
    indexUrl: 'https://download.pytorch.org/whl/cpu',
    torchSpec: 'torch',
    cuTag: 'cpu',
    label: 'CPU only (no GPU detected)',
  };
}

/**
 * 检查 venv 中已安装的 torch 是否符合目标平台。
 * 返回 true 表示已正确安装，无需重装；false 表示需要安装/重装。
 */
function isTorchCorrect(venvPython, config) {
  try {
    const version = execSync(
      `"${venvPython}" -c "import torch; print(torch.__version__)"`,
      { stdio: 'pipe', encoding: 'utf8' }
    ).trim();

    if (!config.cuTag) {
      // macOS：只要能 import 就行
      log(`   torch already installed: ${version}`);
      return true;
    }

    if (version.includes(`+${config.cuTag}`)) {
      log(`   torch already correct: ${version}`);
      return true;
    }

    log(`   torch version mismatch: installed=${version}, need +${config.cuTag}`);
    return false;
  } catch {
    log('   torch not installed in venv');
    return false;
  }
}

/**
 * 安装适合当前平台的 PyTorch。
 */
function installTorch(pip, config) {
  const args = [
    'install',
    config.torchSpec,
    'torchvision',
    'torchaudio',
    '--force-reinstall',
  ];
  if (config.indexUrl) {
    args.push('--index-url', config.indexUrl);
  }
  log(`   pip ${args.join(' ')}`);
  runSync(pip, args);
}

async function main() {
  log('🚀 Starting HiImage development mode...');
  log('📁 Project root: ' + ROOT);

  // --- Python 检测 ---
  log('\n🐍 Detecting Python...');
  const python = findPython();
  if (!python) {
    log('❌ Python not found! Install Python 3.10+ and add to PATH.');
    process.exit(1);
  }
  const version = execSync(`${python} --version`, { encoding: 'utf8' }).trim();
  log('   Using: ' + version);

  // --- 创建 venv ---
  const venvPath = path.join(ROOT, 'venv');
  const venvPython = process.platform === 'win32'
    ? path.join(venvPath, 'Scripts', 'python.exe')
    : path.join(venvPath, 'bin', 'python');
  const pip = process.platform === 'win32'
    ? path.join(venvPath, 'Scripts', 'pip.exe')
    : path.join(venvPath, 'bin', 'pip');

  if (!fs.existsSync(venvPath)) {
    log('\n📦 Creating virtual environment...');
    runSync(python, ['-m', 'venv', venvPath]);
  }

  // --- 检测 GPU 环境并安装对应 PyTorch ---
  log('\n🔍 Detecting GPU environment...');
  const torchConfig = detectTorchConfig();
  log(`   Target: ${torchConfig.label}`);

  if (isTorchCorrect(venvPython, torchConfig)) {
    log('   ✅ PyTorch already up-to-date, skipping.');
  } else {
    log(`\n📦 Installing PyTorch [${torchConfig.label}]...`);
    installTorch(pip, torchConfig);
  }

  // --- 安装后端依赖 ---
  log('\n📦 Installing backend dependencies...');
  const reqFile = path.join(ROOT, 'backend', 'requirements.txt');
  runSync(pip, ['install', '-r', reqFile]);

  // diffusers/transformers/huggingface-hub/peft 与 iopaint 1.6.0 存在版本冲突，
  // 需要 --no-deps 强制覆盖，绕过 pip 严格解析。
  log('\n📦 Installing FLUX-compatible packages (overriding iopaint constraints)...');
  runSync(pip, [
    'install',
    'diffusers>=0.32.0',
    'transformers>=4.47.0,<5.0',
    'huggingface-hub>=0.27.0,<1.0',
    'peft>=0.9.0',
    '--no-deps',
  ]);

  // --- post-install 补丁 ---
  log('\n🔧 Applying post-install patches...');
  runSync(venvPython, [path.join(ROOT, 'scripts', 'post_install.py')]);

  // --- 启动后端 ---
  log('\n⚡ Starting Backend (FastAPI on port 8787)...');
  const uvicorn = process.platform === 'win32'
    ? [path.join(venvPath, 'Scripts', 'python.exe'), '-m', 'uvicorn']
    : [path.join(venvPath, 'bin', 'python'), '-m', 'uvicorn'];

  const backend = spawn(uvicorn[0], [...uvicorn.slice(1), 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8787'], {
    cwd: path.join(ROOT, 'backend'),
    stdio: 'inherit',
    shell: false,
    // detached: 让后端成为进程组组长，cleanup 时可以用 -pid 杀掉整个进程树
    detached: process.platform !== 'win32',
  });
  log('   Backend PID: ' + backend.pid);

  // 等待后端就绪
  log('⏳ Waiting for backend...');
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try {
      execSync('curl -s http://127.0.0.1:8787/api/health', { stdio: 'pipe' });
      ready = true;
      log('✅ Backend ready!');
      break;
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  if (!ready) {
    log('❌ Backend failed to start');
    process.exit(1);
  }

  // --- 启动前端 ---
  log('\n🖥️  Starting Frontend...');
  // 通知 Electron 后端已由 dev.js 管理，不要重复启动
  const frontendEnv = { ...process.env, HIMAGE_BACKEND_MANAGED: '1' };
  const frontend = spawn('npm', ['run', 'dev'], {
    cwd: path.join(ROOT, 'frontend'),
    stdio: 'inherit',
    shell: true,
    env: frontendEnv,
  });
  log('   Frontend PID: ' + frontend.pid);

  // 清理子进程（杀掉整个进程树，防止 uvicorn/iopaint 子进程残留）
  let cleanedUp = false;
  const cleanup = () => {
    if (cleanedUp) return;
    cleanedUp = true;
    log('\n🛑 Shutting down...');
    if (process.platform === 'win32') {
      // Windows：taskkill /T 杀整个进程树
      try { require('child_process').execSync(`taskkill /F /T /PID ${backend.pid}`, { stdio: 'pipe' }); } catch {}
      try { require('child_process').execSync(`taskkill /F /T /PID ${frontend.pid}`, { stdio: 'pipe' }); } catch {}
    } else {
      // macOS/Linux：kill(-pid) 杀进程组
      try { process.kill(-backend.pid, 'SIGKILL'); } catch { try { backend.kill('SIGKILL'); } catch {} }
      try { process.kill(-frontend.pid, 'SIGKILL'); } catch { try { frontend.kill('SIGKILL'); } catch {} }
    }
    log('👋 Done.');
    process.exit(0);
  };
  process.on('exit', cleanup);
  process.on('SIGINT', cleanup);
  process.on('SIGTERM', cleanup);
  // Electron 前端退出后，dev.js 也自动退出（触发 cleanup）
  frontend.on('exit', () => {
    log('[dev.js] Frontend exited, shutting down backend...');
    cleanup();
  });
}

main().catch((err) => {
  log('❌ Error: ' + err.message);
  process.exit(1);
});
