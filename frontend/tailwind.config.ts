import type { Config } from 'tailwindcss';
export default { content: ['./app/**/*.{ts,tsx}'], theme: { extend: { colors: { ink: '#0c0e12', panel: '#13161c', line: '#262a33', muted: '#9097a5', accent: '#9ce7c1' } } }, plugins: [] } satisfies Config;
