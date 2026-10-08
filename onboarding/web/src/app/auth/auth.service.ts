import { HttpInterceptorFn } from '@angular/common/http';
import { computed, inject, Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { ApiService } from '../shared/api.service';
import { Me } from '../shared/models';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly me = signal<Me | null>(null);
  readonly checked = signal(false);
  readonly csrf = computed(() => this.me()?.csrf_token ?? '');

  /** Load the current sign-in once (also refreshes the server-side idle timer). */
  async load(): Promise<Me | null> {
    if (this.checked()) return this.me();
    try {
      this.me.set(await this.api.session());
    } catch {
      this.me.set(null);
    }
    this.checked.set(true);
    return this.me();
  }

  async login(username: string, password: string): Promise<Me> {
    const me = await this.api.login(username, password);
    this.me.set(me);
    this.checked.set(true);
    return me;
  }

  async logout(): Promise<void> {
    try {
      await this.api.logout();
    } finally {
      this.signedOut();
    }
  }

  signedOut(): void {
    this.me.set(null);
    this.checked.set(true);
    void this.router.navigate(['/login']);
  }

  /** Where a role lands after sign-in (FR-003). */
  home(): string[] {
    return ['/sessions'];
  }
}

/** Adds the CSRF header to writes and sends the user to /login when the server says the session ended (FR-004). */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const write = !['GET', 'HEAD', 'OPTIONS'].includes(req.method);
  const out = write && auth.csrf() ? req.clone({ setHeaders: { 'X-CSRF-Token': auth.csrf() } }) : req;
  return next(out).pipe(
    catchError((err) => {
      if (err?.status === 401 && !req.url.endsWith('auth/login') && !req.url.endsWith('auth/session')) {
        auth.signedOut();
      }
      return throwError(() => err);
    }),
  );
};
