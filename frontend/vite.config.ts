import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { isAllowedApiOrigin } from './src/apiOrigin.ts'
import { buildContentSecurityPolicy } from './src/cspPolicy.ts'
import { escapeHtmlAttr } from './src/htmlAttr.ts'

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
          `<head>\n    <meta http-equiv="Content-Security-Policy" content="${escapeHtmlAttr(csp)}" />`,
        )
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiHost = env.VITE_API_HOST || process.env.VITE_API_HOST
  if (apiHost && apiHost.trim() && !isAllowedApiOrigin(apiHost)) {
    throw new Error(
      'Invalid VITE_API_HOST: expected an optional http(s) scheme, a hostname of letters, digits, dots or hyphens, an optional port, and an optional trailing slash.',
    )
  }
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
