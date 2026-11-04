import { defineConfig, loadEnv } from 'vite';

// Development only. The browser receives a session cookie, never OPERATOR_TOKEN.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.BACKEND_URL || 'http://127.0.0.1:8000';
  const url = new URL(target);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid BACKEND_URL');
  return {
    server: { host: '127.0.0.1', strictPort: true, proxy: { '/api': { target: url.origin, changeOrigin: true } } },
    plugins: [{
      name: 'rovr-local-session',
      configureServer(server) {
        server.middlewares.use('/__local/session', async (req, res) => {
          const deny = (status: number) => { res.statusCode = status; res.end(); };
          // No remote callers, cross-origin form submissions, request-supplied token or URL.
          const local = ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress || '');
          let sameOrigin = false;
          try { const origin = new URL(req.headers.origin || ''); sameOrigin = ['localhost', '127.0.0.1'].includes(origin.hostname) && origin.host === req.headers.host; } catch { /* deny */ }
          if (!local || !sameOrigin || req.method !== 'POST' || req.headers['x-rovr-local'] !== '1') return deny(403);
          if (!env.OPERATOR_TOKEN || env.OPERATOR_TOKEN.length < 16) return deny(503);
          try {
            const response = await fetch(new URL('/api/session', url), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: env.OPERATOR_TOKEN }), redirect: 'error', signal: AbortSignal.timeout(5000) });
            res.statusCode = response.status;
            res.setHeader('Cache-Control', 'no-store');
            const cookies = response.headers.getSetCookie();
            if (cookies.length) res.setHeader('Set-Cookie', cookies);
            res.end();
          } catch { deny(502); }
        });
      },
    }],
  };
});
