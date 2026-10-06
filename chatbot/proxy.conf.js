// Dev-server proxy: /mcp/api/agent -> the agent. Avoids browser CORS; the browser's Bearer token (and the AgentCore
// session header) are forwarded unchanged. No AWS credentials here.
//   AGENT_VIA=direct (default)        -> the AgentCore Runtime invocation endpoint
//   AGENT_VIA=agentcore-gateway       -> the inbound AgentCore Gateway (AGENT_GATEWAY_URL, token passthrough to the runtime)
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

const viaGateway = env.AGENT_VIA === 'agentcore-gateway';
if (viaGateway && !env.AGENT_GATEWAY_URL) throw new Error('[proxy] AGENT_VIA=agentcore-gateway needs AGENT_GATEWAY_URL (scripts/agentcore-gateway.sh)');
const gateway = viaGateway ? new URL(env.AGENT_GATEWAY_URL) : null;
console.log(`[proxy] /api/agent -> ${viaGateway ? env.AGENT_GATEWAY_URL + '/invocations (AgentCore Gateway)' : 'AgentCore Runtime ' + arn}`);

// The app is served under <base href> /mcp/ (angular.json baseHref), so its proxy path carries that prefix.
module.exports = {
  '/mcp/api/agent': {
    target: gateway ? gateway.origin : `https://bedrock-agentcore.${region}.amazonaws.com`,
    changeOrigin: true,
    secure: true,
    // Angular's Vite-based dev server uses `rewrite` (not http-proxy-middleware's `pathRewrite`).
    rewrite: () =>
      gateway
        ? `${gateway.pathname.replace(/\/$/, '')}/invocations`
        : `/runtimes/${encodeURIComponent(arn || 'missing')}/invocations?qualifier=DEFAULT`,
  },
};
