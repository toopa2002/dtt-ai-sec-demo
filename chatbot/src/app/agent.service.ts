import { HttpClient, HttpHeaders } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { AGENT_PATH } from './app.config';

export interface AgentReply {
  answer?: string;
  error?: string;
  tools_used?: string[];
  services_allowed?: string[];
  services_denied?: string[];
  services_unavailable?: string[];
}

@Injectable({ providedIn: 'root' })
export class AgentService {
  private readonly http = inject(HttpClient);
  // AgentCore requires a session id of at least 33 characters. One id is reused per conversation;
  // resetSession() starts a fresh runtime session (e.g. after a failed request left the old one unusable).
  private sessionId = AgentService.newSessionId();

  private static newSessionId(): string {
    return `chat-${crypto.randomUUID()}-${Date.now()}`;
  }

  resetSession(): void {
    this.sessionId = AgentService.newSessionId();
  }

  ask(prompt: string): Observable<AgentReply> {
    // MsalInterceptor adds "Authorization: Bearer <agent-api token>" to this call.
    const headers = new HttpHeaders({ 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': this.sessionId });
    return this.http.post<AgentReply>(window.location.origin + AGENT_PATH, { prompt }, { headers });
  }
}
