import { access, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('./') || specifier.startsWith('../')) {
    try {
      const resolved = await nextResolve(specifier, context);
      return resolved;
    } catch (err) {
      if (err.code !== 'ERR_MODULE_NOT_FOUND') throw err;
      const parent = fileURLToPath(context.parentURL);
      const candidate = path.resolve(path.dirname(parent), `${specifier}.ts`);
      try { await access(candidate); return { url: new URL(`file://${candidate}`).href, shortCircuit: true }; }
      catch { throw err; }
    }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.json')) {
    const source = await readFile(fileURLToPath(url), 'utf8');
    return { format: 'module', source: `export default ${source};`, shortCircuit: true };
  }
  return nextLoad(url, context);
}
