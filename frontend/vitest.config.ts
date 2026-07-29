import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom', // provides localStorage + window
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
