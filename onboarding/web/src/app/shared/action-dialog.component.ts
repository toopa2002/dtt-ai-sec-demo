import { DatePipe } from '@angular/common';
import { Component, computed, ElementRef, inject, input, output, signal, viewChild } from '@angular/core';
import { ApiService, apiError } from './api.service';
import { Action } from './models';

/**
 * One SailPoint action in detail (FR-020a, US3 #14, SC-017, research R24), per the design canvas: what the agent sent,
 * what SailPoint returned, the agent's diagnosis and who ordered it, why and when, with "Show in thread" and "Copy
 * details". A native modal <dialog>: Escape, the close buttons and a click on the backdrop close it. IAM engineer only;
 * everything shown was masked by the API.
 */
@Component({
  selector: 'app-action-dialog',
  imports: [DatePipe],
  template: `
    <dialog #dlg aria-labelledby="act-title" (close)="closed.emit()" (click)="onBackdrop($event)">
      @if (action(); as a) {
        <div class="frame">
          <header>
            <span class="ico" [class.bad]="a.result === 'failed'" [class.run]="a.result === 'running'" aria-hidden="true">
              @switch (a.result) {
                @case ('ok') { <svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"></path></svg> }
                @case ('failed') { <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
                @default { <svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9"></path></svg> }
              }
            </span>
            <div class="titles">
              <h2 id="act-title">{{ label() }} · {{ a.outcome ?? a.result }}</h2>
              <span class="muted">
                @if (a.source?.name) { <ng-container i18n="@@act.source">Source</ng-container> <b>{{ a.source?.name }}</b> · }
                {{ a.at | date: 'medium' }}
                @if (a.duration_ms !== null && a.duration_ms !== undefined) { · {{ seconds(a.duration_ms) }} }
              </span>
            </div>
            <button type="button" class="close" (click)="close()" aria-label="Close details" i18n-aria-label="@@act.closeAria">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>
            </button>
          </header>

          <div class="body">
            <section class="context" aria-label="Context" i18n-aria-label="@@act.context">
              <div><span class="k" i18n="@@act.orderedBy">Ordered by</span><b>{{ a.ordered_by.display_name }}</b></div>
              <div><span class="k" i18n="@@act.why">Why it ran</span>
                <b>{{ a.trigger === 'application_owner_confirmation' ? rerunText : orderText }}</b></div>
              <div><span class="k" i18n="@@act.order">Order message</span>
                @if (a.order_message_id) {
                  <button type="button" class="link" (click)="showInThread.emit(a.order_message_id!); close()" i18n="@@act.showInThread">Show in thread</button>
                } @else { <span class="muted" i18n="@@act.notRecorded">not recorded</span> }
              </div>
            </section>

            <section aria-labelledby="req-h">
              <h3 id="req-h" i18n="@@act.request">Request the agent sent</h3>
              @if (a.request_missing) {
                <p class="muted" i18n="@@act.requestMissing">Not recorded (an action from before details were kept).</p>
              } @else {
                <dl>
                  <dt i18n="@@act.action">Action</dt><dd>{{ label() }}</dd>
                  @if (a.source?.id) { <dt i18n="@@act.sourceId">Source</dt><dd class="mono">{{ a.source?.name }} · {{ a.source?.id }}</dd> }
                  @for (row of requestRows(); track row[0]) { <dt>{{ row[0] }}</dt><dd class="mono">{{ row[1] }}</dd> }
                  <dt i18n="@@act.credentials">Credentials</dt><dd class="muted" i18n="@@act.credentialsValue">Tenant service credential (never shown)</dd>
                </dl>
              }
            </section>

            <section aria-labelledby="res-h">
              <h3 id="res-h" i18n="@@act.response">Response from SailPoint</h3>
              <dl>
                <dt i18n="@@act.result">Result</dt><dd [class.bad]="a.result === 'failed'"><b>{{ resultText() }}</b></dd>
                @if (a.duration_ms !== null && a.duration_ms !== undefined) { <dt i18n="@@act.duration">Duration</dt><dd>{{ seconds(a.duration_ms) }}</dd> }
                @if (a.response?.counts?.accounts !== undefined) { <dt i18n="@@act.accounts">Accounts read</dt><dd>{{ a.response?.counts?.accounts }}</dd> }
                @if (a.response?.counts?.entitlements !== undefined) { <dt i18n="@@act.entitlements">Entitlements</dt><dd>{{ a.response?.counts?.entitlements }}</dd> }
                @if (a.response?.counts?.users !== undefined) { <dt i18n="@@act.users">Users</dt><dd>{{ a.response?.counts?.users }}</dd> }
                @if (a.response?.counts?.service_principals !== undefined) { <dt i18n="@@act.sps">Service principals</dt><dd>{{ a.response?.counts?.service_principals }}</dd> }
                @if (a.response?.counts?.ai_agents !== undefined) { <dt i18n="@@act.agents">AI agents</dt><dd>{{ a.response?.counts?.ai_agents }}</dd> }
                @if (a.summary) { <dt i18n="@@act.summary">Summary</dt><dd class="mono">{{ a.summary }}</dd> }
                @for (t of taskRows(); track t[0]) { <dt i18n="@@act.task">Task</dt><dd class="mono">{{ t[0] }} · {{ t[1] }}</dd> }
                @if (a.response?.error || a.error) {
                  <dt i18n="@@act.error">Error</dt><dd><pre>{{ a.response?.error || a.error }}</pre></dd>
                }
                @if (a.response_missing) { <dt></dt><dd class="muted" i18n="@@act.responseMissing">Other details not recorded.</dd> }
              </dl>
            </section>

            @if (a.diagnosis) {
              <section class="diag" aria-labelledby="diag-h">
                <h3 id="diag-h" i18n="@@act.diagnosis">Agent's diagnosis</h3>
                <p>{{ a.diagnosis }}</p>
              </section>
            }
            <p class="muted small" i18n="@@act.footnote">Shown to the IAM engineer only. Keys, tokens and credentials are masked.</p>
          </div>

          <footer>
            @if (copied()) { <span class="muted small" role="status" i18n="@@act.copied">Copied.</span> }
            <button type="button" (click)="copy()" i18n="@@act.copy">Copy details</button>
            <button type="button" class="primary" (click)="close()" i18n="@@act.close">Close</button>
          </footer>
        </div>
      } @else if (error()) {
        <div class="frame"><p class="error" role="alert">{{ error() }}</p>
          <footer><button type="button" class="primary" (click)="close()" i18n="@@act.close">Close</button></footer></div>
      } @else {
        <div class="frame"><p class="muted" i18n="@@loading">Loading…</p></div>
      }
    </dialog>
  `,
  styles: `
    dialog { padding: 0; border: 0; border-radius: 12px; width: min(860px, calc(100vw - 2rem)); max-height: calc(100vh - 4rem);
      background: var(--surface); color: var(--text); box-shadow: 0 24px 64px rgb(0 0 0 / 0.35); }
    dialog::backdrop { background: rgb(0 0 0 / 0.55); }
    .frame { display: flex; flex-direction: column; max-height: calc(100vh - 4rem); }
    header { display: flex; align-items: flex-start; gap: 0.75rem; padding: 1rem 1.3rem; border-bottom: 1px solid var(--border); }
    .titles { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; }
    h2 { margin: 0; font-size: 1.1rem; }
    h3 { margin: 0 0 0.4rem; font-size: 0.9rem; }
    .ico svg, .close svg { width: 1.3rem; height: 1.3rem; fill: none; stroke: var(--ok); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
    .ico.bad svg { stroke: var(--bad); }
    .ico.run svg { stroke: var(--info); }
    .close { margin-left: auto; min-width: 40px; min-height: 40px; display: inline-flex; align-items: center; justify-content: center; }
    .close svg { stroke: var(--text); width: 1rem; height: 1rem; }
    .body { overflow-y: auto; padding: 1rem 1.3rem; display: flex; flex-direction: column; gap: 1.1rem; }
    .context { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.6rem; }
    .context > div { display: flex; flex-direction: column; padding: 0.55rem 0.75rem; border-radius: 8px; background: var(--surface-2); font-size: 0.85rem; }
    .k { font-size: 0.72rem; color: var(--muted); }
    .link { background: none; border: 0; padding: 0; min-height: 0; color: var(--info-strong); font-weight: 600; text-decoration: underline; cursor: pointer; text-align: left; }
    dl { margin: 0; display: grid; grid-template-columns: minmax(8rem, 11rem) minmax(0, 1fr); font-size: 0.85rem;
      border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    dt, dd { margin: 0; padding: 0.45rem 0.7rem; border-bottom: 1px solid var(--border); }
    dt { background: var(--surface-2); color: var(--muted); }
    dd { min-width: 0; overflow-wrap: anywhere; }
    dd.bad { color: var(--bad); }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 0.78rem; padding: 0.5rem; border-radius: 6px;
      background: color-mix(in srgb, var(--bad) 8%, transparent); border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent); }
    .diag { padding: 0.75rem 0.9rem; border-radius: 8px; background: var(--aws-fill); border: 1px solid var(--info); }
    .diag h3 { color: var(--info-strong); }
    .diag p { margin: 0; font-size: 0.86rem; line-height: 1.45; }
    footer { display: flex; align-items: center; gap: 0.5rem; justify-content: flex-end; padding: 0.8rem 1.3rem; border-top: 1px solid var(--border); }
    footer button { min-height: 44px; }
    .small { font-size: 0.76rem; }
    .error { padding: 1rem 1.3rem; margin: 0; }
  `,
})
export class ActionDialogComponent {
  readonly sessionId = input.required<string>();
  /** The action to show; the dialog fetches its full details when it opens. */
  readonly summary = input<Action | null>(null);
  readonly closed = output<void>();
  readonly showInThread = output<string>();
  private readonly api = inject(ApiService);
  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');
  protected readonly action = signal<Action | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly copied = signal(false);
  protected readonly orderText = $localize`:@@act.byOrder:The IAM engineer's order`;
  protected readonly rerunText = $localize`:@@act.byConfirmation:Rerun after the application owner confirmed a fix`;

  readonly label = computed(() => actionLabel(this.action()?.action ?? ''));
  readonly requestRows = computed(() => rows(this.action()?.request ?? {}));
  readonly taskRows = computed(() => Object.entries(this.action()?.response?.task_states ?? {}));
  readonly resultText = computed(() => {
    const r = this.action()?.result;
    if (r === 'tenant_limitation') return $localize`:@@act.limitation:Tenant limitation: start it in ISC`;
    return r === 'ok' ? $localize`:@@act.ok:Succeeded` : r === 'failed' ? $localize`:@@act.failed:Failed` : $localize`:@@act.running:Running`;
  });

  async open(): Promise<void> {
    const s = this.summary();
    this.action.set(s);
    this.error.set(null);
    this.copied.set(false);
    const el = this.dlg().nativeElement;
    if (!el.open) el.showModal();
    if (!s) return;
    try {
      this.action.set(await this.api.action(this.sessionId(), s.id));
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  close(): void {
    const el = this.dlg().nativeElement;
    if (el.open) el.close();
  }

  protected onBackdrop(ev: MouseEvent): void {
    if (ev.target === this.dlg().nativeElement) this.close();  // a click outside the frame
  }

  protected seconds(ms: number): string {
    return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
  }

  protected async copy(): Promise<void> {
    const a = this.action();
    if (!a) return;
    const lines = [
      `${this.label()} · ${a.outcome ?? a.result}`,
      `Source: ${a.source?.name ?? '-'} ${a.source?.id ?? ''}`.trim(),
      `When: ${a.at}${a.duration_ms ? ` (${this.seconds(a.duration_ms)})` : ''}`,
      `Ordered by: ${a.ordered_by.display_name} (${a.trigger === 'application_owner_confirmation' ? this.rerunText : this.orderText})`,
      'Request:', ...this.requestRows().map(([k, v]) => `  ${k}: ${v}`),
      'Response:', `  result: ${a.result}`,
      ...this.taskRows().map(([k, v]) => `  task ${k}: ${v}`),
      ...(a.response?.counts?.accounts !== undefined ? [`  accounts: ${a.response.counts.accounts}`] : []),
      ...(a.response?.error || a.error ? [`  error: ${a.response?.error || a.error}`] : []),
      ...(a.diagnosis ? ['Diagnosis:', `  ${a.diagnosis}`] : []),
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      this.copied.set(true);
    } catch {
      this.copied.set(false);
    }
  }
}

export function actionLabel(a: string): string {
  return (
    {
      create_source: $localize`:@@action.create:Created source`,
      configure_source: $localize`:@@action.configure:Configured source`,
      connection_check: $localize`:@@action.check:Connection check`,
      aggregate: $localize`:@@action.aggregate:Aggregation`,
      test_connection: $localize`:@@action.test:Test Connection`,
      delete_source: $localize`:@@action.delete:Deleted source`,
      // spec 002
      aggregate_entitlements: $localize`:@@action.aggEnt:Entitlement aggregation`,
      aggregate_accounts: $localize`:@@action.aggAcct:Account aggregation`,
      aggregate_datasets: $localize`:@@action.aggData:AI agent aggregation`,
      adopt_source: $localize`:@@action.adopt:Extending source`,
      ensure_schema_attributes: $localize`:@@action.schema:Account model`,
      set_dataset_schedule: $localize`:@@action.schedule:Dataset schedule`,
      set_provisioning_policy: $localize`:@@action.policy:Account-creation policy`,
      set_correlation: $localize`:@@action.correlation:Account matching`,
      apply_application_secret: $localize`:@@action.secret:Applied new secret`,
      set_machine_classification: $localize`:@@action.classification:Machine account classification`,
    }[a] ?? a
  );
}

function rows(obj: Record<string, unknown>, prefix = ''): [string, string][] {
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) out.push(...rows(v as Record<string, unknown>, key));
    else out.push([key, Array.isArray(v) ? v.join(', ') : String(v)]);
  }
  return out;
}
