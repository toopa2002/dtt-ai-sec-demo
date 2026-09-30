// Dev-server proxy: /api/agent -> Bedrock AgentCore Runtime invocation endpoint.
// Avoids browser CORS; the browser's Bearer token is forwarded unchanged. No AWS credentials here.
const fs = require('fs');
const path = require('path');

const env = Object.fromEntries(
  fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8')
    .split('\n').filter((l) => l.includes('=') && !l.startsWith('#'))
    .map((l) => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()]),
);
const arn = env.AGENT_RUNTIME_ARN;
const region = env.AWS_REGION || 'ap-southeast-1';
if (!arn) console.warn('[proxy] AGENT_RUNTIME_ARN is not set in .env; run scripts/agent-deploy.sh first');

module.exports = {
  '/api/agent': {
    target: `https://bedrock-agentcore.${region}.amazonaws.com`,
    changeOrigin: true,
    secure: true,
    // Angular's Vite-based dev server uses `rewrite` (not http-proxy-middleware's `pathRewrite`).
    rewrite: () => `/runtimes/${encodeURIComponent(arn || 'missing')}/invocations?qualifier=DEFAULT`,
  },
};
