import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { buildContentSecurityPolicy } from './src/cspPolicy.ts'

function cspMetaPlugin(apiHost: string | undefined) {
  return {
    name: 'csp-meta',
    transformIndexHtml: {
      order: 'post' as const,
      handler(html: string, ctx: { server?: unknown }) {
        // Dev server HMR needs inline scripts; only stamp CSP on production builds.
        if (ctx.server) {
          return html
        }
        const csp = buildContentSecurityPolicy(apiHost, {
          frameAncestors: false,
        })
        if (html.includes('http-equiv="Content-Security-Policy"')) {
          return html
        }
        return html.replace(
          '<head>',
          `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />`,
        )
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiHost = env.VITE_API_HOST || process.env.VITE_API_HOST
  return {
    plugins: [react(), tailwindcss(), cspMetaPlugin(apiHost)],
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
