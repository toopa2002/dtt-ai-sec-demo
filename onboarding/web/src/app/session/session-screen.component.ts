import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { afterNextRender, Component, computed, DestroyRef, effect, inject, Injector, input, OnInit, signal, viewChild,
  viewChildren } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../auth/auth.service';
import { ActionDialogComponent, actionLabel } from '../shared/action-dialog.component';
import { ApiService, apiError } from '../shared/api.service';
import { ComposerComponent } from '../shared/composer.component';
import { LiveSession } from '../shared/live-stream.service';
import { Action, ConnectorType, Role } from '../shared/models';
import { PlanPanelComponent } from '../shared/plan-panel.component';
import { ProofCountsComponent } from '../shared/proof-counts.component';
import { SecretFieldComponent } from '../shared/secret-field.component';
import { SecretStatusComponent } from '../shared/secret-status.component';
import { StatusChipsComponent } from '../shared/status-chips.component';
import { ThreadComponent } from '../shared/thread.component';
import { minutes, TimeSync } from '../shared/time-sync';

const WIDE = '(min-width: 1101px)';

/**
 * One screen per role over the same live session (FR-003, FR-006–FR-006i), following the design canvas: the header
 * with the status chips (a summary of the plan), the shared plan, the role's side panel (source + SailPoint actions
 * with their details for the IAM engineer; the values the agent fills in for the application owner) and the
 * conversation. The IAM engineer sees both threads side by side, scrolling in step by time; the application owner
 * sees only their own thread, with the IAM engineer's collapsed until they open it. Connector-specific wording comes
 * from the catalog entry, never hard-coded (SC-009).
 */
@Component({
  selector: 'app-session-screen',
  imports: [StatusChipsComponent, ThreadComponent, ComposerComponent, RouterLink, DatePipe, PlanPanelComponent,
    ActionDialogComponent, NgTemplateOutlet, ProofCountsComponent, SecretFieldComponent, SecretStatusComponent],
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
        <app-status-chips [steps]="s.steps" [firstStepLabel]="connector()?.first_step_label ?? defaultFirstStep"
                          [order]="s.milestone_order" />
        <span class="presence">
          <span class="dot" [class.on]="otherOnline()"></span>
          {{ otherRoleLabel() }} {{ otherOnline() ? online : offline }}
          @if (!live.connected()) { · <span class="warn" i18n="@@session.reconnecting">reconnecting…</span> }
        </span>
      </section>

      <div class="layout">
        <aside class="side">
          @if (role() === 'iam_engineer' && hasCapabilities()) {
            <app-proof-counts [proof]="s.proof" [capabilities]="capabilities()" [sourceName]="s.source?.name ?? ''" />
          }
          <app-plan-panel [plan]="s.plan ?? []" [viewerRole]="role()" [nextId]="s.next_step_id ?? null"
                          [done]="s.plan_done ?? 0" [total]="s.plan_total ?? 0" [ownerLabel]="ownerLabel()"
                          [followMinutes]="live.followMinutes()" />
          @if (role() === 'iam_engineer' && connector()?.secret) {
            <app-secret-status [status]="s.application_secret" />
          }
          @if (role() === 'iam_engineer') {
            <section class="card actions" aria-label="SailPoint actions" i18n-aria-label="@@side.actionsAria">
              <h2 i18n="@@side.actions">SailPoint actions</h2>
              <p class="muted small" i18n="@@side.actionsHintDetails">Every change the agent made and who ordered it. Open one for the request and response.</p>
              <ol>
                @for (a of live.actions(); track a.id) {
                  <li [class.failed]="a.result === 'failed'" [class.limited]="a.result === 'tenant_limitation'">
                    <button type="button" class="row" (click)="openAction(a)" [attr.data-action]="a.action">
                      @if (a.result === 'ok') { <svg class="icon ok" viewBox="0 0 24 24" aria-label="passed"><path d="M5 12l5 5L20 7"></path></svg> }
                      @else if (a.result === 'running') { <svg class="icon run" viewBox="0 0 24 24" aria-label="running"><path d="M12 3a9 9 0 1 0 9 9"></path></svg> }
                      @else if (a.result === 'tenant_limitation') { <svg class="icon limit" viewBox="0 0 24 24" aria-label="tenant limitation"><rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="M8 11V8a4 4 0 0 1 8 0v3"></path></svg> }
                      @else { <svg class="icon bad" viewBox="0 0 24 24" aria-label="failed"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
                      <span class="what"><b>{{ actionLabel(a.action) }}</b> {{ a.source?.name }}
                        @if (a.summary) { <small class="summary mono">{{ a.summary }}</small> }
                        <small class="outcome" [class.error]="a.result === 'failed'" [class.limit]="a.result === 'tenant_limitation'">{{ a.outcome ?? a.error ?? a.result }}</small>
                        <small>{{ orderedBy }} {{ a.ordered_by.display_name }}
                          @if (a.trigger === 'application_owner_confirmation') { · <span i18n="@@side.rerun">rerun after the application owner confirmed a fix</span> }
                          @if (a.trigger === 'followup') { · <span i18n="@@side.followup">continued after a long-running step</span> }
                          @if (a.trigger === 'secret_submitted') { · <span i18n="@@side.secretSubmitted">after a new secret</span> }
                        </small>
                      </span>
                      <time>{{ a.at | date: 'HH:mm' }}</time>
                      <svg class="icon chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"></path></svg>
                    </button>
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
            <section class="card" aria-label="Source details" i18n-aria-label="@@side.sourceAria">
              <h2 i18n="@@side.source">Source</h2>
              <dl>
                <dt i18n="@@side.connector">Connector</dt><dd>{{ connector()?.name }} <a routerLink="/catalog" i18n="@@side.catalog">catalog</a></dd>
                @for (f of connector()?.session_fields ?? []; track f.name) {
                  @if (show(s.details[f.name])) { <dt>{{ f.label }}</dt><dd class="mono">{{ fmt(s.details[f.name]) }}</dd> }
                }
                <dt i18n="@@side.tenant">Tenant</dt><dd class="mono">{{ s.tenant?.api_host }}</dd>
                @if (!connector()?.secret) {
                  <dt i18n="@@side.externalId">External ID</dt><dd class="mono">{{ s.tenant?.external_id ?? '—' }}</dd>
                }
                <dt i18n="@@side.sourceId">Source in SailPoint</dt><dd class="mono">{{ s.source?.id ?? notYet }}@if (s.source?.adopted) { <span class="muted"> · <span i18n="@@side.extended">extended</span></span> }</dd>
                <dt i18n="@@side.access">Access</dt><dd>{{ writes() ? readWrite : readOnlyAccess }}</dd>
              </dl>
            </section>
            <app-action-dialog [sessionId]="s.id" [summary]="openedAction()" (closed)="openedAction.set(null)"
                               (showInThread)="showInThread($event)" />
          } @else {
            <section class="card" aria-label="Session values" i18n-aria-label="@@owner.valuesAria">
              <h2 i18n="@@owner.values">Values the agent fills in for you</h2>
              <dl>
                @for (f of connector()?.session_fields ?? []; track f.name) {
                  @if (show(s.details[f.name])) { <dt>{{ f.label }}</dt><dd class="mono">{{ fmt(s.details[f.name]) }}</dd> }
                }
                @if (!connector()?.secret) {
                  <dt i18n="@@side.externalId">External ID</dt><dd class="mono">{{ s.tenant?.external_id ?? '—' }}</dd>
                }
                <dt i18n="@@side.tenant">Tenant</dt><dd class="mono">{{ s.tenant?.name }}</dd>
              </dl>
            </section>
          }
        </aside>

        <div class="main-col">
        @if (role() === 'application_owner' && connector()?.secret) {
          <app-secret-field [sessionId]="s.id" [status]="s.application_secret" [needed]="live.secretNeeded()"
                            [open]="secretOpen()" [label]="connector()!.secret!.label" [help]="connector()!.secret!.help"
                            [expiresHelp]="connector()!.secret!.expires_help" />
        }
        <section class="conversations" aria-label="Conversations" i18n-aria-label="@@conv.aria">
          <header class="conv-head">
            <h2>{{ role() === 'iam_engineer' ? convTitle : yourConvTitle }}</h2>
            <span class="muted small">{{ convHint() }}</span>
            @if (role() === 'iam_engineer') {
              <button type="button" class="sync" [attr.aria-pressed]="syncOn()" [disabled]="!wide()" (click)="toggleSync()"
                      [title]="wide() ? '' : narrowSyncHint">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"></path></svg>
                {{ syncOn() && wide() ? syncOnText : syncOffText }}
              </button>
            }
          </header>
          @if (role() === 'iam_engineer') {
            <div class="threads" [class.single]="ownerCollapsed()">
              <app-thread [thread]="role()" [viewerRole]="role()" [sessionId]="s.id" [ownerLabel]="ownerLabel()"
                          [names]="names()" [viewerId]="meId()" [emptyText]="emptyText()" [dividerTimes]="dividerTimes()">
                <ng-container *ngTemplateOutlet="composer" />
              </app-thread>
              @if (!ownerCollapsed()) {
                <app-thread [thread]="otherRole()" [viewerRole]="role()" [sessionId]="s.id" [ownerLabel]="ownerLabel()"
                            [names]="names()" [viewerId]="meId()" [emptyText]="otherEmptyText()" [dividerTimes]="dividerTimes()" />
              }
              <!-- spec 002 R19: the owner's thread may be collapsed to a bar; side by side stays the default -->
              <button type="button" class="other-bar" [attr.aria-pressed]="ownerCollapsed()" (click)="toggleOwnerThread()">
                <svg viewBox="0 0 24 24" aria-hidden="true" [class.open]="!ownerCollapsed()"><path d="M9 6l6 6-6 6"></path></svg>
                <b>{{ otherPairLabel() }}</b>
                <span class="muted">· {{ names()[otherRole()] }} · {{ viewOnlyText }}@if (ownerCollapsed() && unseenOwner()) { · <span i18n="@@conv.newCount">{{ unseenOwner() }} new</span> }</span>
                <span class="toggle">{{ ownerCollapsed() ? sideBySideText : collapseText }}</span>
              </button>
            </div>
          } @else {
            <div class="threads single">
              <app-thread [thread]="role()" [viewerRole]="role()" [sessionId]="s.id" [ownerLabel]="ownerLabel()"
                          [names]="names()" [viewerId]="meId()" [emptyText]="emptyText()">
                <ng-container *ngTemplateOutlet="composer" />
              </app-thread>
              <!-- FR-006: the IAM engineer's thread is hidden by default; a display choice, not a permission -->
              <button type="button" class="other-bar" [attr.aria-expanded]="showOther()" aria-controls="other-thread"
                      (click)="showOther.set(!showOther())">
                <svg viewBox="0 0 24 24" aria-hidden="true" [class.open]="showOther()"><path d="M9 6l6 6-6 6"></path></svg>
                <b>{{ otherPairLabel() }}</b>
                <span class="muted">{{ showOther() ? '' : hiddenText }} · {{ names()[otherRole()] }} · {{ viewOnlyText }}</span>
                <span class="toggle">{{ showOther() ? hideText : showText }}</span>
              </button>
              @if (showOther()) {
                <app-thread id="other-thread" class="other-open" [thread]="otherRole()" [viewerRole]="role()"
                            [sessionId]="s.id" [ownerLabel]="ownerLabel()" [names]="names()" [viewerId]="meId()" [emptyText]="otherEmptyText()" />
              }
            </div>
          }
          <ng-template #composer>
            @if (s.status === 'open') {
              <app-composer [sessionId]="s.id" [placeholder]="placeholder()" [hint]="composerHint()" />
            } @else {
              <p class="finished" i18n="@@session.finishedAdmin">This session is finished. An admin can reopen it.</p>
            }
          </ng-template>
        </section>
        </div>
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

  protected readonly hasCapabilities = computed(() => !!this.connector()?.capabilities?.length);
  protected readonly capabilities = computed(() => {
    const caps = this.live.session()?.details?.['capabilities'];
    return Array.isArray(caps) ? (caps as string[]) : [];
  });
  protected readonly writes = computed(() =>
    (this.connector()?.capabilities ?? []).some((c) => c.tag === 'writes' && this.capabilities().includes(c.id)),
  );
  protected readonly readOnlyAccess = $localize`:@@side.accessValue:read-only (aggregation)`;
  protected readonly readWrite = $localize`:@@side.accessWrite:read and write (provisioning)`;
  /** The owner's secret field is open while the secret step is current or a new one is needed (research R16). */
  protected readonly secretOpen = computed(() => {
    const s = this.live.session();
    const secret = s?.application_secret;
    const step = s?.plan?.find((p) => p.id === 'provide_secret');
    return (
      this.live.secretNeeded() !== null ||
      !secret ||
      secret.state === 'missing' ||
      !!secret.expires_soon ||
      (!!step && step.state !== 'done' && step.state !== 'skipped' && secret.state !== 'received' &&
        secret.state !== 'in_isc' && secret.state !== 'vault_deleted')
    );
  });
  /** Spec 002 R19: the IAM engineer may collapse the owner's thread; remembered per browser, side by side by default. */
  protected readonly ownerCollapsed = signal(false);
  private readonly collapsedAt = signal(0);
  protected readonly unseenOwner = computed(
    () => this.live.ownerThread().filter((m) => (m.seq ?? 0) > this.collapsedAt() && m.speaker !== 'agent').length,
  );
  protected readonly sideBySideText = $localize`:@@conv.sideBySide:Show side by side`;
  protected readonly collapseText = $localize`:@@conv.collapse:Collapse`;
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
  protected readonly convTitle = $localize`:@@conv.title:Conversations`;
  protected readonly yourConvTitle = $localize`:@@conv.yours:Your conversation`;
  protected readonly syncOnText = $localize`:@@conv.syncOn:Sync by time: on`;
  protected readonly syncOffText = $localize`:@@conv.syncOff:Sync by time: off`;
  protected readonly narrowSyncHint = $localize`:@@conv.syncNarrow:The threads scroll separately when they are stacked.`;
  protected readonly hiddenText = $localize`:@@conv.hidden:(hidden)`;
  protected readonly viewOnlyText = $localize`:@@thread.viewOnlyLower:view only`;
  protected readonly showText = $localize`:@@conv.show:Show`;
  protected readonly hideText = $localize`:@@conv.hide:Hide`;
  protected readonly convHint = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@conv.hintIamSync:Both threads scroll together by time. Only you can order SailPoint changes.`
      : $localize`:@@conv.hintOwnerOnly:The agent tells you what you need from the IAM engineer's side. SailPoint changes are theirs to order.`,
  );
  protected readonly otherPairLabel = computed(() => $localize`:@@thread.pairOther:${this.otherRoleLabel()}:role: ↔ Agent`);
  /** The application owner's view of the IAM engineer's thread: collapsed on every visit (research R25). */
  protected readonly showOther = signal(false);
  /** FR-006i: the IAM engineer's two threads scroll in step by time; on by default, remembered on this browser. */
  protected readonly syncOn = signal(true);
  protected readonly wide = signal(true);
  protected readonly dividerTimes = computed(() =>
    this.role() === 'iam_engineer' && this.syncOn() && this.wide()
      ? minutes(this.live.iamThread(), this.live.ownerThread()) : null,
  );
  protected readonly openedAction = signal<Action | null>(null);
  protected readonly meId = computed(() => this.auth.me()?.id ?? null);
  private readonly threads = viewChildren(ThreadComponent);
  private readonly dialog = viewChild(ActionDialogComponent);
  private readonly injector = inject(Injector);
  private readonly destroyRef = inject(DestroyRef);
  private sync: TimeSync | null = null;
  protected readonly actionLabel = actionLabel;
  protected readonly placeholder = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@composer.iamPlaceholder:Order the agent: create the connector, rerun the connection check, start aggregation…`
      : $localize`:@@composer.ownerPlaceholder:Paste command output, or ask the agent about a step…`,
  );
  protected readonly composerHint = computed(() =>
    this.role() === 'iam_engineer'
      ? $localize`:@@composer.iamHint:Your order is the approval: the agent changes SailPoint right away and records it under SailPoint actions. Keys, tokens and passwords are masked before anyone sees them.`
      : this.connector()?.secret
        ? $localize`:@@composer.ownerHintSecret:Client secrets and tokens are masked here. A screenshot that shows a secret Value is held. The secret goes in the secret field, never the chat.`
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

  constructor() {
    // Re-couple the two logs whenever the threads are (re)created (research R26).
    effect(() => {
      const threads = this.threads();
      afterNextRender({ write: () => this.coupleThreads(threads) }, { injector: this.injector });
    });
    // Handed over by an admin (FR-033): this person no longer has access; back to their sessions.
    effect(() => {
      if (this.live.revoked()) void this.router.navigate(['/sessions'], { queryParams: { left: this.id() } });
    });
    this.destroyRef.onDestroy(() => this.sync?.destroy());
  }

  async ngOnInit(): Promise<void> {
    const media = typeof matchMedia === 'function' ? matchMedia(WIDE) : null;
    if (media) {
      this.wide.set(media.matches);
      const onChange = (e: MediaQueryListEvent) => this.wide.set(e.matches);
      media.addEventListener('change', onChange);
      this.destroyRef.onDestroy(() => media.removeEventListener('change', onChange));
    }
    try {
      await this.live.open(this.id(), this.role());
      const s = this.live.session();
      const catalog = await this.api.catalog();
      this.connector.set(catalog.find((c) => c.id === s?.connector_type) ?? null);
      const me = this.auth.me();
      if (me) this.syncOn.set(readSync(me.id));
      if (me && this.role() === 'iam_engineer' && readFlag(`onboarding.ownerCollapsed.${me.id}`)) this.collapseOwner(true);
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

  protected toggleSync(): void {
    const on = !this.syncOn();
    this.syncOn.set(on);
    const me = this.auth.me();
    if (me) writeSync(me.id, on);
  }

  protected toggleOwnerThread(): void {
    this.collapseOwner(!this.ownerCollapsed());
    const me = this.auth.me();
    if (me) writeFlag(`onboarding.ownerCollapsed.${me.id}`, this.ownerCollapsed());
  }

  private collapseOwner(on: boolean): void {
    this.ownerCollapsed.set(on);
    if (on) this.collapsedAt.set(Math.max(0, ...this.live.ownerThread().map((m) => m.seq ?? 0)));
  }

  protected openAction(a: Action): void {
    this.openedAction.set(a);
    afterNextRender({ write: () => void this.dialog()?.open() }, { injector: this.injector });
  }

  protected showInThread(messageId: string): void {
    for (const t of this.threads()) t.scrollToMessage(messageId);
  }

  private coupleThreads(threads: readonly ThreadComponent[]): void {
    this.sync?.destroy();
    this.sync = null;
    if (this.role() !== 'iam_engineer' || threads.length < 2) return;
    const [a, b] = threads.map((t) => t.logElement());
    if (a && b) this.sync = new TimeSync(a, b, () => this.syncOn() && this.wide());
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

function syncKey(userId: string): string {
  return `onboarding.sync.${userId}`;
}

function readSync(userId: string): boolean {
  try {
    return localStorage.getItem(syncKey(userId)) !== 'off';
  } catch {
    return true;
  }
}

function writeSync(userId: string, on: boolean): void {
  try {
    localStorage.setItem(syncKey(userId), on ? 'on' : 'off');
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === 'on';
  } catch {
    return false;
  }
}

function writeFlag(key: string, on: boolean): void {
  try {
    localStorage.setItem(key, on ? 'on' : 'off');
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}
