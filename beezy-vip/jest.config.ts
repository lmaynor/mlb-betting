export default {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  moduleNameMapper: { '^@/(.*)$': '<rootDir>/$1' },
  transform: {
    '^.+\\.(ts|tsx)$': ['ts-jest', { tsconfig: './tsconfig.json' }],
  },
  testMatch: ['**/tests/**/*.test.tsx', '**/tests/**/*.test.ts'],
}
