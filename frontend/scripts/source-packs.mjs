import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { access, mkdir, readFile, readdir, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const frontendRoot = fileURLToPath(new URL('../', import.meta.url));
const repo = path.dirname(frontendRoot);
const readJson = async file => JSON.parse(await readFile(file, 'utf8'));
const exists = async file => { try { await access(file); return true; } catch { return false; } };

export async function sourcePacks() {
  const packs = [];
  for (const parent of ['plugins', 'examples/deployed-workspace/plugins']) {
    for (const directory of await readdir(path.join(repo, parent), { withFileTypes: true })) {
      if (!directory.isDirectory()) continue;
      const root = path.join(repo, parent, directory.name, 'frontend');
      if (!await exists(path.join(root, 'plugin.json'))) continue;
      const manifest = await readJson(path.join(root, 'plugin.json'));
      if (manifest.apiVersion !== 1 || !/^[a-z][a-z0-9._-]*$/.test(manifest.pluginId)) throw new Error(`Invalid Pack frontend manifest: ${root}`);
      if (packs.some(pack => pack.owner === manifest.pluginId)) throw new Error(`Duplicate Pack frontend owner: ${manifest.pluginId}`);
      packs.push({ root, owner: manifest.pluginId });
    }
  }
  return packs;
}

async function npm(args, cwd, env = process.env) {
  let executable = process.env.npm_execpath;
  if (!executable) {
    const require = createRequire(import.meta.url);
    try { executable = require.resolve('npm/bin/npm-cli.js'); }
    catch { executable = path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'); }
  }
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [executable, ...args], { cwd, env, stdio: 'inherit', windowsHide: true });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`npm ${args.join(' ')} failed for ${cwd} (${code})`)));
  });
}

/** A checkout may reuse already installed, exact lockfile versions. These are
 * ordinary project-local links; fresh checkouts use npm ci for each Pack.
 * No host runtime dependency or resolver alias is implied by this cache.
 */
export async function preparePack(pack) {
  const manifest = await readJson(path.join(pack.root, 'package.json'));
  const lock = await readJson(path.join(pack.root, 'package-lock.json'));
  for (const group of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
    const declared = manifest[group] ?? {}, locked = lock.packages['']?.[group] ?? {};
    if (Object.keys({ ...declared, ...locked }).some(name => declared[name] !== locked[name])) {
      throw new Error(`Update the frontend package-lock.json for ${pack.owner}: ${group} changed`);
    }
  }
  const sdkReference = manifest.devDependencies?.['@oaw/plugin-api'];
  if (!sdkReference?.startsWith('file:')) throw new Error(`Source Pack must declare the checkout SDK: ${pack.owner}`);
  const sdk = path.resolve(pack.root, sdkReference.slice(5));
  const modules = path.join(pack.root, 'node_modules');
  const cache = path.join(frontendRoot, 'node_modules');
  const required = Object.entries(lock.packages).filter(([key, item]) => key.startsWith('node_modules/') && !item.link &&
    (!item.os || item.os.includes(process.platform)) && (!item.cpu || item.cpu.includes(process.arch)));
  const matching = async (base, key, item) => {
    try { return (await readJson(path.join(base, key, 'package.json'))).version === item.version; }
    catch { return false; }
  };
  if (await exists(path.join(modules, '@oaw/plugin-api/package.json')) &&
      (await Promise.all(required.map(([key, item]) => matching(pack.root, key, item)))).every(Boolean)) return;
  // Reusing a cache is optional. Never mix a partially matching cache with a
  // project install, and never alter an existing project's dependency tree.
  if (!await exists(modules) && (await Promise.all(required.map(([key, item]) => matching(frontendRoot, key, item)))).every(Boolean)) {
    await mkdir(path.join(modules, '@oaw'), { recursive: true });
    await symlink(sdk, path.join(modules, '@oaw/plugin-api'), 'junction');
    for (const [key] of required) {
      if (key.slice('node_modules/'.length).includes('/node_modules/')) continue;
      const relative = key.slice('node_modules/'.length);
      const target = path.join(modules, relative);
      await mkdir(path.dirname(target), { recursive: true });
      await symlink(path.join(cache, relative), target, 'junction');
    }
    await writeFile(path.join(modules, '.oaw-cache.json'), JSON.stringify({ sha256: createHash('sha256').update(JSON.stringify(lock)).digest('hex') }));
  } else await npm(['ci', '--ignore-scripts', '--no-audit', '--no-fund'], pack.root);
}

export async function buildSourcePack(pack) {
  await preparePack(pack);
  // Vite's development server sets NODE_ENV=development in its own process.
  // Pack artifacts use the release ABI even when served by that dev server.
  // Scope this to the build child: the host stays in development mode and npm
  // ci still installs the Pack's development/build dependencies.
  await npm(['run', 'build'], pack.root, { ...process.env, NODE_ENV: 'production' });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const pack of await sourcePacks()) await (process.argv.includes('--prepare') ? preparePack(pack) : buildSourcePack(pack));
}
