/**
 * Post-build regression guard for the Tailwind v3 -> v4 CSS-first migration.
 * Ensures both the functional utility layer and SecretaryAI custom theme
 * survive code generation. This is NOT a screenshot/visual-signoff substitute.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const assets = join(process.cwd(), 'dist', 'assets');
const cssFiles = readdirSync(assets).filter((name) => name.endsWith('.css'));
if (cssFiles.length === 0) {
  throw new Error('No compiled CSS artifacts found in dist/assets');
}
const compiled = cssFiles.map((file) => readFileSync(join(assets, file), 'utf8')).join('\n');

// These selectors are used in the actual JSX source; absence silently
// produces an unstyled, superficially "successful" Vite build.
const requiredSelectors = [
  '.flex', '.bg-slate-50', '.text-gray-900',
  '.shadow-card', '.animate-fade-in', '.animate-slide-down',
];
const requiredThemeValues = [
  '--color-brand-600', '--shadow-card-md',
  '--animate-fade-in', '--animate-slide-down',
];
for (const selector of requiredSelectors) {
  if (!compiled.includes(selector)) {
    throw new Error('Missing generated Tailwind utility: ' + selector);
  }
}
for (const token of requiredThemeValues) {
  if (!compiled.includes(token)) {
    throw new Error('Missing migrated SecretaryAI theme token: ' + token);
  }
}
if (compiled.includes('@tailwind base;') || compiled.includes('@tailwind utilities;')) {
  throw new Error('Uncompiled Tailwind v3 directives leaked into output');
}
console.log('PASS: Tailwind v4 utilities and SecretaryAI brand/motion tokens in generated CSS');
