import { Component, inject, input, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService, apiError } from '../shared/api.service';
import { ConnectorType, Person, Tenant } from '../shared/models';

/** New session for an available connector type (FR-005): tenant, the type's required details, the invited owner. */
@Component({
  selector: 'app-new-session',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="wrap">
      <a routerLink="/catalog" class="back" i18n="@@new.back">← Connector catalog</a>
      @if (connector(); as c) {
        <h1 i18n="@@new.title">New {{ c.name }} session</h1>
        <p class="muted">{{ c.description }}</p>
        <form class="card" (submit)="$event.preventDefault(); create()">
          <label for="tenant" i18n="@@new.tenant">SailPoint tenant</label>
          <select id="tenant" name="tenant" [(ngModel)]="tenantId" required>
            <option value="" disabled i18n="@@new.pickTenant">Choose a tenant</option>
            @for (t of tenants(); track t.id) {
              <option [value]="t.id" [disabled]="t.status !== 'usable'">{{ t.name }} — {{ t.api_host }}{{ t.status !== 'usable' ? ' (' + unusable + ')' : '' }}</option>
            }
          </select>
          @for (f of c.session_fields; track f.name) {
            <label [for]="'f-' + f.name">{{ f.label }}@if (!f.required) { <span class="muted" i18n="@@new.optional"> (optional)</span> }</label>
            <input [id]="'f-' + f.name" [name]="f.name" [(ngModel)]="values[f.name]" [required]="f.required"
                   [placeholder]="hint(f.type)" [attr.aria-describedby]="errors()[f.name] ? 'e-' + f.name : null" />
            @if (errors()[f.name]) { <p class="error" [id]="'e-' + f.name">{{ errors()[f.name] }}</p> }
          }
          <label for="owner">{{ c.owner_label }}</label>
          <select id="owner" name="owner" [(ngModel)]="ownerId">
            <option value="" i18n="@@new.inviteLater">Invite later</option>
            @for (p of owners(); track p.id) { <option [value]="p.id">{{ p.display_name }} ({{ p.username }})</option> }
          </select>
          @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
          <button type="submit" class="primary" [disabled]="busy() || !tenantId" i18n="@@new.create">Start session</button>
        </form>
      } @else if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
    </div>
  `,
  styles: `
    .wrap { max-width: 40rem; margin: 0 auto; padding: 1.5rem; width: 100%; }
    .back { font-size: 0.85rem; }
    h1 { margin: 0.75rem 0 0.25rem; font-size: 1.4rem; }
    form { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 1rem; }
    label { margin-top: 0.5rem; }
    .primary { margin-top: 1rem; min-height: 44px; align-self: flex-start; }
    .error { margin: 0; font-size: 0.82rem; }
  `,
})
export class NewSessionComponent implements OnInit {
  readonly type = input.required<string>();
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly connector = signal<ConnectorType | null>(null);
  protected readonly tenants = signal<Pick<Tenant, 'id' | 'name' | 'api_host' | 'status'>[]>([]);
  protected readonly owners = signal<Person[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly errors = signal<Record<string, string>>({});
  protected readonly busy = signal(false);
  protected readonly unusable = $localize`:@@new.unusable:credential not working`;
  protected tenantId = '';
  protected ownerId = '';
  protected values: Record<string, string> = {};

  async ngOnInit(): Promise<void> {
    try {
      const [catalog, tenants, owners] = await Promise.all([
        this.api.catalog(),
        this.api.sessionTenants(),
        this.api.applicationOwners(),
      ]);
      const c = catalog.find((x) => x.id === this.type());
      if (!c || c.status !== 'available') {
        this.error.set($localize`:@@new.planned:This connector type isn't available yet.`);
        return;
      }
      this.connector.set(c);
      this.tenants.set(tenants);
      this.owners.set(owners);
      for (const f of c.session_fields) if (typeof f.default === 'string' && !f.default.includes('{')) this.values[f.name] = f.default;
      if (tenants.length === 1 && tenants[0].status === 'usable') this.tenantId = tenants[0].id;
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  protected hint(type: string): string {
    return (
      {
        aws_account_id: '111122223333',
        aws_account_id_list: '111122223333, 444455556666',
        region: 'ap-southeast-1',
        region_list: 'ap-southeast-1, us-east-1',
      }[type] ?? ''
    );
  }

  protected async create(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.errors.set({});
    try {
      const s = await this.api.createSession({
        connector_type: this.type(),
        tenant_id: this.tenantId,
        details: this.values,
        application_owner_id: this.ownerId || undefined,
      });
      await this.router.navigate(['/sessions', s.id, 'iam']);
    } catch (err) {
      const e = apiError(err);
      // validation_failed messages are "field: problem; field: problem" — show them next to the fields.
      const perField: Record<string, string> = {};
      for (const part of e.message.split('; ')) {
        const [k, ...rest] = part.split(': ');
        if (this.connector()?.session_fields.some((f) => f.name === k)) perField[k] = rest.join(': ');
      }
      this.errors.set(perField);
      this.error.set(Object.keys(perField).length ? $localize`:@@new.fix:Fix the highlighted fields.` : e.message);
    } finally {
      this.busy.set(false);
    }
  }
}
