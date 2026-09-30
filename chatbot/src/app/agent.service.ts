import { inject, Injectable } from '@angular/core';
import { MsalService } from '@azure/msal-angular';
import { AccountInfo, InteractionRequiredAuthError } from '@azure/msal-browser';
import { Observable } from 'rxjs';
import { AGENT_PATH, AGENT_SCOPE } from './app.config';
import { AgentEvent, HopEvent } from './flow.model';

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

  ask(prompt: string): Observable<AgentEvent> {
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
        const response = await fetch(window.location.origin + AGENT_PATH, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': this.sessionId,
          },
          body: JSON.stringify({ prompt }),
          signal: abort.signal,
        });

        if (!response.ok) {
          const body = (await response.text()).slice(0, 300);
          const denied = response.status === 401 || response.status === 403;
          emit(invoke.end(denied ? 'denied' : 'error', { http_status: response.status, body }));
          emit({ type: 'error', message: `Request failed (${response.status})${denied ? ': the agent rejected your token' : ''}` });
          this.resetSession();
          subscriber.complete();
          return;
        }

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
      emit(h.end('ok', { scope: this.scope, from_cache: result.fromCache }));
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
