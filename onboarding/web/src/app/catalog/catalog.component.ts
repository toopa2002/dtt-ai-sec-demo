import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../auth/auth.service';
import { ApiService, apiError } from '../shared/api.service';
import { ConnectorType } from '../shared/models';

/** Connector catalog (FR-028, FR-029, US5): shipped with each release, read-only here. */
@Component({
  selector: 'app-catalog',
  imports: [RouterLink],
  template: `
    <div class="wrap">
      <h1 i18n="@@catalog.title">Connector types</h1>
      <p class="muted lede" i18n="@@catalog.lede">Each connector type tells the agent what to ask the application owner to set up, what to configure in SailPoint, which checks to run and which failures it knows how to fix. The list ships with each product version; it can't be changed here.</p>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }

      <h2 i18n="@@catalog.available">Available</h2>
      @for (c of available(); track c.id) {
        <article class="card available">
          <div class="main">
            <h3>{{ c.name }} <span class="chip passed" i18n="@@catalog.availableChip">available</span></h3>
            <p>{{ c.description }}</p>
            <p class="muted small"><span i18n="@@catalog.ownerIs">Application owner:</span> {{ c.owner_label }}</p>
          </div>
          <div><span class="label" i18n="@@catalog.asks">Application owner will be asked to</span><p>{{ c.owner_asks }}</p></div>
          <div><span class="label" i18n="@@catalog.configures">Agent configures in SailPoint</span><p>{{ c.agent_configures }}</p></div>
          @if (isIam()) {
            <a class="button primary" [routerLink]="['/sessions/new', c.id]" i18n="@@catalog.start">Start a session</a>
          }
        </article>
      }

      <h2 i18n="@@catalog.planned">Planned</h2>
      <div class="table card">
        <table>
          <thead>
            <tr>
              <th scope="col" i18n="@@catalog.colType">Connector type</th>
              <th scope="col" i18n="@@catalog.asks">Application owner will be asked to</th>
              <th scope="col" i18n="@@catalog.configures">Agent configures in SailPoint</th>
              <th scope="col" i18n="@@catalog.colStatus">Status</th>
            </tr>
          </thead>
          <tbody>
            @for (c of planned(); track c.id) {
              <tr><th scope="row">{{ c.name }}</th><td>{{ c.owner_asks }}</td><td>{{ c.agent_configures }}</td>
                <td><span class="tag ro" i18n="@@catalog.plannedTag">planned</span></td></tr>
            }
          </tbody>
        </table>
      </div>
      <p class="muted small" i18n="@@catalog.plannedNote">A planned type can be read about but can't start a session. It becomes available in a later product version, once its playbook has been tested against a real tenant.</p>
    </div>
  `,
  styles: `
    .wrap { max-width: 72rem; margin: 0 auto; padding: 1.5rem; width: 100%; }
    h1 { margin: 0; font-size: 1.4rem; }
    .lede { max-width: 70ch; }
    h2 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 1.5rem 0 0.6rem; }
    .available { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr) auto; gap: 1.25rem;
      align-items: start; border: 2px solid var(--highlight); }
    @media (max-width: 900px) { .available { grid-template-columns: minmax(0, 1fr); } }
    h3 { margin: 0 0 0.4rem; display: flex; align-items: center; gap: 0.6rem; }
    p { margin: 0.2rem 0; line-height: 1.5; font-size: 0.9rem; }
    .label { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
    .small { font-size: 0.78rem; }
    .button { display: inline-flex; align-items: center; min-height: 44px; padding: 0 1rem; border-radius: 6px; text-decoration: none; font-weight: 600; white-space: nowrap; }
    .button.primary { background: var(--accent); color: var(--on-accent); }
    .table { padding: 0; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; min-width: 44rem; }
    th, td { text-align: left; padding: 0.6rem 0.9rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    thead th { font-size: 0.75rem; color: var(--muted); font-weight: 600; }
    tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
  `,
})
export class CatalogComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  protected readonly catalog = signal<ConnectorType[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly available = computed(() => this.catalog().filter((c) => c.status === 'available'));
  protected readonly planned = computed(() => this.catalog().filter((c) => c.status === 'planned'));
  protected readonly isIam = computed(() => this.auth.me()?.role === 'iam_engineer');

  async ngOnInit(): Promise<void> {
    try {
      this.catalog.set(await this.api.catalog());
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }
}
