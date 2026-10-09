import { Component, computed, inject, input, OnInit, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService, apiError } from '../shared/api.service';
import { Capability, ConnectorType, Person, SessionField, Tenant } from '../shared/models';

/**
 * New session for an available connector type (FR-005): tenant, the type's required details, the invited owner.
 * Spec 002 (canvas "New Entra ID session"): a type with capabilities gets capability cards, the fields that belong to
 * a capability inside its card, a warning to accept (provisioning) before Start session, the new / extend choice, and
 * a "what happens" panel computed here from the catalog (research R15).
 */
@Component({
  selector: 'app-new-session',
  imports: [FormsModule, NgTemplateOutlet, RouterLink],
  template: `
    <div class="wrap" [class.wide]="hasCapabilities()">
      <a routerLink="/catalog" class="back" i18n="@@new.back">← Connector catalog</a>
      @if (connector(); as c) {
        <h1 i18n="@@new.title">New {{ c.name }} session</h1>
        <p class="muted">{{ c.description }}</p>
        <div class="layout">
          <form class="main" (submit)="$event.preventDefault(); create()">
            <section class="card">
              <h2 i18n="@@new.people">Tenants and people</h2>
              <label for="tenant" i18n="@@new.tenant">SailPoint tenant</label>
              <select id="tenant" name="tenant" [(ngModel)]="tenantId" required>
                <option value="" disabled i18n="@@new.pickTenant">Choose a tenant</option>
                @for (t of tenants(); track t.id) {
                  <option [value]="t.id" [disabled]="t.status !== 'usable'">{{ t.name }} — {{ t.api_host }}{{ t.status !== 'usable' ? ' (' + unusable + ')' : '' }}</option>
                }
              </select>
              @for (f of plainFields(); track f.name) {
                <ng-container *ngTemplateOutlet="field; context: { $implicit: f }"></ng-container>
              }
              <label for="owner">{{ c.owner_label }}</label>
              <select id="owner" name="owner" [(ngModel)]="ownerId">
                <option value="" i18n="@@new.inviteLater">Invite later</option>
                @for (p of owners(); track p.id) { <option [value]="p.id">{{ p.display_name }} ({{ p.username }})</option> }
              </select>
            </section>

            @if (hasCapabilities()) {
              <fieldset class="card">
                <legend i18n="@@new.capabilities">Capabilities</legend>
                <p class="muted small" i18n="@@new.capsHelp">Each one adds steps for the {{ c.owner_label }}, settings on the source and its own check. Pick only what you need.</p>
                <div class="caps">
                  @for (cap of c.capabilities ?? []; track cap.id) {
                    <div class="cap" [class.on]="isChosen(cap.id)" [class.writes]="cap.tag === 'writes'" [class.off]="cap.enabled === false">
                      <label class="cap-head">
                        <input type="checkbox" [checked]="isChosen(cap.id)" [disabled]="cap.always || cap.enabled === false"
                               (change)="toggle(cap.id)" [attr.aria-describedby]="'cap-' + cap.id" />
                        <span>
                          <b>{{ cap.label }}</b>
                          @if (cap.always) { <span class="muted small" i18n="@@new.always"> always on</span> }
                          @if (cap.tag === 'writes') { <span class="writes-tag" i18n="@@new.writesTag"> writes to the directory</span> }
                          @else { <span class="muted small" i18n="@@new.readOnly"> read-only</span> }
                          @if (cap.enabled === false) { <span class="muted small" i18n="@@new.soon"> coming soon</span> }
                        </span>
                      </label>
                      @if (cap.summary) { <p class="muted small" [id]="'cap-' + cap.id">{{ cap.summary }}</p> }
                      @if (isChosen(cap.id)) {
                        @if (cap.warning) {
                          <div class="warning" role="group" [attr.aria-label]="cap.label">
                            <p>{{ warningText(cap) }}</p>
                            <label class="accept"><input type="checkbox" [checked]="accepted().has(cap.id)" (change)="accept(cap.id)" />
                              <span i18n="@@new.accept">I understand and accept this for the session</span></label>
                          </div>
                        }
                        @for (f of fieldsFor(cap.id); track f.name) {
                          <ng-container *ngTemplateOutlet="field; context: { $implicit: f }"></ng-container>
                        }
                      }
                    </div>
                  }
                </div>
                @if (errors()['capabilities']) { <p class="error">{{ errors()['capabilities'] }}</p> }
              </fieldset>
            }

            @if (modeField(); as m) {
              <fieldset class="card">
                <legend i18n="@@new.source">Source</legend>
                <label class="radio"><input type="radio" name="mode" value="new" [(ngModel)]="values[m.name]" />
                  <span i18n="@@new.modeNew">Create a new source</span></label>
                <label class="radio"><input type="radio" name="mode" value="extend" [(ngModel)]="values[m.name]" />
                  <span i18n="@@new.modeExtend">Extend an existing {{ c.name }} source with the capabilities above</span></label>
                <p class="muted small" i18n="@@new.modeHelp">If a source for this tenant already exists, the agent names it and its owner when the session starts and offers to extend it. An extended source keeps its secret and is never deleted.</p>
              </fieldset>
            }

            @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
            <div class="actions">
              <button type="submit" class="primary" [disabled]="busy() || !tenantId || !warningsAccepted()" i18n="@@new.create">Start session</button>
              <a routerLink="/catalog" class="secondary" i18n="@@new.cancel">Cancel</a>
              @if (!warningsAccepted()) { <span class="muted small" i18n="@@new.acceptFirst">Accept the {{ pendingWarning() }} warning to continue.</span> }
            </div>
          </form>

          @if (hasCapabilities()) {
            <aside class="side" aria-label="What this session will do" i18n-aria-label="@@new.sideAria">
              <section class="card">
                <h2 i18n="@@new.ownerWillBeAsked">The {{ c.owner_label }} will be asked to</h2>
                <ol class="list">
                  <li i18n="@@new.registerLine">Register the app and grant admin consent</li>
                  @for (line of ownerLines(); track line) { <li>{{ line }}</li> }
                  @if (c.secret) { <li i18n="@@new.secretLine">Put the client secret's Value into the secret field, never the chat</li> }
                </ol>
                <div class="chips">
                  <span class="chip-s" i18n="@@new.ownerSteps">{{ ownerStepCount() }} owner steps</span>
                  @if (writesCount()) { <span class="chip-s warn" i18n="@@new.writesCount">{{ writesCount() }} capability writes</span> }
                </div>
              </section>
              <section class="card">
                <h2 i18n="@@new.agentWill">The agent will, on your order</h2>
                <ol class="list">
                  @for (t of agentSteps(); track t) { <li>{{ t }}</li> }
                </ol>
                <p class="muted small" i18n="@@new.agentOrder">Each step runs only after the one before it passed.@if (c.isc_api) { Uses the SailPoint API {{ c.isc_api }}. }</p>
              </section>
            </aside>
          }
        </div>

        <ng-template #field let-f>
          <label [for]="'f-' + f.name">{{ f.label }}@if (!f.required && !requiredByCapability(f.name)) { <span class="muted" i18n="@@new.optional"> (optional)</span> }</label>
          @if (f.type === 'guid_list') {
            <textarea [id]="'f-' + f.name" [name]="f.name" rows="2" [(ngModel)]="values[f.name]" [placeholder]="hint(f.type)"
                      [attr.aria-describedby]="errors()[f.name] ? 'e-' + f.name : null"></textarea>
          } @else {
            <input [id]="'f-' + f.name" [name]="f.name" [(ngModel)]="values[f.name]" [required]="f.required"
                   [placeholder]="hint(f.type)" [attr.aria-describedby]="errors()[f.name] ? 'e-' + f.name : null" />
          }
          @if (errors()[f.name]) { <p class="error" [id]="'e-' + f.name">{{ errors()[f.name] }}</p> }
          @if (f.type === 'entra_tenant') { <p class="muted small" i18n="@@new.tenantHelp">The initial .onmicrosoft.com domain or the tenant ID.</p> }
        </ng-template>
      } @else if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
    </div>
  `,
  styles: `
    .wrap { max-width: 40rem; margin: 0 auto; padding: 1.5rem; width: 100%; box-sizing: border-box; }
    .wrap.wide { max-width: 87rem; }
    .back { font-size: 0.85rem; }
    h1 { margin: 0.75rem 0 0.25rem; font-size: 1.4rem; }
    h2, legend { font-size: 0.95rem; font-weight: 700; margin: 0 0 0.4rem; padding: 0; }
    .layout { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; margin-top: 1rem; }
    .main { flex: 999 1 36rem; min-width: 0; display: flex; flex-direction: column; gap: 1rem; }
    .side { flex: 1 1 18rem; max-width: 26rem; display: flex; flex-direction: column; gap: 1rem; }
    .card { display: flex; flex-direction: column; gap: 0.35rem; }
    fieldset.card { border: 1px solid var(--border); }
    label { margin-top: 0.5rem; }
    .caps { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 0.6rem; }
    .cap { border: 1px solid var(--border); border-radius: 8px; padding: 0.7rem; display: flex; flex-direction: column; gap: 0.3rem; }
    .cap.on { border-color: var(--ok); }
    .cap.on.writes { border: 2px solid var(--warn); }
    .cap.off { opacity: 0.6; }
    .cap-head { display: flex; gap: 0.5rem; align-items: flex-start; margin: 0; }
    .cap-head input, .accept input, .radio input { width: 1.1rem; height: 1.1rem; margin-top: 0.15rem; }
    .writes-tag { color: var(--warn); font-weight: 600; font-size: 0.78rem; }
    .warning { border: 1px solid var(--warn); border-radius: 6px; padding: 0.6rem; background: color-mix(in srgb, var(--warn) 10%, var(--surface)); }
    .warning p { margin: 0 0 0.4rem; font-size: 0.85rem; line-height: 1.45; }
    .accept, .radio { display: flex; gap: 0.5rem; align-items: center; font-weight: 600; margin: 0.25rem 0 0; }
    .radio { font-weight: 400; }
    .small { font-size: 0.78rem; margin: 0; }
    .actions { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; }
    .primary { min-height: 44px; }
    .secondary { min-height: 44px; display: inline-flex; align-items: center; padding: 0 1rem; border: 1px solid var(--border); border-radius: 6px; text-decoration: none; }
    .list { margin: 0; padding-left: 1.2rem; display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.88rem; line-height: 1.4; }
    .chips { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.4rem; }
    .chip-s { font-size: 0.72rem; padding: 0.05rem 0.55rem; border-radius: 999px; background: var(--surface-2); }
    .chip-s.warn { color: var(--warn); }
    .error { margin: 0; font-size: 0.82rem; }
    textarea { font: inherit; }
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
  protected readonly chosen = signal<Set<string>>(new Set());
  protected readonly accepted = signal<Set<string>>(new Set());
  protected readonly unusable = $localize`:@@new.unusable:credential not working`;
  protected tenantId = '';
  protected ownerId = '';
  protected values: Record<string, string> = {};

  protected readonly hasCapabilities = computed(() => !!this.connector()?.capabilities?.length);
  private readonly capabilityField = computed(() =>
    this.connector()?.session_fields.find((f) => f.type === 'capabilities'),
  );
  protected readonly modeField = computed(() =>
    this.connector()?.session_fields.find((f) => f.choices?.includes('extend')),
  );
  protected readonly plainFields = computed(() =>
    (this.connector()?.session_fields ?? []).filter(
      (f) => f.type !== 'capabilities' && !f.show_if && f !== this.modeField() && f.name !== 'client_id',
    ),
  );
  private readonly chosenCaps = computed(() =>
    (this.connector()?.capabilities ?? []).filter((c) => this.chosen().has(c.id)),
  );
  protected readonly ownerLines = computed(() =>
    this.chosenCaps()
      .map((c) => c.owner_summary)
      .filter((x): x is string => !!x),
  );
  protected readonly writesCount = computed(() => this.chosenCaps().filter((c) => c.tag === 'writes').length);
  private readonly previewSteps = computed(() =>
    (this.connector()?.plan_preview ?? []).filter((s) => !s.capability || this.chosen().has(s.capability)),
  );
  protected readonly ownerStepCount = computed(
    () => this.previewSteps().filter((s) => s.actor === 'application_owner').length,
  );
  protected readonly agentSteps = computed(() =>
    this.previewSteps()
      .filter((s) => s.actor === 'agent')
      .map((s) => s.title),
  );
  protected readonly pendingWarning = computed(
    () => this.chosenCaps().find((c) => c.warning && !this.accepted().has(c.id))?.label.toLowerCase() ?? '',
  );
  protected readonly warningsAccepted = computed(() => !this.pendingWarning());

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
      this.chosen.set(new Set((c.capabilities ?? []).filter((x) => x.always).map((x) => x.id)));
      if (tenants.length === 1 && tenants[0].status === 'usable') this.tenantId = tenants[0].id;
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  protected isChosen(id: string): boolean {
    return this.chosen().has(id);
  }

  protected toggle(id: string): void {
    const next = new Set(this.chosen());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.chosen.set(next);
  }

  protected accept(id: string): void {
    const next = new Set(this.accepted());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.accepted.set(next);
  }

  protected fieldsFor(capability: string): SessionField[] {
    return (this.connector()?.session_fields ?? []).filter((f) => f.show_if === capability);
  }

  protected requiredByCapability(name: string): boolean {
    return this.chosenCaps().some((c) => c.requires_fields?.includes(name));
  }

  protected warningText(cap: Capability): string {
    const domain = this.values['tenant_domain'] || $localize`:@@new.thisDirectory:this directory`;
    return (cap.warning ?? '').replace('{tenant_domain}', domain);
  }

  protected hint(type: string): string {
    return (
      {
        aws_account_id: '111122223333',
        aws_account_id_list: '111122223333, 444455556666',
        region: 'ap-southeast-1',
        region_list: 'ap-southeast-1, us-east-1',
        entra_tenant: 'contoso.onmicrosoft.com',
        guid_list: '00000000-0000-0000-0000-000000000000',
        domain: 'contoso.com',
        country_code: 'TH',
      }[type] ?? ''
    );
  }

  protected async create(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.errors.set({});
    const details: Record<string, unknown> = { ...this.values };
    const capField = this.capabilityField();
    if (capField) details[capField.name] = [...this.chosen()];
    try {
      const s = await this.api.createSession({
        connector_type: this.type(),
        tenant_id: this.tenantId,
        details,
        application_owner_id: this.ownerId || undefined,
        accept_warnings: [...this.accepted()].filter((id) => this.chosen().has(id)),
      });
      await this.router.navigate(['/sessions', s.id, 'iam']);
    } catch (err) {
      const e = apiError(err);
      // Field messages come as `errors` (spec 002) or, from older responses, as "field: problem; field: problem".
      const perField: Record<string, string> = { ...(e.errors ?? {}) };
      if (!e.errors) {
        for (const part of e.message.split('; ')) {
          const [k, ...rest] = part.split(': ');
          if (this.connector()?.session_fields.some((f) => f.name === k)) perField[k] = rest.join(': ');
        }
      }
      this.errors.set(perField);
      this.error.set(
        perField['warnings_accepted'] ??
          (Object.keys(perField).length ? $localize`:@@new.fix:Fix the highlighted fields.` : e.message),
      );
    } finally {
      this.busy.set(false);
    }
  }
}
