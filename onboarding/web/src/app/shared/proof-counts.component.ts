import { Component, computed, input } from '@angular/core';
import { Proof } from './models';

/** "What SailPoint now sees" (spec 002 FR-133, research R18; canvas "IAM engineer"): IAM engineer only. */
@Component({
  selector: 'app-proof-counts',
  template: `
    <section class="card" aria-label="Proof results" i18n-aria-label="@@proof.aria">
      <div class="head">
        <h2 i18n="@@proof.title">What SailPoint now sees</h2>
        <span class="muted small">{{ sourceName() }}</span>
      </div>
      <dl class="tiles">
        <div class="tile">
          <dt i18n="@@proof.users">Users</dt>
          <dd>{{ shown(proof()?.users) }}</dd>
        </div>
        @if (servicePrincipals()) {
          <div class="tile">
            <dt i18n="@@proof.sps">Service principals</dt>
            <dd>{{ shown(proof()?.service_principals) }}</dd>
          </div>
        }
        <div class="tile">
          <dt i18n="@@proof.entitlements">Entitlements</dt>
          <dd>{{ shown(proof()?.entitlements) }}</dd>
        </div>
        @if (aiAgents()) {
          @if (proof()?.ai_agents_state === 'tenant_limitation' && proof()?.ai_agents == null) {
            <div class="tile limit">
              <dt i18n="@@proof.agents">AI agents (Foundry)</dt>
              <dd class="text" i18n="@@proof.startInIsc">Start it in ISC</dd>
            </div>
          } @else {
            <div class="tile">
              <dt i18n="@@proof.agents">AI agents (Foundry)</dt>
              <dd>{{ shown(proof()?.ai_agents) }}</dd>
            </div>
          }
        }
      </dl>
    </section>
  `,
  styles: `
    .head {
      display: flex;
      align-items: baseline;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    h2 {
      margin: 0;
      font-size: 0.95rem;
    }
    .small {
      font-size: 0.78rem;
    }
    .tiles {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.6rem;
      margin: 0.6rem 0 0;
    }
    .tile {
      padding: 0.55rem 0.7rem;
      border-radius: 8px;
      background: var(--surface-2);
      display: flex;
      flex-direction: column;
      gap: 0.1rem;
    }
    dt {
      font-size: 0.75rem;
      color: var(--muted);
    }
    dd {
      margin: 0;
      font-size: 1.35rem;
      font-weight: 700;
    }
    .tile.limit {
      border: 1px solid var(--warn);
      background: color-mix(in srgb, var(--warn) 10%, var(--surface));
    }
    .tile.limit dt,
    dd.text {
      color: var(--warn);
    }
    dd.text {
      font-size: 0.95rem;
      padding-top: 0.25rem;
    }
  `,
})
export class ProofCountsComponent {
  readonly proof = input<Proof | null | undefined>(null);
  readonly capabilities = input<string[]>([]);
  readonly sourceName = input<string>('');
  protected readonly servicePrincipals = computed(() => this.capabilities().includes('service_principals'));
  protected readonly aiAgents = computed(() => this.capabilities().includes('ai_agents'));

  protected shown(v: number | null | undefined): string {
    return v === null || v === undefined ? '—' : v.toLocaleString('en-US');
  }
}
