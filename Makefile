# MCP Gateway security demo. Run `make help`.
SHELL := /bin/bash
S := scripts

help:            ## List targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | column -t -s $$'\t'

prereqs:         ## Check required tools and logins
	$(S)/00-prereqs.sh
k3s:             ## Install/start k3s + trust the local registry (sudo)
	$(S)/01-k3s-up.sh
entra:           ## Create Entra apps, consent, demo users and role assignments
	python3 $(S)/entra.py setup --create-users
images:          ## Start registry, build + push gateway and MCP server images
	$(S)/02-registry.sh && $(S)/03-build-push.sh
up:              ## Deploy gateway (Entra mode), network policies, port-forward + ngrok
	$(S)/04-deploy.sh
adapters:        ## Operator registers the weather + hr-directory MCP servers via the gateway API
	$(S)/05-register-adapters.sh
test-agent:      ## Run the agent locally against the real gateway/Entra/Bedrock (before deploying)
	$(S)/test-agent-local.sh
agent:           ## Deploy the agent to Bedrock AgentCore (ap-southeast-1)
	$(S)/agent-deploy.sh
chat:            ## Run the Angular chatbot on http://localhost:3000
	$(S)/chatbot.sh
demo:            ## Scripted demo: gateway state + authorization matrix (out/demo-run.md)
	$(S)/demo.sh
verify:          ## Prove MCP servers are reachable only through the gateway
	$(S)/verify-gateway-only.sh
tunnel:          ## Restart port-forward + ngrok after a reboot
	$(S)/tunnel.sh start
access:          ## Show Entra role assignments
	python3 $(S)/entra.py show
down:            ## Stop tunnel, delete the adapter namespace
	$(S)/99-teardown.sh
destroy:         ## Remove everything (AWS, Entra apps, k3s)
	$(S)/99-teardown.sh --aws --entra --k3s

.PHONY: help prereqs k3s entra images up adapters test-agent agent chat demo verify tunnel access down destroy
