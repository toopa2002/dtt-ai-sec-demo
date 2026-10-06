import { ApplicationConfig, InjectionToken, provideBrowserGlobalErrorListeners } from '@angular/core';
import { HTTP_INTERCEPTORS, provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import {
  BrowserCacheLocation,
  InteractionType,
  IPublicClientApplication,
  PublicClientApplication,
} from '@azure/msal-browser';
import {
  MSAL_GUARD_CONFIG,
  MSAL_INSTANCE,
  MSAL_INTERCEPTOR_CONFIG,
  MsalBroadcastService,
  MsalGuard,
  MsalGuardConfiguration,
  MsalInterceptor,
  MsalInterceptorConfiguration,
  MsalService,
} from '@azure/msal-angular';
import { routes } from './app.routes';

export interface RuntimeConfig {
  tenantId: string;
  chatClientId: string;
  agentApiClientId: string;
  /** How the agent reaches the MCP tools: "mcp-gateway" (direct) or "agentcore-gateway". */
  toolsVia?: string;
  /** How the chatbot reaches the agent: "direct" (runtime endpoint) or "agentcore-gateway" (inbound gateway). */
  agentVia?: string;
  /** Identities shown on the traffic panel's nodes (no secrets: ARNs and client ids only). */
  agentRoleArn?: string;
  gatewayRoleArn?: string;
  /** The agent's model loop: "claude" (Claude on Bedrock) or "bedrock-agent" (Bedrock Agent with action groups). */
  agentEngine?: string;
  bedrockAgentId?: string;
  gatewayApiClientId?: string;
  /** AWS resource names drawn on the traffic panel's nodes. */
  agentRuntimeArn?: string;
  bedrockAgentName?: string;
  mcpAdapters?: string[];
}

/** The account each traffic-panel node acts as. */
export interface Identities {
  tenantId: string;
  agentEngine: string;
  bedrockAgentId: string;
  agentRoleArn: string;
  gatewayRoleArn: string;
  gatewayApiClientId: string;
  /** AgentCore runtime name (from its ARN), Bedrock Agent name, and the MCP servers (one AgentCore Gateway each). */
  agentRuntimeName: string;
  bedrockAgentName: string;
  mcpAdapters: string[];
}

/** Where the app is served: <base href> (e.g. https://host/mcp/), so it can share a domain with other apps. */
export const APP_URL = document.baseURI;

/** The dev-server proxy to the agent, under the app's base path. */
export const AGENT_URL = new URL('api/agent', APP_URL).href;

/** Scope for token #1 (chatbot -> agent API). */
export const AGENT_SCOPE = new InjectionToken<string>('AGENT_SCOPE');

/** The agent's tool route (TOOLS_VIA in .env); the traffic panel picks its layout from it. */
export const TOOLS_VIA = new InjectionToken<string>('TOOLS_VIA');

/** The chatbot's route to the agent (AGENT_VIA in .env; the dev-server proxy does the routing). */
export const AGENT_VIA = new InjectionToken<string>('AGENT_VIA');

export const IDENTITIES = new InjectionToken<Identities>('IDENTITIES');

export function buildAppConfig(config: RuntimeConfig): ApplicationConfig {
  const agentScope = `api://${config.agentApiClientId}/access_as_user`;

  const msalInstance: IPublicClientApplication = new PublicClientApplication({
    auth: {
      clientId: config.chatClientId,
      authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri: APP_URL,
      postLogoutRedirectUri: APP_URL,
    },
    cache: { cacheLocation: BrowserCacheLocation.SessionStorage },
  });

  const guardConfig: MsalGuardConfiguration = {
    interactionType: InteractionType.Redirect,
    authRequest: { scopes: [agentScope], prompt: 'select_account' },
  };

  // The interceptor attaches the agent-API token (token #1) to every call to the agent proxy.
  const interceptorConfig: MsalInterceptorConfiguration = {
    interactionType: InteractionType.Redirect,
    protectedResourceMap: new Map([[AGENT_URL, [agentScope]]]),
  };

  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      provideRouter(routes),
      provideHttpClient(withInterceptorsFromDi()),
      { provide: HTTP_INTERCEPTORS, useClass: MsalInterceptor, multi: true },
      { provide: MSAL_INSTANCE, useValue: msalInstance },
      { provide: MSAL_GUARD_CONFIG, useValue: guardConfig },
      { provide: MSAL_INTERCEPTOR_CONFIG, useValue: interceptorConfig },
      { provide: AGENT_SCOPE, useValue: agentScope },
      { provide: TOOLS_VIA, useValue: config.toolsVia || 'mcp-gateway' },
      { provide: AGENT_VIA, useValue: config.agentVia || 'direct' },
      {
        provide: IDENTITIES,
        useValue: {
          tenantId: config.tenantId,
          agentEngine: config.agentEngine || 'claude',
          bedrockAgentId: config.bedrockAgentId || '',
          agentRoleArn: config.agentRoleArn || '',
          gatewayRoleArn: config.gatewayRoleArn || '',
          gatewayApiClientId: config.gatewayApiClientId || '',
          // runtime/mcpdemo_agent-ZSZ5Gd8OSF -> mcpdemo_agent
          agentRuntimeName: (config.agentRuntimeArn ?? '').split('/').pop()?.replace(/-[A-Za-z0-9]{10}$/, '') ?? '',
          bedrockAgentName: config.bedrockAgentName || '',
          mcpAdapters: config.mcpAdapters?.length ? config.mcpAdapters : ['weather', 'hr-directory'],
        } satisfies Identities,
      },
      MsalService,
      MsalGuard,
      MsalBroadcastService,
    ],
  };
}
