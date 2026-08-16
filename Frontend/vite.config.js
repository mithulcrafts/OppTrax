import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

function normalizeBasePath(basePath) {
  const fallbackBasePath = '/OppTrax/'
  const resolvedBasePath = basePath || fallbackBasePath
  const withLeadingSlash = resolvedBasePath.startsWith('/') ? resolvedBasePath : `/${resolvedBasePath}`
  return withLeadingSlash.endsWith('/') ? withLeadingSlash : `${withLeadingSlash}/`
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react()],
    base: normalizeBasePath(env.VITE_BASE_PATH),
  }
})