import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from './auth/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    @if (auth.me(); as me) {
      <header>
        <a class="brand" routerLink="/sessions"><span class="dot"></span><span i18n="@@app.name">ISC Onboarding Agent</span></a>
        <nav aria-label="Main" i18n-aria-label="@@nav.aria">
          <a routerLink="/sessions" routerLinkActive="on" i18n="@@nav.sessions">Sessions</a>
          <a routerLink="/catalog" routerLinkActive="on" i18n="@@nav.catalog">Connector catalog</a>
          @if (me.is_admin) { <a routerLink="/admin" routerLinkActive="on" i18n="@@nav.admin">Admin</a> }
        </nav>
        <span class="who">
          <span class="badge" [class.iam]="me.role === 'iam_engineer'" [class.owner]="me.role === 'application_owner'">{{ roleLabel() }}</span>
          <span class="mono">{{ me.username }}</span>
          <button type="button" (click)="auth.logout()" i18n="@@nav.signOut">Sign out</button>
        </span>
      </header>
    }
    <main><router-outlet /></main>
  `,
  styles: `
    :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh; }  /* the page never grows; pages scroll in main */
    header { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem 1.5rem; padding: 0.6rem 1.5rem; background: #000; color: #fff; }
    .brand { display: flex; align-items: center; gap: 0.55rem; color: #fff; text-decoration: none; font-weight: 700; }
    .dot { width: 0.65rem; height: 0.65rem; border-radius: 50%; background: var(--highlight); }
    nav { display: flex; gap: 1rem; }
    nav a { color: #d0d0ce; text-decoration: none; font-size: 0.9rem; padding: 0.3rem 0; border-bottom: 2px solid transparent; }
    nav a.on, nav a:hover { color: #fff; border-color: var(--highlight); }
    .who { margin-left: auto; display: flex; align-items: center; gap: 0.75rem; font-size: 0.82rem; color: #d0d0ce; }
    .who button { background: transparent; color: #fff; border-color: #53565a; min-height: 32px; }
    main { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow-y: auto; }
  `,
})
export class App {
  protected readonly auth = inject(AuthService);
  protected readonly roleLabel = computed(() => {
    const me = this.auth.me();
    if (!me) return '';
    const role = me.role === 'iam_engineer' ? $localize`:@@role.iam:IAM engineer` : $localize`:@@role.owner:Application owner`;
    return me.is_admin ? $localize`:@@role.admin:${role}:role: · admin` : role;
  });
}
