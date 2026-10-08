import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../auth/auth.service';
import { ApiService, apiError } from '../shared/api.service';
import { ComposerComponent } from '../shared/composer.component';
import { LiveSession } from '../shared/live-stream.service';
import { ConnectorType, Role } from '../shared/models';
import { StatusChipsComponent } from '../shared/status-chips.component';
import { ThreadComponent } from '../shared/thread.component';

interface OwnerStep {
  index: number;
  text: string;
  read_only: boolean;
  state: 'pending' | 'current' | 'done' | 'failed';
}

/**
 * One screen per role over the same live session (FR-003, FR-006–FR-006d), following the design canvas: the header
 * with the status chips, the role's side panel (source + SailPoint actions for the IAM engineer; setup steps + the
 * values the agent fills in for the application owner), and "Conversations" with the viewer's own thread (writable,
 * with suggestions) beside the other participant's thread (view only). Connector-specific wording comes from the
 * catalog entry, never hard-coded (SC-009).
 */
@Component({
  selector: 'app-session-screen',
  imports: [StatusChipsComponent, ThreadComponent, ComposerComponent, RouterLink, DatePipe],
  providers: [LiveSession],
  template: `
    @if (error()) {
      <p class="error page" role="alert">{{ error() }}</p>
    } @else if (live.session(); as s) {
      <section class="head">
        <div class="title">
          <span class="muted" i18n="@@session.label">Onboarding session</span>
          <h1>{{ s.title }}</h1>
        </div>
        <span class="chip type"><span class="muted" i18n="@@session.connector">Connector type</span> <b>{{ connector()?.name ?? s.connector_type }}</b></span>
        <app-status-chips [steps]="s.steps" [firstStepLabel]="connector()?.first_step_label ?? defaultFirstStep" />
        <span class="presence">
          <span class="dot" [class.on]="otherOnline()"></span>
          {{ otherRoleLabel() }} {{ otherOnline() ? online : offline }}
          @if (!live.connected()) { · <span class="warn" i18n="@@session.reconnecting">reconnecting…</span> }
        </span>
      </section>

      <div class="layout">
        <aside class="side">
          @if (role() === 'iam_engineer') {
            <section class="card" aria-label="Source details" i18n-aria-label="@@side.sourceAria">
              <h2 i18n="@@side.source">Source</h2>
              <dl>
                <dt i18n="@@side.connector">Connector</dt><dd>{{ connector()?.name }} <a routerLink="/catalog" i18n="@@side.catalog">catalog</a></dd>
                @for (f of connector()?.session_fields ?? []; track f.name) {
                  @if (show(s.details[f.name])) { <dt>{{ f.label }}</dt><dd class="mono">{{ fmt(s.details[f.name]) }}</dd> }
                }
                <dt i18n="@@side.tenant">Tenant</dt><dd class="mono">{{ s.tenant?.api_host }}</dd>
                <dt i18n="@@side.externalId">External ID</dt><dd class="mono">{{ s.tenant?.external_id ?? '—' }}</dd>
                <dt i18n="@@side.sourceId">Source in SailPoint</dt><dd class="mono">{{ s.source?.id ?? notYet }}</dd>
                <dt i18n="@@side.access">Access</dt><dd i18n="@@side.accessValue">read-only (aggregation)</dd>
              </dl>
            </section>
            <section class="card actions" aria-label="SailPoint actions" i18n-aria-label="@@side.actionsAria">
              <h2 i18n="@@side.actions">SailPoint actions</h2>
              <p class="muted small" i18n="@@side.actionsHint">Every change the agent made, and who ordered it.</p>
              <ol>
                @for (a of live.actions(); track a.id) {
                  <li [class.failed]="a.result === 'failed'">
                    @if (a.result === 'ok') { <svg class="icon ok" viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"></path></svg> }
                    @else { <svg class="icon bad" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
                    <span><b>{{ actionLabel(a.action) }}</b> {{ a.source?.name }}
                      <small>{{ orderedBy }} {{ a.ordered_by.display_name }} · {{ a.at | date: 'HH:mm' }}
                        @if (a.trigger === 'application_owner_confirmation') { · <span i18n="@@side.rerun">rerun after the application owner confirmed a fix</span> }
                      </small>
                      @if (a.error) { <small class="error">{{ a.error }}</small> }
                    </span>
                  </li>
                } @empty {
                  <li class="muted small" i18n="@@side.noActions">No changes yet.</li>
                }
              </ol>
              @if (s.check_order) {
                <p class="muted small" i18n="@@side.standingOrder">Your order to run the checks stands: the agent reruns them when the application owner confirms a fix.</p>
              }
              @if (s.status === 'open') {
                <button type="button" (click)="finish()" i18n="@@session.finish">Finish session</button>
              }
            </section>
          } @else {
            <section class="card" aria-label="Setup steps" i18n-aria-label="@@owner.stepsAria">
              <h2>{{ stepsTitle() }}</h2>
              <p class="muted small" i18n="@@owner.stepsHint">Run each step where the agent says and paste the output back. The agent never acts on your application and never needs its credentials.</p>
              <ol class="steps">
                @for (st of ownerSteps(); track st.index) {
                  <li [class]="st.state" [attr.data-state]="st.state">
                    <span class="n">
                      @switch (st.state) {
                        @case ('done') { <svg class="icon ok" viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"></path></svg> }
                        @case ('failed') { <svg class="icon bad" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
                        @default { {{ st.index }} }
                      }
                    </span>
                    <span class="t">{{ st.text }}</span>
                    <span class="tag" [class.ro]="st.read_only" [class.ch]="!st.read_only">{{ st.read_only ? readOnly : change }}</span>
                  </li>
                } @empty {
                  <li class="muted small empty" i18n="@@owner.noSteps">Ask the agent what to set up first.</li>
                }
              </ol>
            </section>
            <section class="card" aria-label="Session values" i18n-aria-label="@@owner.valuesAria">
              <h2 i18n="@@owner.values">Values the agent fills in for you</h2>
              <dl>
                @for (f of connector()?.session_fields ?? []; track f.name) {
                  @if (show(s.details[f.name])) { <dt>{{ f.label }}</dt><dd class="mono">{{ fmt(s.details[f.name]) }}</dd> }
                }
                <dt i18n="@@side.externalId">External ID</dt><dd class="mono">{{ s.tenant?.external_id ?? '—' }}</dd>
                <dt i18n="@@side.tenant">Tenant</dt><dd class="mono">{{ s.tenant?.name }}</dd>
              </dl>
            </section>
          }
        </aside>

        <section class="conversations" aria-label="Conversations" i18n-aria-label="@@conv.aria">
          <header class="conv-head">
            <h2 i18n="@@conv.title">Conversations</h2>
            <span class="muted small">{{ convHint() }}</span>
          </header>
          <div class="threads">
            <app-thread [thread]="role()" [viewerRole]="role()" [sessionId]="s.id" [ownerLabel]="ownerLabel()"
                        [names]="names()" [emptyText]="emptyText()">
              @if (s.status === 'open') {
                <app-composer [sessionId]="s.id" [placeholder]="placeholder()" [hint]="composerHint()" />
              } @else {
                <p class="finished" i18n="@@session.finished">This session is finished. The history stays for 90 days.</p>
              }
            </app-thread>
            <app-thread [thread]="otherRole()" [viewerRole]="role()" [sessionId]="s.id" [ownerLabel]="ownerLabel()"
                        [names]="names()" [emptyText]="otherEmptyText()" />
          </div>
        </section>
      </div>
    } @else {
      <p class="muted page" i18n="@@loading">Loading…</p>
    }
  `,
  styleUrl: './session-screen.component.css',
})
export class SessionScreenComponent implements OnInit {
  readonly id = input.required<string>();
  readonly role = input.required<Role>();
  protected readonly live = inject(LiveSession);
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly connector = signal<ConnectorType | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly defaultFirstStep = $localize`:@@step.application_ready:Application ready`;
  protected readonly online = $localize`:@@presence.online:online`;
  protected readonly offline = $localize`:@@presence.offline:offline`;
  protected readonly notYet = $localize`:@@side.notYet:not created yet`;
  protected readonly orderedBy = $localize`:@@side.orderedBy:ordered by`;
  protected readonly readOnly = $localize`:@@tag.readOnly:read-only`;
  protected readonly change = $localize`:@@tag.change:change`;

  protected readonly ownerLabel = computed(() => this.connector()?.owner_label ?? $localize`:@@role.owner:Application owner`);
  protected readonly otherRole = computed<Role>(() => (this.role() === 'iam_engineer' ? 'application_owner' : 'iam_engineer'));
  protected readonly otherOnline = computed(() => this.live.online()[this.otherRole()]);
  protected readonly otherRoleLabel = computed(() =>
    this.otherRole() === 'iam_engineer' ? $localize`:@@role.iam:IAM engineer` : this.ownerLabel(),
  );
  protected readonly names = computed<Partial<Record<Role, string>>>(() => {
    const p = this.live.session()?.participants;
    return {
      iam_engineer: p?.iam_engineer?.display_name,
      application_owner: p?.application_owner?.display_name,
    };
  });
  protected readonly stepsTitle = computed(() => {
    const app = this.connector()?.application_label ?? 'the application';
    return $localize`:@@owner.steps:Your steps in ${app}:app:`;
  });
  protected readonly ownerSteps = computed<OwnerStep[]>(() => {
    const seen = new Map<number, { index: number; text: string; read_only: boolean }>();
    for (const m of this.live.messages()) for (const s of m.application_steps ?? []) seen.set(s.index, s);
    const list = [...seen.values()].sort((a, b) => a.index - b.index);
    const ready = this.live.session()?.steps.application_ready;
    return list.map((s, i) => ({
      ...s,
      state: i < list.length - 1 || ready === 'passed' ? 'done' : ready === 'failed' ? 'failed' : 'current',
    }));
  });
  protected readonly convHint = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@conv.hintIam:One thread per person. The agent works between them; only you can order SailPoint changes.`
      : $localize`:@@conv.hintOwner:One thread per person. The agent works between them; SailPoint changes are the IAM engineer's to order.`,
  );
  protected readonly placeholder = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@composer.iamPlaceholder:Order the agent: create the connector, rerun the connection check, start aggregation…`
      : $localize`:@@composer.ownerPlaceholder:Paste command output, or ask the agent about a step…`,
  );
  protected readonly composerHint = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@composer.iamHint:Your order is the approval: the agent changes SailPoint right away and records it under SailPoint actions. Keys, tokens and passwords are masked before anyone sees them.`
      : $localize`:@@composer.ownerHint:Access keys, secret keys and tokens are masked before anyone sees them. A screenshot that shows a secret is held and you're asked for a cropped one.`,
  );
  protected readonly emptyText = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@chat.emptyIam:Tell the agent to create the connector when the session details are right.`
      : $localize`:@@chat.emptyOwner:Ask the agent what to set up first. It will give you each step with the values filled in.`,
  );
  protected readonly otherEmptyText = computed(() =>
    $localize`:@@chat.emptyOther:Nothing yet in ${this.otherRoleLabel()}:role:'s thread.`,
  );

  async ngOnInit(): Promise<void> {
    try {
      await this.live.open(this.id());
      const s = this.live.session();
      const catalog = await this.api.catalog();
      this.connector.set(catalog.find((c) => c.id === s?.connector_type) ?? null);
      const me = this.auth.me();
      // The screen must match the signed-in role (FR-003); the API enforces this too.
      if (me && me.role !== this.role()) {
        void this.router.navigate(['/sessions', this.id(), me.role === 'iam_engineer' ? 'iam' : 'owner']);
      }
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  protected show(v: unknown): boolean {
    return !(v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length));
  }

  protected fmt(v: unknown): string {
    return Array.isArray(v) ? v.join(', ') : String(v);
  }

  protected actionLabel(a: string): string {
    return (
      {
        create_source: $localize`:@@action.create:Created source`,
        configure_source: $localize`:@@action.configure:Configured source`,
        connection_check: $localize`:@@action.check:Connection check`,
        aggregate: $localize`:@@action.aggregate:Aggregation`,
        test_connection: $localize`:@@action.test:Test Connection`,
        delete_source: $localize`:@@action.delete:Deleted source`,
      }[a] ?? a
    );
  }

  protected async finish(): Promise<void> {
    try {
      const s = await this.api.finishSession(this.id());
      this.live.session.update((cur) => (cur ? { ...cur, status: s.status } : cur));
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }
}
