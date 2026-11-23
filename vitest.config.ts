import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // exFAT/AppleDouble metadata files can appear as ._foo.test.ts.
    exclude: ['**/node_modules/**', '**/._*'],
  },
})
