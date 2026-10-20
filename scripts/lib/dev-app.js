const path = require('node:path');
const { buildServiceSpecs, parseCliArgs } = require('./dev-logic');

async function runApp(argv, dependencies) {
  const {
    root,
    getEnvironmentStatus,
    runSetup,
    spawnService,
    waitForBackend,
    terminateTree,
    printCheck,
    existsSync,
    onSignal,
    setExitCode,
    log,
  } = dependencies;
  const options = (dependencies.parseCliArgs || parseCliArgs)(argv);
  const specs = (dependencies.buildServiceSpecs || buildServiceSpecs)(
    root || path.resolve(__dirname, '..', '..'),
    options.mode
  );
  const children = [];
  let shuttingDown = false;

  const shutdown = (exitCode = 0) => {
    if (shuttingDown) return;
    shuttingDown = true;
    for (const child of children) terminateTree(child);
    setExitCode(exitCode);
  };

  onSignal('SIGINT', () => shutdown(130));
  onSignal('SIGTERM', () => shutdown(143));

  const watchChild = (child, defaultExitCode) => {
    child.once('exit', (code) => {
      if (!shuttingDown) shutdown(code ?? defaultExitCode);
    });
    child.once('error', (error) => {
      log(`Service failed: ${error.message}`);
      shutdown(1);
    });
  };

  try {
    let status = getEnvironmentStatus();
    if (options.check) {
      printCheck(options, specs, status);
      return { children, shutdown };
    }

    if (!status.ready && !options.skipSetup) {
      log('Environment is missing or stale; running one-time setup...');
      runSetup();
      status = getEnvironmentStatus();
    }
    if (!status.ready) {
      throw new Error('Environment is not ready. Run "npm run setup" first.');
    }
    if (
      options.mode === 'preview'
      && !options.backendOnly
      && !existsSync(path.join(root, 'frontend', 'dist', 'index.html'))
    ) {
      throw new Error('Frontend build is missing. Run "npm run build" or use "npm run preview".');
    }

    if (!options.frontendOnly) {
      log(`Starting FastAPI (${options.mode === 'dev' ? 'reload' : 'production'} mode)...`);
      const backend = spawnService(specs.backend);
      children.push(backend);
      watchChild(backend, 1);
      await waitForBackend(backend);
      log('FastAPI is ready at http://127.0.0.1:8787');
    }

    if (!options.backendOnly) {
      log(`Starting Vite ${options.mode === 'dev' ? 'development server' : 'preview'}...`);
      const frontend = spawnService(specs.frontend);
      children.push(frontend);
      watchChild(frontend, 0);
    }
    return { children, shutdown };
  } catch (error) {
    shutdown(1);
    throw error;
  }
}

module.exports = { runApp };
