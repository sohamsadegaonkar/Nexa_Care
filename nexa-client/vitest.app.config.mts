import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

const setupFile = fileURLToPath(new URL('./test/setup.ts', import.meta.url).href)

export default defineConfig({
  resolve: {
    extensions: ['.web.tsx', '.web.ts', '.web.js', '.tsx', '.ts', '.js', '.json'],
    alias: [
      { find: /^next\/script$/, replacement: 'next/script.js' },
      { find: /^next\/navigation$/, replacement: 'next/navigation.js' },
      { find: /^react-native$/, replacement: 'react-native-web' },
      {
        find: /^react-native-svg$/,
        replacement: 'react-native-svg/lib/module/index.js',
      },
      // Keep tests on react-native-svg's compiled output; the source mixin pulls React Native Flow syntax.
      {
        find: /^react-native-svg\/src\/lib\/SvgTouchableMixin(?:\.ts)?$/,
        replacement: 'react-native-svg/lib/module/lib/SvgTouchableMixin.js',
      },
    ],
  },
  esbuild: {
    target: 'es2020',
    loader: 'tsx',
  },
  test: {
    include: ['packages/app/**/*.test.ts', 'packages/app/**/*.test.tsx'],
    setupFiles: [setupFile],
    environment: 'jsdom',
    deps: {
      interopDefault: true,
    },
    server: {
      deps: {
        inline: ['@tamagui/next-theme', 'react-native-svg'],
      },
    },
  },
})
