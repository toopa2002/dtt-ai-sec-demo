import { DatePipe } from '@angular/common';
import { Component, input } from '@angular/core';
import { SecretStatus } from './models';

/** The IAM engineer's view of the application secret (spec 002 R16): metadata only, no input (FR-120). */
@Component({
  selector: 'app-secret-status',
  imports: [DatePipe],
  template: `
    <section class="card" aria-label="Application secret" i18n-aria-label="@@secretCard.aria">
      <div class="head">
        <h2 i18n="@@secretCard.title">Application secret</h2>
        <span class="state" [class]="status()?.state ?? 'missing'">{{ stateLabel() }}</span>
      </div>
      <dl>
        <dt i18n="@@secretCard.provided">Provided</dt>
        <dd>
          {{ status()?.provided_by ?? '—' }}
          @if (status()?.provided_at) {
            · {{ status()?.provided_at | date: 'HH:mm' }}
          }
        </dd>
        <dt i18n="@@secretCard.inIsc">In SailPoint</dt>
        <dd>{{ status()?.applied_at ? (status()?.applied_at | date: 'HH:mm') : '—' }}</dd>
        <dt i18n="@@secretCard.deleted">Vault copy deleted</dt>
        <dd>
          @if (status()?.vault_deleted_at) {
            {{ status()?.vault_deleted_at | date: 'HH:mm' }}
            <span i18n="@@secretCard.afterTest">after Test Connection passed</span>
          } @else {
            —
          }
        </dd>
        <dt i18n="@@secretCard.expires">Expires</dt>
        <dd [class.warn]="status()?.expires_soon">
          {{ status()?.expires_on ?? '—' }}
          @if (status()?.expires_soon) {
            · <span i18n="@@secretCard.soon">within 30 days: ask for a new one</span>
          } @else {
            · <span class="muted" i18n="@@secretCard.warnBefore">warning 30 days before</span>
          }
        </dd>
      </dl>
    </section>
  `,
  styles: `
    .head {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    h2 {
      margin: 0;
      font-size: 0.95rem;
    }
    .state {
      margin-left: auto;
      font-size: 0.72rem;
      padding: 0.05rem 0.55rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .state.received {
      border-color: var(--info);
      color: var(--info-strong);
    }
    .state.in_isc,
    .state.vault_deleted {
      border-color: var(--ok);
      color: var(--ok);
    }
    dl {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 0.25rem 0.9rem;
      margin: 0.5rem 0 0;
      font-size: 0.82rem;
    }
    dt {
      color: var(--muted);
    }
    dd {
      margin: 0;
    }
    .warn {
      color: var(--warn);
      font-weight: 600;
    }
  `,
})
export class SecretStatusComponent {
  readonly status = input<SecretStatus | null | undefined>(null);

  protected stateLabel(): string {
    return {
      missing: $localize`:@@secret.missing:Not received`,
      received: $localize`:@@secret.received:Received`,
      in_isc: $localize`:@@secret.inIsc:In SailPoint`,
      vault_deleted: $localize`:@@secretCard.vaultDeletedLower:vault copy deleted`,
    }[this.status()?.state ?? 'missing'];
  }
}
