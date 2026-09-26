import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'dist-preview', 'node_modules', 'coverage'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Unused vars are an error, but a leading underscore is an explicit
      // "intentionally unused" marker (common in destructuring).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // `any` defeats the purpose of the type system, and this codebase's
      // safety arguments are type-level (claim shapes, API envelopes).
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  {
    /*
     * The static-preview harness is a build-time entry point that Vite never
     * hot-reloads, so the fast-refresh rule (which exists to keep dev HMR
     * working) has nothing to protect here. It defines a stub provider value and
     * a one-off fallback component in the same file by design.
     */
    files: ['preview/**/*.{ts,tsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
)
