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
  return execSync(cmd + ' ' + args.join(' '), { ...opts, stdio: 'inherit', shell: true });
}

async function main() {
  log('🚀 Starting HiImage development mode...');
  log('📁 Project root: ' + ROOT);

  // --- Python 检测 ---
  log('🐍 Detecting Python...');
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

  if (!fs.existsSync(venvPath)) {
    log('  Creating virtual environment...');
    runSync(python, ['-m', 'venv', venvPath]);
  }

  // --- 安装后端依赖 ---
  log('  Installing backend dependencies...');
  const pip = process.platform === 'win32'
    ? path.join(venvPath, 'Scripts', 'pip.exe')
    : path.join(venvPath, 'bin', 'pip');
  const reqFile = path.join(ROOT, 'backend', 'requirements.txt');
  runSync(pip, ['install', '-r', reqFile]);

  // --- 启动后端 ---
  log('⚡ Starting Backend (FastAPI on port 8787)...');
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
  log('🖥️  Starting Frontend...');
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
