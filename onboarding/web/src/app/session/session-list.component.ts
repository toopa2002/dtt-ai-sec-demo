import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AuthService } from '../auth/auth.service';
import { ApiService, apiError } from '../shared/api.service';
import { SessionSummary, STEP_KEYS } from '../shared/models';

/** Sessions the person takes part in, open and finished (US6). Finished ones open read-only with their history. */
@Component({
  selector: 'app-session-list',
  imports: [RouterLink, DatePipe],
  template: `
    <div class="wrap">
      <header>
        <h1 i18n="@@sessions.title">Onboarding sessions</h1>
        @if (isIam()) { <a class="button primary" routerLink="/catalog" i18n="@@sessions.new">New session</a> }
      </header>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      @if (left()) {
        <p class="notice" role="status" i18n="@@sessions.left">An admin handed your place in that session to someone else, so it is no longer in your list.</p>
      }
      @for (group of groups(); track group.label) {
        <h2>{{ group.label }}</h2>
        @for (s of group.items; track s.id) {
          <a class="card row" [routerLink]="['/sessions', s.id, isIam() ? 'iam' : 'owner']">
            <span class="name">{{ s.title }}</span>
            <span class="muted mono">{{ s.connector_type }}</span>
            <span class="progress" [attr.aria-label]="passed(s) + ' of 6 steps passed'">{{ passed(s) }}/6</span>
            <span class="muted">{{ (s.finished_at ?? s.created_at) | date: 'd MMM, HH:mm' }}</span>
          </a>
        } @empty {
          <p class="muted">{{ group.empty }}</p>
        }
      }
    </div>
  `,
  styles: `
    .notice { padding: 0.6rem 0.9rem; border-radius: 8px; background: var(--aws-fill); border: 1px solid var(--info); }
    .wrap { max-width: 64rem; margin: 0 auto; padding: 1.5rem; width: 100%; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    h1 { margin: 0; font-size: 1.4rem; }
    h2 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 1.5rem 0 0.6rem; }
    .button { display: inline-flex; align-items: center; min-height: 40px; padding: 0 1rem; border-radius: 6px; text-decoration: none; font-weight: 600; }
    .button.primary { background: var(--accent); color: var(--on-accent); }
    .row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto; gap: 1rem; align-items: center;
      text-decoration: none; color: var(--text); margin-bottom: 0.5rem; }
    .row:hover { border-color: var(--highlight); }
    .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .progress { font-variant-numeric: tabular-nums; font-weight: 600; }
  `,
})
export class SessionListComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  protected readonly sessions = signal<SessionSummary[]>([]);
  protected readonly error = signal<string | null>(null);
  /** Arrived here because an admin handed this person's place over (FR-033). */
  protected readonly left = signal(!!inject(ActivatedRoute).snapshot.queryParamMap.get('left'));
  protected readonly isIam = computed(() => this.auth.me()?.role === 'iam_engineer');
  protected readonly groups = computed(() => [
    {
      label: $localize`:@@sessions.open:Open`,
      empty: $localize`:@@sessions.noOpen:No open sessions.`,
      items: this.sessions().filter((s) => s.status === 'open'),
    },
    {
      label: $localize`:@@sessions.finished:Finished (kept for 90 days)`,
      empty: $localize`:@@sessions.noFinished:No finished sessions.`,
      items: this.sessions().filter((s) => s.status === 'finished'),
    },
  ]);

  async ngOnInit(): Promise<void> {
    try {
      this.sessions.set(await this.api.sessions());
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  protected passed(s: SessionSummary): number {
    return STEP_KEYS.filter((k) => s.steps[k] === 'passed').length;
  }
}
