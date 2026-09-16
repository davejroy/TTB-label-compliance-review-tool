import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * Vitest configuration for the TTB Label Compliance Review Tool frontend.
 *
 * Uses happy-dom as the test environment (lighter-weight than jsdom,
 * full DOM API coverage needed by React Testing Library).
 *
 * Tests live in src/__tests__/ and match *.test.{ts,tsx}.
 * The setup file imports @testing-library/jest-dom matchers globally
 * so every test file gets `expect(el).toBeInTheDocument()` etc. for free.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/__tests__/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/vite-env.d.ts', 'src/__tests__/**'],
    },
  },
});
