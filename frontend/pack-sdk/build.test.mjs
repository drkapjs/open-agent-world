import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import test from 'node:test';
import ts from 'typescript';
import { buildPackFrontend } from './build.mjs';
import { buildPackFrontendVite } from './build-vite.mjs';
import { sourcePackArtifacts } from '../server/source-packs.mjs';
import { buildSourcePack } from '../scripts/source-packs.mjs';
import { createServer } from 'vite';
import sdkExports from './sdk-exports.mjs';
import { sdkRuntimeExports } from '../scripts/pack-sdk-exports.mjs';

async function temporaryDirectory() {
  const base = fileURLToPath(new URL('../../.tmp/', import.meta.url));
  await mkdir(base, { recursive: true });
  return mkdtemp(path.join(base, 'pack-build-'));
}

test('published runtime export list matches the complete SDK value surface', () => {
  const root = new URL('../', import.meta.url);
  const configPath = fileURLToPath(new URL('tsconfig.app.json', root));
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  const filename = fileURLToPath(new URL('src/plugins/sdk.ts', root));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configPath));
  const program = ts.createProgram([filename], parsed.options);
  assert.deepEqual(sdkExports, sdkRuntimeExports(program, filename));
});

test('build delivers shared SDK exports, CSS, relative assets and a worker without bundling React', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'entry.tsx'), `import { Settings2, CardFace, useWorkspaceNavigation } from '@oaw/plugin-api';
      import { useState } from 'react'; import './style.css'; import asset from './sample.svg?url';
      export default { apiVersion: 1, views: { body: () => <Settings2 /> }, asset, useState, CardFace, useWorkspaceNavigation };
      export const worker = () => new Worker(new URL('./workers/analysis.js', import.meta.url), {type: 'module'});`);
    await writeFile(path.join(root, 'style.css'), '.example { background-image: url("./sample.svg"); color: red; }');
    await writeFile(path.join(root, 'sample.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
    await writeFile(path.join(root, 'worker.ts'), 'self.onmessage = event => self.postMessage(event.data + 1);');
    await writeFile(path.join(root, 'package.json'), '{"type":"module"}');
    const outfile = path.join(root, 'dist/index.js');
    const result = await buildPackFrontend({ entryPoint: path.join(root, 'entry.tsx'), outfile, minify: false,
      workers: { analysis: path.join(root, 'worker.ts') } });
    const js = await readFile(outfile, 'utf8');
    assert.match(js, /oaw\.frontend\.host\.v1/);
    assert.match(js, /shared\.Settings2/);
    assert.match(js, /new URL\(.*import\.meta\.url/);
    assert.match(js, /link\.onerror/);
    assert.ok(!Object.keys(result.metafile.inputs).some(name => /node_modules[/\\]react/.test(name)));
    const manifest = JSON.parse(await readFile(path.join(root, 'dist/frontend-build.json'), 'utf8'));
    assert.equal(manifest.entry, 'index.js');
    assert.ok(manifest.files.some(file => file.path === 'index.css'));
    assert.ok(manifest.files.some(file => file.path === 'workers/analysis.js'));
    assert.ok(manifest.files.some(file => /^assets\/sample-.*\.svg$/.test(file.path)));
    for (const file of manifest.files) {
      assert.equal((await readFile(path.join(root, 'dist', file.path))).length, file.size_bytes);
      assert.match(file.sha256, /^[a-f0-9]{64}$/);
    }
    // Execute the emitted module too: shared identity and asset bases are
    // runtime contracts that text matching alone does not prove.
    const key = Symbol.for('oaw.frontend.host.v1');
    const original = globalThis[key];
    const host = { react: { useState() {} }, reactDom: {}, jsx: { jsx() {} }, sdk: { Settings2() {}, CardFace() {}, useWorkspaceNavigation() {} } };
    globalThis[key] = host;
    try {
      const module = await import(pathToFileURL(outfile).href);
      assert.equal(module.default.useState, host.react.useState);
      assert.equal(module.default.CardFace, host.sdk.CardFace);
      assert.equal(module.default.useWorkspaceNavigation, host.sdk.useWorkspaceNavigation);
      assert.ok(module.default.asset.startsWith(pathToFileURL(path.join(root, 'dist/assets/')).href));
    } finally {
      if (original === undefined) delete globalThis[key]; else globalThis[key] = original;
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('workers reject window-only shared SDK imports', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'entry.js'), 'export default {};');
    await writeFile(path.join(root, 'worker.js'), 'import { t } from "@oaw/plugin-api"; self.postMessage(t("hello"));');
    await assert.rejects(buildPackFrontend({ entryPoint: path.join(root, 'entry.js'), outfile: path.join(root, 'dist/index.js'),
      workers: { analysis: path.join(root, 'worker.js') } }), /cannot import the window host runtime/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Vite Pack artifact carries native workers and CSS and refuses private host imports', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', peerDependencies: { react: '^18' } }));
    await writeFile(path.join(root, 'index.tsx'), `import { useState } from 'react'; import './style.css';
      export const worker = () => new Worker(new URL('./worker.ts', import.meta.url), {type:'module'});
      export default {apiVersion:1, views:{}, useState};`);
    await writeFile(path.join(root, 'style.css'), '.pack-example { color: red }');
    await writeFile(path.join(root, 'worker.ts'), 'self.onmessage = event => self.postMessage(event.data + 1);');
    const artifact = await buildPackFrontendVite({ root });
    assert.ok(artifact.files.some(file => /worker.*\.js$/.test(file.path)));
    assert.ok(artifact.files.some(file => file.path.endsWith('.css')));
    assert.match(await readFile(path.join(root, 'dist/index.js'), 'utf8'), /link\.onerror|Unable to load Pack stylesheet/);
    await writeFile(path.join(root, 'index.tsx'), 'import value from "../../../frontend/src/plugins/sdk"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /source import escapes/);
    await writeFile(path.join(root, 'index.tsx'), 'import value from "undeclared-package"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('real source Pack build isolates development NODE_ENV and emits release JSX', async () => {
  const root = await temporaryDirectory();
  const previous = process.env.NODE_ENV;
  const runtimeKey = Symbol.for('oaw.frontend.host.v1');
  const previousRuntime = globalThis[runtimeKey];
  try {
    const sdk = fileURLToPath(new URL('./', import.meta.url));
    const devDependencies = { '@oaw/plugin-api': 'file:' + path.relative(root, sdk).replaceAll('\\', '/') };
    await writeFile(path.join(root, 'package.json'), JSON.stringify({
      name: 'oaw-development-build-fixture', version: '1.0.0', type: 'module',
      scripts: { build: 'node build.mjs' }, devDependencies,
    }));
    await writeFile(path.join(root, 'package-lock.json'), JSON.stringify({
      lockfileVersion: 3, packages: { '': { devDependencies } },
    }));
    await writeFile(path.join(root, 'index.tsx'), 'export default <section>shared JSX</section>;');
    await writeFile(path.join(root, 'build.mjs'), `
      import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite';
      if (process.env.NODE_ENV !== 'production') throw new Error('Pack inherited host development mode');
      await buildPackFrontendVite({});
    `);
    process.env.NODE_ENV = 'development';
    // Direct SDK callers also get release JSX without altering their own env.
    await buildPackFrontendVite({ root });
    assert.equal(process.env.NODE_ENV, 'development');
    // Exercise npm and the actual child process, unlike the injected watcher fixture.
    await buildSourcePack({ root, owner: 'test.development' });
    assert.equal(process.env.NODE_ENV, 'development');
    const output = path.join(root, 'dist/index.js');
    assert.doesNotMatch(await readFile(output, 'utf8'), /jsx-dev-runtime|jsxDEV/);
    globalThis[runtimeKey] = { jsx: { jsx: (tag, props) => ({ tag, props }) } };
    assert.deepEqual((await import(pathToFileURL(output).href)).default,
      { tag: 'section', props: { children: 'shared JSX' } });
  } finally {
    if (previous === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = previous;
    if (previousRuntime === undefined) delete globalThis[runtimeKey]; else globalThis[runtimeKey] = previousRuntime;
    await rm(root, { recursive: true, force: true });
  }
});

test('development serves the release artifact and rebuilds it on source changes', async () => {
  const root = await temporaryDirectory();
  let server;
  try {
    await writeFile(path.join(root, 'package.json'), '{"type":"module"}');
    await writeFile(path.join(root, 'index.tsx'), 'export default {value:"before-watch"};');
    const pack = { root, owner: 'test.source' };
    let builds = 0;
    server = await createServer({ root, configFile: false, server: { port: 0, host: '127.0.0.1' },
      plugins: [sourcePackArtifacts({ discover: async () => [pack], buildPack: async () => { builds++; await buildPackFrontendVite({ root }); } })] });
    await server.listen();
    const address = server.httpServer.address();
    const url = `http://127.0.0.1:${address.port}/assets/packs/test.source/index.js`;
    assert.equal(await (await fetch(url)).text(), await readFile(path.join(root, 'dist/index.js'), 'utf8'));
    // Wait until the OS watcher has registered the source, then change it.
    await new Promise(resolve => setTimeout(resolve, 150));
    await writeFile(path.join(root, 'index.tsx'), 'export default {value:"after-watch"};');
    let observed = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      if ((await (await fetch(url)).text()).includes('after-watch')) { observed = true; break; }
    }
    assert.equal(observed, true); assert.equal(builds, 2);
    assert.equal((await fetch(url.replace('index.js', '%2e%2e%2fpackage.json'))).status, 404);
  } finally { await server?.close(); await rm(root, { recursive: true, force: true }); }
});
