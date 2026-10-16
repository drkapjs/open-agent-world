import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { build } from 'vite';
import autoprefixer from 'autoprefixer';
import { hostRuntimeModule } from './build.mjs';

/** The same Pack artifact is consumed by source development and archive installs.
 * Libraries are resolved from the Pack's own installed dependency tree.
 */
export async function buildPackFrontendVite({ root = process.cwd(), outDir = 'dist', plugins: additionalPlugins = [], minify = true }) {
  root = path.resolve(root); outDir = path.resolve(root, outDir);
  if (root === outDir || path.dirname(outDir) === outDir) throw new Error('Pack output must be a separate build directory');
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  const dependencies = new Set([...Object.keys(manifest.dependencies ?? {}), ...Object.keys(manifest.peerDependencies ?? {}), '@oaw/plugin-api']);
  const prefix = '\0oaw-host:';
  const boundary = (worker = false) => ({ name: 'oaw-pack-boundary', enforce: 'pre',
    async resolveId(source, importer) {
      importer = importer?.replaceAll('\\', '/');
      if (/^(react(?:\/.*)?|react-dom(?:\/.*)?|@oaw\/plugin-api)$/.test(source)) {
        if (worker) throw new Error('Pack workers cannot import host React or SDK modules');
        return prefix + source;
      }
      if (importer?.startsWith(root.replaceAll('\\', '/')) && (source.startsWith('.') || path.isAbsolute(source))) {
        const candidate = path.resolve(path.dirname(importer.split('?')[0]), source.split('?')[0]);
        if (!candidate.startsWith(root + path.sep) && candidate !== root) throw new Error(`Pack source import escapes its frontend project: ${source}`);
      }
      if (importer?.startsWith(root.replaceAll('\\', '/')) && !source.startsWith('.') && !path.isAbsolute(source) && !source.startsWith('\0')) {
        const name = source.startsWith('@') ? source.split('/').slice(0, 2).join('/') : source.split('/')[0];
        if (!dependencies.has(name)) throw new Error(`Undeclared Pack frontend dependency: ${name}`);
      }
    },
    load(id) { if (id.startsWith(prefix)) return hostRuntimeModule(id.slice(prefix.length)); },
  });
  const plugins = [boundary(), ...additionalPlugins];
  const result = await build({ root, configFile: false, publicDir: false, base: './', plugins,
    define: { 'process.env.NODE_ENV': '"production"' },
    // Artifact JSX always uses the published host ABI, including when this
    // helper is called from a process with NODE_ENV=development.
    esbuild: { jsx: 'automatic', jsxImportSource: 'react', jsxDev: false },
    css: { postcss: { plugins: [autoprefixer({ overrideBrowserslist: ['Chrome >= 109', 'Firefox >= 115', 'Safari >= 16.4'] })] } },
    resolve: { conditions: ['browser', 'module'] },
    worker: { format: 'es', plugins: () => [boundary(true)] },
    build: { outDir, emptyOutDir: true, minify, target: 'es2022', assetsInlineLimit: 0,
      rollupOptions: { input: path.join(root, 'index.tsx'), preserveEntrySignatures: 'strict',
        output: { format: 'es', entryFileNames: 'index.js', chunkFileNames: 'chunks/[name]-[hash].js', assetFileNames: 'assets/[name]-[hash][extname]' } },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(item => item.output);
  const files = [];
  for (const output of outputs) {
    const filename = path.join(outDir, output.fileName);
    let bytes = await readFile(filename);
    if (output.type === 'chunk' && output.viteMetadata?.importedCss.size) {
      const styles = [...output.viteMetadata.importedCss].map(file => './' + path.relative(path.dirname(output.fileName), file).replaceAll('\\', '/'));
      const loader = `\nif(typeof document!=="undefined")await Promise.all(${JSON.stringify(styles)}.map(relative=>{const href=new URL(relative,import.meta.url).href;if([...document.querySelectorAll('link[rel="stylesheet"]')].some(link=>link.href===href))return;return new Promise((resolve,reject)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=resolve;link.onerror=()=>{link.remove();reject(new Error('Unable to load Pack stylesheet'));};document.head.append(link);});}));\n`;
      bytes = Buffer.concat([bytes, Buffer.from(loader)]); await writeFile(filename, bytes);
    }
    files.push({ path: output.fileName, size_bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
  }
  const artifact = { api_version: 1, entry: 'index.js', files };
  await writeFile(path.join(outDir, 'frontend-build.json'), JSON.stringify(artifact, null, 2) + '\n');
  return artifact;
}
