import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import {
  Action,
  AdminSession,
  ApiError,
  Attachment,
  ConnectorType,
  Me,
  Message,
  Person,
  Role,
  SecretStatus,
  Session,
  SessionSummary,
  Suggestions,
  Tenant,
  User,
} from './models';

/** All calls are relative to <base href> (/onboarding/), so the API lives at /onboarding/api/. */
export const API = 'api';

export function apiError(err: unknown): ApiError {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as Partial<ApiError> | null;
    if (body && typeof body === 'object' && body.message) {
      return {
        code: body.code ?? 'error',
        message: body.message,
        retry_after_seconds: body.retry_after_seconds,
        errors: body.errors,
      };
    }
    if (err.status === 0) return { code: 'offline', message: $localize`:@@error.offline:Can't reach the server.` };
  }
  return { code: 'error', message: $localize`:@@error.generic:Something went wrong. Try again.` };
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  private get<T>(path: string): Promise<T> {
    return firstValueFrom(this.http.get<T>(`${API}/${path}`));
  }

  private send<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
    return firstValueFrom(this.http.request<T>(method, `${API}/${path}`, { body }));
  }

  login(username: string, password: string) {
    return this.send<Me>('POST', 'auth/login', { username, password });
  }
  logout() {
    return this.send<void>('POST', 'auth/logout');
  }
  session() {
    return this.get<Me>('auth/session');
  }

  catalog() {
    return this.get<ConnectorType[]>('catalog');
  }

  sessions(status?: 'open' | 'finished') {
    return this.get<SessionSummary[]>(status ? `sessions?status=${status}` : 'sessions');
  }
  sessionTenants() {
    return this.get<Pick<Tenant, 'id' | 'name' | 'api_host' | 'status'>[]>('sessions/tenants');
  }
  applicationOwners() {
    return this.get<Person[]>('sessions/people');
  }
  createSession(body: {
    connector_type: string;
    tenant_id: string;
    details: Record<string, unknown>;
    application_owner_id?: string;
    accept_warnings?: string[];
  }) {
    return this.send<Session & { warnings?: Record<string, string> }>('POST', 'sessions', body);
  }
  /** Spec 002 FR-120: write-only; the response is metadata only. */
  putSecret(id: string, value: string, expiresOn: string) {
    return this.send<SecretStatus>('PUT', `sessions/${id}/application-secret`, { value, expires_on: expiresOn });
  }
  secretStatus(id: string) {
    return this.get<SecretStatus>(`sessions/${id}/application-secret`);
  }
  getSession(id: string) {
    return this.get<Session>(`sessions/${id}`);
  }
  finishSession(id: string) {
    return this.send<Session>('POST', `sessions/${id}/finish`);
  }
  messages(id: string, afterSeq = 0) {
    return this.get<Message[]>(`sessions/${id}/messages?after_seq=${afterSeq}`);
  }
  sendMessage(id: string, text: string, attachmentIds: string[] = []) {
    return this.send<Message>('POST', `sessions/${id}/messages`, { text, attachment_ids: attachmentIds });
  }
  actions(id: string) {
    return this.get<Action[]>(`sessions/${id}/actions`);
  }
  suggestions(id: string) {
    return this.get<Suggestions>(`sessions/${id}/suggestions`);
  }
  /** One SailPoint action with its request, response and diagnosis (FR-020a); IAM engineer only. */
  action(id: string, actionId: string) {
    return this.get<Action>(`sessions/${id}/actions/${actionId}`);
  }
  adminSessions() {
    return this.get<AdminSession[]>('admin/sessions');
  }
  reopenSession(id: string) {
    return this.send<{ reopened: boolean }>('POST', `admin/sessions/${id}/reopen`);
  }
  handOver(id: string, place: Role, userId: string) {
    return this.send<{ applied: boolean }>('POST', `admin/sessions/${id}/handover`, { place, user_id: userId });
  }
  /** Long-poll fallback for the event stream: events after `after`, waiting up to 25 s for the first one. */
  pollEvents(id: string, after: number) {
    return this.get<{ events: { id: number; type: string; data: unknown }[] }>(`sessions/${id}/events/poll?after=${after}`);
  }
  upload(id: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    return firstValueFrom(this.http.post<Attachment>(`${API}/sessions/${id}/attachments`, form));
  }
  dismissAttachment(id: string, attachmentId: string) {
    return this.send<void>('DELETE', `sessions/${id}/attachments/${attachmentId}`);
  }
  attachmentUrl(id: string, attachmentId: string) {
    return `${API}/sessions/${id}/attachments/${attachmentId}`;
  }

  users() {
    return this.get<User[]>('admin/users');
  }
  createUser(body: { username: string; display_name: string; role: string; is_admin: boolean; initial_password: string }) {
    return this.send<User>('POST', 'admin/users', body);
  }
  updateUser(id: string, body: Record<string, unknown>) {
    return this.send<User>('PATCH', `admin/users/${id}`, body);
  }
  tenants() {
    return this.get<Tenant[]>('admin/tenants');
  }
  addTenant(body: { name: string; api_host: string; client_id: string; client_secret: string }) {
    return this.send<Tenant>('POST', 'admin/tenants', body);
  }
  replaceCredential(id: string, body: { client_id: string; client_secret: string }) {
    return this.send<Tenant>('PUT', `admin/tenants/${id}/credential`, body);
  }
  checkTenant(id: string) {
    return this.send<Tenant>('POST', `admin/tenants/${id}/check`);
  }
}
