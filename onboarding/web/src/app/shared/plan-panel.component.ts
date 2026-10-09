import { Component, computed, input } from '@angular/core';
import { PlanStep, Role } from './models';

/**
 * The shared plan (FR-008a-c, US3 #12-13, research R22), the same on both screens: "x of y done", a progress bar, one
 * row per step with its state, who does it ("You" for the viewer's own steps) and the reason for added, blocked, failed
 * or skipped steps; the next step highlighted. On the application owner's screen a "Your next step" card leads when the
 * next step is theirs. Follows the design canvas "ISC Onboarding Agent UI".
 */
@Component({
  selector: 'app-plan-panel',
  template: `
    <section class="card plan" aria-labelledby="plan-h">
      <div class="head">
        <h2 id="plan-h" i18n="@@plan.title">Plan</h2>
        <span class="muted small">{{ countText() }}</span>
      </div>
      <div class="bar" aria-hidden="true"><div class="fill" [style.width.%]="percent()"></div></div>

      @if (nextIsMine() && next(); as n) {
        <div class="next-card">
          <span class="eyebrow" i18n="@@plan.yourNext">Your next step</span>
          <b>{{ numberOf(n) }} · {{ n.title }}</b>
          @if (n.reason) { <span class="why">{{ n.reason }}</span> }
          <span class="tag" [class.ch]="n.kind === 'change'" [class.ro]="n.kind === 'read_only'">{{ n.kind === 'change' ? change : readOnly }}</span>
        </div>
      }

      <ol>
        @for (st of plan(); track st.id; let i = $index) {
          <li [attr.data-state]="st.state" [attr.data-step]="st.id" [class.next]="st.id === nextId()"
              [attr.aria-current]="st.id === nextId() ? 'step' : null">
            <span class="ico" [attr.aria-label]="stateLabel(st.state)" role="img">
              @switch (st.state) {
                @case ('done') { <svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"></path></svg> }
                @case ('failed') { <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
                @case ('blocked') { <svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="M8 11V8a4 4 0 0 1 8 0v3"></path></svg> }
                @case ('skipped') { <svg viewBox="0 0 24 24"><path d="M5 12h14"></path></svg> }
                @case ('in_progress') { <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"></circle><circle cx="12" cy="12" r="9"></circle></svg> }
                @default { <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"></circle></svg> }
              }
            </span>
            <span class="t">
              <span class="title">{{ i + 1 }} · {{ st.title }}</span>
              @if (st.reason && st.state !== 'done') {
                <span class="why">{{ st.added_by === 'agent' && st.state !== 'blocked' && st.state !== 'skipped' ? addedBy : '' }}{{ st.reason }}</span>
              }
              @if (pendingMinutes(st); as m) {
                <span class="pending" i18n="@@plan.pending">pending · {{ m }} min</span>
              }
            </span>
            <span class="who" [class.me]="st.actor === viewerRole()">{{ actorLabel(st.actor) }}</span>
          </li>
        } @empty {
          <li class="muted small empty" i18n="@@plan.empty">The plan appears when the session starts.</li>
        }
      </ol>
    </section>
  `,
  styles: `
    .pending { display: block; font-size: 0.75rem; color: var(--info-strong); font-weight: 600; }
    .plan { display: flex; flex-direction: column; gap: 0.55rem; }
    .head { display: flex; align-items: baseline; gap: 0.5rem; }
    h2 { margin: 0; font-size: 0.95rem; }
    .small { font-size: 0.76rem; }
    .bar { height: 6px; border-radius: 999px; background: var(--border); overflow: hidden; }
    .fill { height: 100%; background: var(--ok); transition: width 0.3s; }
    .next-card { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px;
      background: var(--aws-fill); border: 1px solid var(--info); font-size: 0.82rem; }
    .eyebrow { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--info-strong); }
    .next-card .why { color: var(--info-strong); }
    .tag { align-self: flex-start; font-size: 0.68rem; padding: 0 0.5rem; border-radius: 999px; border: 1px solid var(--border); }
    .tag.ch { background: var(--text); color: var(--surface); border-color: var(--text); }
    ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; font-size: 0.8rem; }
    li { display: grid; grid-template-columns: 1.1rem minmax(0, 1fr) auto; gap: 0.5rem; align-items: start; padding: 0.4rem 0;
      border-top: 1px solid var(--border); }
    li.empty { display: block; }
    li.next { background: var(--aws-fill); margin: 0 -0.5rem; padding: 0.4rem 0.5rem; border-radius: 6px; }
    li[data-state='done'] .title, li[data-state='skipped'] .title { color: var(--muted); }
    li[data-state='skipped'] .title { text-decoration: line-through; }
    li[data-state='failed'] .why { color: var(--bad); }
    li[data-state='blocked'] .why { color: var(--warn); }
    li.next .title { font-weight: 700; }
    .t { display: flex; flex-direction: column; min-width: 0; }
    .why { font-size: 0.74rem; color: var(--info-strong); }
    .ico svg { width: 1rem; height: 1rem; fill: none; stroke: var(--muted); stroke-width: 2.4; stroke-linecap: round; stroke-linejoin: round; }
    li[data-state='done'] .ico svg { stroke: var(--ok); }
    li[data-state='failed'] .ico svg { stroke: var(--bad); }
    li[data-state='in_progress'] .ico svg { stroke: var(--info); }
    li[data-state='blocked'] .ico svg { stroke: var(--warn); }
    .who { font-size: 0.68rem; padding: 0 0.5rem; border-radius: 999px; background: var(--surface-2); color: var(--muted); white-space: nowrap; }
    .who.me { background: var(--info); color: var(--on-accent); font-weight: 600; }
  `,
})
export class PlanPanelComponent {
  readonly plan = input<PlanStep[]>([]);
  readonly viewerRole = input.required<Role>();
  readonly nextId = input<string | null>(null);
  readonly done = input(0);
  readonly total = input(0);
  readonly ownerLabel = input('Owner');
  /** Spec 002 FR-139: minutes a followed step has run (live), by plan step id. */
  readonly followMinutes = input<Record<string, number>>({});

  /** "pending · N min" once a still-running step has passed 30 minutes (from the live count or its pending_since). */
  protected pendingMinutes(st: PlanStep): number | null {
    if (st.state === 'done' || st.state === 'failed' || st.state === 'skipped') return null;
    const live = this.followMinutes()[st.id];
    const since = st.pending_since ? Math.floor((Date.now() - Date.parse(st.pending_since)) / 60000) : null;
    const m = Math.max(live ?? 0, since ?? 0);
    return m >= 30 ? m : null;
  }
  protected readonly readOnly = $localize`:@@tag.readOnly:read-only`;
  protected readonly change = $localize`:@@tag.change:change`;
  protected readonly addedBy = $localize`:@@plan.addedBy:Added by the agent: `;

  readonly percent = computed(() => (this.total() ? Math.round((100 * this.done()) / this.total()) : 0));
  readonly countText = computed(() => $localize`:@@plan.count:${this.done()}:done: of ${this.total()}:total: done`);
  readonly next = computed(() => this.plan().find((s) => s.id === this.nextId()) ?? null);
  readonly nextIsMine = computed(() => this.viewerRole() === 'application_owner' && this.next()?.actor === 'application_owner');

  protected numberOf(step: PlanStep): number {
    return this.plan().indexOf(step) + 1;
  }

  protected actorLabel(actor: PlanStep['actor']): string {
    if (actor === this.viewerRole()) return $localize`:@@plan.you:You`;
    if (actor === 'iam_engineer') return $localize`:@@role.iam:IAM engineer`;
    if (actor === 'application_owner') return this.ownerLabel();
    return $localize`:@@chat.agent:Agent`;
  }

  protected stateLabel(state: PlanStep['state']): string {
    return {
      todo: $localize`:@@plan.todo:to do`,
      in_progress: $localize`:@@plan.inProgress:in progress`,
      done: $localize`:@@plan.done:done`,
      failed: $localize`:@@plan.failed:failed`,
      skipped: $localize`:@@plan.skipped:skipped`,
      blocked: $localize`:@@plan.blocked:blocked`,
    }[state];
  }
}
