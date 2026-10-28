import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { buildContentSecurityPolicy } from './src/cspPolicy'

function cspMetaPlugin() {
  return {
    name: 'csp-meta',
    transformIndexHtml: {
      order: 'post' as const,
      handler(html: string, ctx: { server?: unknown }) {
        // Dev server HMR needs inline scripts; only stamp CSP on production builds.
        if (ctx.server) {
          return html
        }
        const csp = buildContentSecurityPolicy(process.env.VITE_API_HOST, {
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
export default defineConfig({
  plugins: [react(), tailwindcss(), cspMetaPlugin()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
