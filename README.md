# MCP Gateway security demo

> Architecture, flow and deployment diagrams plus the step-by-step demo script: **[docs/DEMO-GUIDE.md](docs/DEMO-GUIDE.md)**.

An AI agent that can only reach tools through **Microsoft MCP Gateway**, acting with the **end user's own identity**:

```
Browser ─► Angular chatbot (WSL :3000) ──token #1──► Bedrock AgentCore agent (ap-southeast-1, Claude Haiku 4.5)
            Entra sign-in (mcpdemo-chatbot)             │ on-behalf-of → token #2 (user identity)
                                                        ▼
                         ngrok (mutually-exotic-bonefish.ngrok-free.app) ─► MCP Gateway on k3s (WSL, Entra auth)
                                                        ├─ /adapters/weather/mcp       needs mcp.weather.user
                                                        └─ /adapters/hr-directory/mcp  needs mcp.hr.user (PII)
```

| Gate | Where | What is checked |
|---|---|---|
| 1 | Entra sign-in to `mcpdemo-chatbot` | user is assigned to the chatbot app |
| 2 | AgentCore JWT authorizer + agent code | Entra token for `mcpdemo-agent-api`, issued to the chatbot (`azp`), role `Agent.Invoke` |
| 3 | MCP Gateway | OBO token for `mcpdemo-gateway-api`; per-adapter `requiredRoles` against the user's roles |

MCP servers are reachable **only** through the gateway: they are created by the gateway's management API,
k3s NetworkPolicies admit traffic to adapter pods only from the gateway pod, nothing else is exposed,
and the HR server has no egress at all (`make verify` proves this).

Entra ID Free tier: roles are assigned directly to users (no groups).

## Personas

| Persona | Roles | "What's the weather in Bangkok, and who is Somchai's manager?" |
|---|---|---|
| `mcpdemo-full` | chatbot, Agent.Invoke, mcp.weather.user, mcp.hr.user | weather ✅ HR ✅ |
| `mcpdemo-weather-only` | chatbot, Agent.Invoke, mcp.weather.user | weather ✅ HR 🔒 |
| `mcpdemo-agent-only` | chatbot, Agent.Invoke | both 🔒 (no gateway access at all) |
| `mcpdemo-none` | — | blocked at chatbot sign-in |
| `mcpdemo-operator` | mcp.admin | registers the MCP servers (not a chat persona) |

Initial passwords for created users are in `.personas.local` (gitignored).

## Setup (once)

Prerequisites: WSL2 with systemd, Docker, `az` logged in to tenant `2aa0aac8-a644-4603-9530-36541a8e3c79`,
`aws` credentials for ap-southeast-1 with the **Anthropic use case form submitted** in Bedrock (Claude Haiku 4.5), `zip`, ngrok authtoken.
`make prereqs` checks all of it (.NET 8 and Node 22 install into your home directory, no sudo).

```bash
make prereqs     # check tools and logins
make k3s         # k3s without Traefik/ServiceLB + trust localhost:5000 (sudo)
make entra       # Entra apps, consent, demo users, role assignments (writes IDs to .env)
make images      # local registry + build gateway (patched, see below) and both MCP servers
make up          # gateway in Entra mode, network policies, port-forward + ngrok
make adapters    # operator deploys weather + hr-directory THROUGH the gateway API (device-code sign-in)
make agent       # AgentCore runtime + Entra JWT authorizer; OBO secret straight into SSM
make chat        # http://localhost:3000/mcp/  (public: https://$NGROK_DOMAIN/mcp/)
```

After a reboot: `make tunnel` (and `make chat`).

## Running the demo

1. `make demo` shows the gateway, the gateway-managed MCP pods and the direct-call matrix
   (no token → 401, weather-only on HR → 403, …); transcript in `out/demo-run.md`.
2. `make verify` proves direct pod access is blocked and every MCP server is gateway-registered.
3. Open https://$NGROK_DOMAIN/mcp/ (or http://localhost:3000/mcp/) in a private window and sign in as each persona. The **traffic panel** on the right shows each hop live (chatbot → Entra → agent → gateway → MCP server, plus Bedrock); see the guide §5.4a. Ask:
   - "What's the weather in Bangkok, and who is Somchai's manager?"
   - "What's the weather where Somchai lives?" (HR tool, then weather tool)
4. Live revocation: `python3 scripts/entra.py revoke <full-upn> gateway:mcp.hr.user`,
   sign out/in as `mcpdemo-full`, ask again: HR is denied, weather still works, nothing redeployed.
   Restore with `grant`.
5. HR access audit: `kubectl -n adapter logs hr-directory-0 | grep hr-audit` (the gateway forwards the
   authenticated user id; the adapter never sees the bearer token).

## AgentCore Gateway mode

The agent can reach the same MCP servers through **AWS Bedrock AgentCore Gateways** (one per MCP server) instead of
calling the MCP Gateway directly; `TOOLS_VIA` in `.env` selects the route (`mcp-gateway` is the default). AWS (and governance tools such as SailPoint, which reads AgentCore gateways and their MCP
targets) then see the agent's tools; the per-user security model is unchanged.

```
agent ──token #1──► AgentCore Gateway mcpdemo-gw-<name> (Entra JWT, aud = mcpdemo-agent-api)
                      └─ on-behalf-of exchange (Entra credential provider) → token #2 (user roles)
                         ──► MCP Gateway /adapters/<name>/mcp   (gate 3 unchanged)
```

```bash
make agentcore-gw                         # credential provider, role, one gateway + MCP target per server
TOOLS_VIA=agentcore-gateway make agent    # switch the agent; TOOLS_VIA=mcp-gateway make agent switches back
```

- Targets list tools at request time with the user's exchanged token; a server the user may not use answers with an
  authorization error (the MCP Gateway's 403), which the agent reports as denied. One gateway per server keeps one
  denial from hiding the other server (a gateway lists its targets one page each and stops at a refused one).
- The traffic panel shows an "AgentCore Gateways" node in this mode (`toolsVia` in the chatbot's `config.json`).
- **Bedrock Agent engine:** `make bedrock-agent` creates `mcpdemo-tools-agent`, a Bedrock Agent with one
  *return-control* action group per MCP server (`weather-mcp`, `hr-directory-mcp`; functions = the MCP tools).
  With `AGENT_ENGINE=bedrock-agent make agent`, mcpdemo_agent hands the model loop to it: the Bedrock Agent picks the
  tools, hands each call back, and mcpdemo_agent runs it through the AgentCore Gateways with the user's token — no
  Lambda, no token in Bedrock. Governance tools that read Bedrock Agent action groups (SailPoint's **Tools**) now see
  the agent's tools. `AGENT_ENGINE=claude make agent` switches back.
- **Inbound:** `make agentcore-gw` also puts an AgentCore Gateway in front of the agent itself (`mcpdemo-gw-agent`,
  target `mcpdemo-agent`, no protocol type). With `AGENT_VIA=agentcore-gateway` in `.env`, the chatbot's proxy calls
  `AGENT_GATEWAY_URL/invocations` instead of the runtime endpoint. The gateway checks the Entra token and passes it
  through unchanged (`JWT_PASSTHROUGH`), so the runtime's authorizer and the agent's `azp`/role checks still apply;
  SSE streaming works as before. The traffic panel then shows chatbot → AgentCore Gateway → agent.
- `make destroy` (or `scripts/agentcore-gateway.sh --delete`) removes it. Details: guide §5.8.

## Sharing the ngrok domain

The demo uses only `/mcp` on `$NGROK_DOMAIN`, so another app can use the rest of the domain:

| Public path | Goes to |
|---|---|
| `/mcp/` | chatbot dev server (`:3000`, Angular `baseHref` `/mcp/`) |
| `/mcp/gw/...` | MCP Gateway (port-forward `:8000`, prefix stripped) — `GATEWAY_URL` and the AgentCore Gateway targets use this |
| `/.well-known/oauth-protected-resource/mcp/gw/...` | the gateway's protected-resource metadata |
| everything else | `NGROK_ROOT_UPSTREAM` (a local port in `.env`), or 404 when empty |

ngrok's free plan can route by path but not rewrite paths, so `make tunnel` also runs a small nginx container,
`mcpdemo-edge` (`deployment/edge-nginx.conf`, port 8090), that strips the prefixes and puts `/mcp/gw` back into the
gateway's OAuth discovery URLs. Entra's chatbot redirect URIs are `https://$NGROK_DOMAIN/mcp/` and
`http://localhost:3000/mcp/`.

## Gateway patch

Upstream MCP Gateway uses Entra auth only in Production mode, which requires Cosmos DB; Development mode
(local Redis) forces `X-Dev-*` header auth. `patches/0001-entra-auth-in-development.patch` adds
`Authentication__UseEntra=true` so the local Redis setup authenticates with Entra. It is applied to a
clean submodule at build time (`scripts/03-build-push.sh`); the submodule itself stays unmodified.

## ISC Onboarding Agent

A second app in this repo, under [`onboarding/`](onboarding/): an IAM engineer and an application owner onboard an
application into SailPoint ISC in one shared chat, with a Claude Haiku agent on AgentCore doing the SailPoint side.
Web, API and MongoDB run on the local cluster under `/onboarding/` on the same ngrok domain; the agent runs on
AgentCore. Start with [`onboarding/README.md`](onboarding/README.md); deployment steps are in
[docs/DEMO-GUIDE.md §8](docs/DEMO-GUIDE.md#8-isc-onboarding-agent).

## Cost

Only AWS costs money: about $0.005 per question (Haiku 4.5) and a few cents for deploys; idle about
$0.05/month. The agent uses direct code deploy (no ECR/CodeBuild). Everything else is local or free tier.
Open-Meteo's free API is for non-commercial use; set `WEATHER_MOCK=1` in `demo/adapter-weather.json` if needed.

## Teardown

```bash
make down        # stop tunnel, delete the adapter namespace
make destroy     # also AgentCore runtime + SSM secret, Entra apps, k3s + registry
```
