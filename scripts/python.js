const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { getVenvPaths } = require('./lib/python-env');

const root = path.resolve(__dirname, '..');
const { python } = getVenvPaths(root);
const result = spawnSync(python, process.argv.slice(2), {
  cwd: root,
  stdio: 'inherit',
  shell: false,
});

if (result.error) {
  console.error(`Unable to run venv Python: ${result.error.message}`);
  console.error('Run "npm run setup" first.');
  process.exitCode = 1;
} else {
  process.exitCode = result.status ?? 1;
}
