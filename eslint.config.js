const js = require('@eslint/js');

module.exports = [
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: {
        L: 'readonly',
        flatpickr: 'readonly',
        Units: 'writable',
        API: 'writable',
        MapController: 'writable',
        UIController: 'writable',
        window: 'readonly',
        document: 'readonly',
        console: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        fetch: 'readonly',
        AbortController: 'readonly',
        URLSearchParams: 'readonly',
        localStorage: 'readonly',
      },
    },
    rules: {
      quotes: ['error', 'single'],
      semi: ['error', 'always'],
      'no-unused-vars': ['warn', { args: 'none' }],
      'no-console': 'off',
      'no-undef': 'error',
      'no-redeclare': 'off',
      'prefer-const': 'error',
    },
  },
  {
    ignores: ['**/*.test.js', '**/node_modules/**'],
  },
];
