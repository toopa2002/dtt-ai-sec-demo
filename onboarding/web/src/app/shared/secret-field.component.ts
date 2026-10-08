import { Component, ElementRef, inject, input, signal, viewChild } from '@angular/core';
import { ApiService, apiError } from './api.service';
import { SecretState, SecretStatus } from './models';

/**
 * The application owner's secret field (spec 002 FR-120, FR-121, research R16; canvas "Entra administrator").
 * Write-only: the value is read from the input once on submit, the input is cleared before the request returns, and
 * the value is never kept in a signal, the composer, a suggestion or browser storage. The API answers with metadata.
 */
@Component({
  selector: 'app-secret-field',
  template: `
    <section class="card secret" aria-labelledby="secret-h">
      <div class="head">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="5" y="11" width="14" height="9" rx="2"></rect>
          <path d="M8 11V8a4 4 0 0 1 8 0v3"></path>
        </svg>
        <h2 id="secret-h" i18n="@@secret.title">Secret field</h2>
        <span class="muted small" i18n="@@secret.privacy"
          >Only you see this. The value goes straight to the vault, then into SailPoint. The agent, the chat and the IAM
          engineer never see it.</span
        >
      </div>
      @if (open()) {
        @if (needed() !== null) {
          <p class="need" role="status"><b i18n="@@secret.needed">A new secret is needed.</b> {{ needed() }}</p>
        }
        <form class="row" (submit)="$event.preventDefault(); submit()">
          <div class="field grow">
            <label for="secret-value">{{ label() }}</label>
            <input
              #value
              id="secret-value"
              type="password"
              autocomplete="off"
              spellcheck="false"
              [attr.aria-invalid]="errors()['value'] ? true : null"
              aria-describedby="secret-help secret-err"
            />
          </div>
          <div class="field">
            <label for="secret-exp" i18n="@@secret.expires">Expires</label>
            <input #expires id="secret-exp" type="date" [attr.aria-invalid]="errors()['expires_on'] ? true : null" />
          </div>
          <button type="submit" class="primary" [disabled]="busy()" i18n="@@secret.send">Send to the vault</button>
        </form>
        <p id="secret-help" class="muted small">{{ help() }} · {{ expiresHelp() }}</p>
        @if (errorText(); as e) {
          <p id="secret-err" class="error" role="alert">{{ e }}</p>
        }
      }
      <div class="strip">
        <span class="small"><b i18n="@@secret.status">Status</b></span>
        <ol aria-label="Secret status" i18n-aria-label="@@secret.statusAria">
          @for (st of states; track st.id) {
            <li [class.current]="st.id === state()" [class.past]="isPast(st.id)">{{ st.label }}</li>
          }
        </ol>
        @if (status()?.expires_on) {
          <span class="small" [class.warn]="status()?.expires_soon">
            <span i18n="@@secret.expiresOn">expires {{ status()?.expires_on }}</span>
            @if (status()?.expires_soon) {
              <span i18n="@@secret.expiresSoon"> · within 30 days: create a new one</span>
            }
          </span>
        }
        <span class="muted small" i18n="@@secret.deletedNote"
          >The vault copy is deleted as soon as Test Connection passes.</span
        >
      </div>
    </section>
  `,
  styles: `
    .secret {
      border: 2px solid var(--info);
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem 0.75rem;
    }
    .head h2 {
      margin: 0;
      font-size: 1rem;
    }
    .icon {
      width: 1.25rem;
      height: 1.25rem;
      fill: none;
      stroke: var(--info-strong);
      stroke-width: 2.2;
    }
    .row {
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) auto;
      gap: 0.75rem;
      align-items: end;
    }
    @media (max-width: 700px) {
      .row {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      min-width: 0;
    }
    input {
      min-height: 44px;
    }
    input[aria-invalid='true'] {
      border: 2px solid var(--deny);
    }
    .primary {
      min-height: 44px;
    }
    .need {
      margin: 0;
      padding: 0.5rem 0.7rem;
      border-radius: 6px;
      background: color-mix(in srgb, var(--deny) 10%, var(--surface));
      font-size: 0.88rem;
    }
    .error {
      margin: 0;
      padding: 0.45rem 0.65rem;
      border-radius: 6px;
      background: color-mix(in srgb, var(--deny) 10%, var(--surface));
    }
    .strip {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 1rem;
      padding-top: 0.6rem;
      border-top: 1px solid var(--border);
    }
    .strip ol {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
    }
    .strip li {
      font-size: 0.75rem;
      padding: 0.1rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .strip li.past {
      color: var(--ok);
      border-color: var(--ok);
    }
    .strip li.current {
      color: var(--info-strong);
      border-color: var(--info);
      font-weight: 600;
      background: color-mix(in srgb, var(--info) 8%, var(--surface));
    }
    .small {
      font-size: 0.78rem;
      margin: 0;
    }
    .warn {
      color: var(--warn);
      font-weight: 600;
    }
  `,
})
export class SecretFieldComponent {
  readonly sessionId = input.required<string>();
  readonly status = input<SecretStatus | null | undefined>(null);
  /** The agent's reason when it asked for a new secret; null when it didn't. */
  readonly needed = input<string | null>(null);
  readonly label = input($localize`:@@secret.valueLabel:Client secret Value`);
  readonly help = input($localize`:@@secret.help:Certificates & secrets → the Value column, shown only once`);
  readonly expiresHelp = input($localize`:@@secret.expiresHelp:The Expires date shown next to it`);
  /** Whether the field is shown, not just the status strip (research R16). */
  readonly open = input(true);
  private readonly api = inject(ApiService);
  private readonly value = viewChild<ElementRef<HTMLInputElement>>('value');
  private readonly expires = viewChild<ElementRef<HTMLInputElement>>('expires');
  protected readonly busy = signal(false);
  protected readonly errors = signal<Record<string, string>>({});
  protected readonly failure = signal<string | null>(null);
  protected readonly states: { id: SecretState; label: string }[] = [
    { id: 'missing', label: $localize`:@@secret.missing:Not received` },
    { id: 'received', label: $localize`:@@secret.received:Received` },
    { id: 'in_isc', label: $localize`:@@secret.inIsc:In SailPoint` },
    { id: 'vault_deleted', label: $localize`:@@secret.vaultDeleted:Vault copy deleted` },
  ];

  protected state(): SecretState {
    return this.status()?.state ?? 'missing';
  }

  protected isPast(id: SecretState): boolean {
    const order = this.states.map((s) => s.id);
    return order.indexOf(id) < order.indexOf(this.state());
  }

  protected errorText(): string | null {
    const e = this.errors();
    const parts: string[] = [];
    if (e['value'])
      parts.push(
        e['value'] === "this is the secret's ID, not its Value"
          ? $localize`:@@secret.guid:This is the secret's ID, not its Value. In Entra, open Certificates & secrets and copy the Value column. It's shown only once, right after you create the secret.`
          : `${this.label()}: ${e['value']}`,
      );
    if (e['expires_on']) parts.push($localize`:@@secret.expiryError:Expires: ${e['expires_on']}:problem:`);
    return parts.join(' ') || this.failure();
  }

  protected async submit(): Promise<void> {
    const input = this.value()?.nativeElement;
    const expiry = this.expires()?.nativeElement;
    if (!input) return;
    const secret = input.value;
    input.value = ''; // cleared before the request returns: the value never stays on the page
    this.busy.set(true);
    this.errors.set({});
    this.failure.set(null);
    try {
      await this.api.putSecret(this.sessionId(), secret, expiry?.value ?? '');
      if (expiry) expiry.value = '';
    } catch (err) {
      const e = apiError(err);
      this.errors.set(e.errors ?? {});
      if (!e.errors) this.failure.set(e.message);
    } finally {
      this.busy.set(false);
    }
  }
}
