import { inject, Injectable } from '@angular/core';
import { MsalService } from '@azure/msal-angular';
import { AccountInfo, InteractionRequiredAuthError } from '@azure/msal-browser';
import { Observable } from 'rxjs';
import { AGENT_SCOPE, AGENT_URL } from './app.config';
import { AgentEvent, Engine, HopEvent, HttpExchange } from './flow.model';

const CLAIMS_SHOWN = ['aud', 'azp', 'roles', 'scp', 'preferred_username'];

/** The whitelisted claims of a JWT (the token itself is never shown). */
function claimsOf(jwt: string): Record<string, unknown> {
  try {
    const payload = JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return Object.fromEntries(CLAIMS_SHOWN.filter((k) => k in payload).map((k) => [k, payload[k]]));
  } catch {
    return {};
  }
}

const short = (text: string) => (text.length > 300 ? text.slice(0, 300) + '…' : text);

/**
 * Streams the agent's events (server-sent events from AgentCore) and adds the
 * chatbot's own hops: token #1 from Entra, and the POST to the agent.
 */
@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly msal = inject(MsalService);
  private readonly scope = inject(AGENT_SCOPE);
  // AgentCore requires a session id of at least 33 characters. One id is reused per conversation;
  // resetSession() starts a fresh runtime session (e.g. after a failed request left the old one unusable).
  private sessionId = AgentService.newSessionId();

  private static newSessionId(): string {
    return `chat-${crypto.randomUUID()}-${Date.now()}`;
  }

  resetSession(): void {
    this.sessionId = AgentService.newSessionId();
  }

  ask(prompt: string, engine: Engine): Observable<AgentEvent> {
    return new Observable<AgentEvent>((subscriber) => {
      const abort = new AbortController();
      const emit = (ev: AgentEvent) => subscriber.next(ev);

      (async () => {
        const token = await this.acquireToken(emit);
        if (!token) {
          subscriber.complete();
          return;
        }

        const invoke = hop('invoke', 'chatbot', 'agent', 'POST /invocations');
        emit(invoke.start());
        const sent: HttpExchange['request'] = {
          method: 'POST',
          url: AGENT_URL,
          headers: {
            'content-type': 'application/json',
            accept: 'text/event-stream',
            'x-amzn-bedrock-agentcore-runtime-session-id': this.sessionId,
            authorization: 'Bearer <token #1>',
          },
          body: short(JSON.stringify({ prompt, engine })),
        };
        const response = await fetch(AGENT_URL, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': this.sessionId,
          },
          body: JSON.stringify({ prompt, engine }),
          signal: abort.signal,
        });

        const received = (body: string | null): HttpExchange => ({
          request: sent,
          response: {
            status: response.status,
            headers: { 'content-type': response.headers.get('content-type') ?? '' },
            body,
          },
        });
        if (!response.ok) {
          const body = (await response.text()).slice(0, 300);
          const denied = response.status === 401 || response.status === 403;
          emit(invoke.end(denied ? 'denied' : 'error', { http_status: response.status, body, http: [received(body)] }));
          emit({ type: 'error', message: `Request failed (${response.status})${denied ? ': the agent rejected your token' : ''}` });
          this.resetSession();
          subscriber.complete();
          return;
        }

        // Still in flight (the agent ends this step); add the browser-side exchange to it.
        emit({ ...invoke.start(), detail: { http: [received('(server-sent events, streamed below)')] } });
        for await (const ev of readEvents(response)) emit(ev);
        subscriber.complete();
      })().catch((err: unknown) => {
        if (abort.signal.aborted) return;
        this.resetSession();
        emit({ type: 'error', message: err instanceof Error ? err.message : String(err) });
        subscriber.complete();
      });

      return () => abort.abort();
    });
  }

  /** Token #1 for the agent API, shown as the chatbot -> Entra hop. */
  private async acquireToken(emit: (ev: AgentEvent) => void): Promise<string | null> {
    const h = hop('token', 'chatbot', 'entra', 'acquire token #1 (agent API)');
    emit(h.start());
    const account: AccountInfo | undefined =
      this.msal.instance.getActiveAccount() ?? this.msal.instance.getAllAccounts()[0];
    try {
      const result = await this.msal.instance.acquireTokenSilent({ scopes: [this.scope], account });
      const auth = this.msal.instance.getConfiguration().auth;
      const http: HttpExchange = {
        request: {
          method: result.fromCache ? '(MSAL cache)' : 'POST',
          url: `${auth.authority}/oauth2/v2.0/token`,
          headers: { 'content-type': 'application/x-www-form-urlencoded' },
          body: result.fromCache
            ? 'not sent: token #1 was still valid in the browser cache'
            : `grant_type=refresh_token&client_id=${auth.clientId}&scope=${this.scope}&refresh_token=<refresh token>`,
        },
        response: {
          status: result.fromCache ? 'cache' : 200,
          headers: { 'content-type': 'application/json' },
          body: short(JSON.stringify({ token_type: 'Bearer', access_token: '<token #1>', expires_on: result.expiresOn,
            'token #1 claims': claimsOf(result.accessToken) })),
        },
      };
      emit(h.end('ok', { scope: this.scope, from_cache: result.fromCache, http: [http] }));
      return result.accessToken;
    } catch (err) {
      emit(h.end('error', { scope: this.scope, error: err instanceof Error ? err.message : String(err) }));
      if (err instanceof InteractionRequiredAuthError) {
        await this.msal.instance.acquireTokenRedirect({ scopes: [this.scope], account });
      } else {
        emit({ type: 'error', message: 'Could not get a token for the agent. Try signing in again.' });
      }
      return null;
    }
  }
}

function hop(id: string, from: HopEvent['from'], to: HopEvent['to'], label: string) {
  const t0 = performance.now();
  const base = { type: 'hop' as const, id, from, to, label };
  return {
    start: (): HopEvent => ({ ...base, status: 'start' }),
    end: (status: HopEvent['status'], detail?: Record<string, unknown>): HopEvent => ({
      ...base,
      status,
      ms: Math.round(performance.now() - t0),
      detail,
    }),
  };
}

/** Parse a text/event-stream body ("data: {json}" blocks separated by blank lines). */
async function* readEvents(response: Response): AsyncGenerator<AgentEvent> {
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (data) yield normalize(JSON.parse(data));
    }
  }
}

/** AgentCore reports an exception inside the stream as {error, error_type, message} (no "type"). */
function normalize(raw: Record<string, unknown>): AgentEvent {
  if (typeof raw['type'] === 'string') return raw as unknown as AgentEvent;
  return { type: 'error', message: String(raw['error'] ?? raw['message'] ?? 'Unknown agent error') };
}
