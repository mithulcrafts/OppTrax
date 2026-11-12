import process from 'node:process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function normalizeBasePath(basePath) {
  const fallbackBasePath = '/'
  if (!basePath) {
    return fallbackBasePath
  }
  const withLeadingSlash = basePath.startsWith('/') ? basePath : `/${basePath}`
  return withLeadingSlash.endsWith('/') ? withLeadingSlash : `${withLeadingSlash}/`
}

export default defineConfig({
  base: normalizeBasePath(process.env.VITE_BASE_PATH),
  plugins: [react()],
})
