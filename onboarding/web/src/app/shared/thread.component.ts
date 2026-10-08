import { DatePipe } from '@angular/common';
import { afterNextRender, Component, computed, effect, ElementRef, inject, Injector, input, signal, untracked, viewChild } from '@angular/core';
import { LiveSession } from './live-stream.service';
import { Message, Role } from './models';
import { RichTextComponent } from './rich-text.component';
import { ThreadItem, withDividers } from './time-sync';
import { WaitingBannerComponent } from './waiting-banner.component';

/**
 * One participant's thread with the agent (FR-006), following the design canvas "Conversations" threads: the viewer
 * writes in their own thread (the composer is projected in below); the other participant's thread is shown live but
 * **view only**. Relay notes (FR-006c) appear as one-line note rows. The messages scroll in their own log (FR-006f,
 * research R19) above the waiting banner (FR-006g), so the header, banner and composer always stay in view.
 */
@Component({
  selector: 'app-thread',
  imports: [RichTextComponent, DatePipe, WaitingBannerComponent],
  template: `
    <header class="top">
      <span class="avatar" aria-hidden="true">{{ initials() }}</span>
      <div class="who-block">
        <span class="pair">{{ pairLabel() }}</span>
        <span class="muted small">{{ participantName() }} · {{ roleLabel() }}
          @if (viewOnly()) { · <span [class.on]="online()">{{ online() ? onlineText : offlineText }}</span> }
        </span>
      </div>
      @if (viewOnly()) { <span class="badge" i18n="@@thread.viewOnly">View only</span> }
    </header>

    <div class="log-wrap">
    <div class="log" #log role="log" aria-live="polite" tabindex="0" [attr.aria-label]="ariaLabel()" (scroll)="onScroll()">
      @if (!messages().length) {
        <p class="empty">{{ emptyText() }}</p>
      }
      @for (item of items(); track trackItem(item)) {
        @if (item.kind === 'divider') {
          <p class="divider" [attr.data-ts]="item.ts"><span>{{ item.label }}</span></p>
        } @else if (item.kind === 'gap') {
          <p class="divider gap" [attr.data-ts]="item.ts">
            <span>{{ item.from === item.to ? noMessagesAt(item.from) : noMessagesFromTo(item.from, item.to) }}</span>
          </p>
        } @else {
        @let m = item.m;
        @if (m.kind === 'relay_note') {
          <p class="note" [attr.data-kind]="m.kind" [attr.data-ts]="item.ts">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h16M14 6l6 6-6 6"></path></svg>
            <span>{{ m.created_at | date: 'HH:mm' }} · {{ m.text }}</span>
          </p>
        } @else if (m.kind === 'system_note') {
          <p class="note system" [attr.data-kind]="m.kind" [attr.data-ts]="item.ts">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16h.01"></path></svg>
            <span>{{ m.created_at | date: 'HH:mm' }} · {{ m.text }}</span>
          </p>
        } @else {
          <article class="msg" [class.mine]="isMine(m)" [class.agent]="m.speaker === 'agent'" [attr.data-kind]="m.kind"
                   [class.pending]="pending(m)" [attr.data-reply-state]="m.reply_state || null" [attr.data-ts]="item.ts"
                   [attr.data-message-id]="m.id">
            <header>
              <span class="who">{{ who(m) }}</span>
              <time>{{ m.created_at | date: 'HH:mm' }}</time>
              @if (m.masked) {
                <span class="state" title="Secrets in this message were masked before anyone saw them" i18n-title="@@chat.maskedTitle"
                      i18n="@@chat.masked">secrets masked</span>
              }
              @if (m.relayed_from) { <span class="state" i18n="@@chat.relayed">passed on</span> }
            </header>
            @if (pending(m)) {
              <!-- FR-006h: the reply's status until its answer streams in, in this same place -->
              <div class="bubble status" role="status">
                @if (m.reply_state === 'working') {
                  <svg class="icon spin" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"></path></svg>
                } @else {
                  <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12l5 5L20 6"></path></svg>
                }
                <span><b>{{ statusLead(m) }}</b> {{ statusRest(m) }}</span>
              </div>
            } @else {
            <div class="bubble" [class.failed]="m.reply_state === 'failed'">
              <app-rich-text [text]="m.text" />
              @for (a of m.attachments; track a.id) {
                @if (a.secret_check === 'passed') {
                  <a [href]="'api/sessions/' + sessionId() + '/attachments/' + a.id" target="_blank" rel="noopener">
                    <img class="shot" [src]="'api/sessions/' + sessionId() + '/attachments/' + a.id" alt="Screenshot" i18n-alt="@@chat.screenshot" />
                  </a>
                }
              }
              @if (m.application_steps?.length) {
                <ul class="steps">
                  @for (s of m.application_steps; track s.index) {
                    <li><span class="tag" [class.ro]="s.read_only" [class.ch]="!s.read_only">{{ s.read_only ? readOnly : change }}</span>
                      {{ s.index }} · {{ s.text }}</li>
                  }
                </ul>
              }
            </div>
            }
          </article>
        }
        }
      }
      @if (progressText(); as p) {
        <p class="progress" role="status"><span class="dot"></span>{{ p }}</p>
      }
    </div>
    @if (unseen()) {
      <button type="button" class="jump" (click)="toEnd()">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M6 13l6 6 6-6"></path></svg>
        {{ newMessagesText() }}
      </button>
    }
    </div>

    <app-waiting-banner [viewerRole]="viewerRole()" [names]="names()" [ownerLabel]="ownerLabel()" />
    @if (viewOnly()) {
      <p class="foot muted small" i18n="@@thread.viewOnlyHint">You can read this thread but not post in it. Ask the agent in your thread to relay a message.</p>
    }
    <ng-content />
  `,
  styles: `
    :host { display: flex; flex-direction: column; min-height: 0; min-width: 0; background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
    :host(.view-only) { background: var(--surface); }
    .top { display: flex; align-items: center; gap: 0.6rem; padding: 0.65rem 0.9rem; border-bottom: 1px solid var(--border); }
    .avatar { width: 2rem; height: 2rem; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
      font-size: 0.72rem; font-weight: 700; background: var(--accent); color: var(--on-accent); flex: none; }
    .who-block { display: flex; flex-direction: column; min-width: 0; }
    .pair { font-weight: 600; font-size: 0.9rem; }
    .small { font-size: 0.74rem; }
    .on { color: var(--ok); }
    .badge { margin-left: auto; padding: 0.1rem 0.5rem; border: 1px solid var(--border); border-radius: 999px; font-size: 0.72rem; color: var(--muted); }
    .log-wrap { position: relative; flex: 1 1 0; min-height: 16rem; display: flex; flex-direction: column; }
    .log { position: relative; flex: 1 1 0; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable;
      scrollbar-width: thin; scrollbar-color: #a0a0a0 transparent; padding: 0.9rem; display: flex; flex-direction: column; gap: 0.8rem; }
    .log:focus-visible { outline: 2px solid var(--highlight); outline-offset: -2px; }
    .jump { position: absolute; left: 50%; bottom: 0.6rem; transform: translateX(-50%); display: inline-flex; align-items: center;
      gap: 0.35rem; min-height: 32px; padding: 0.2rem 0.8rem; border-radius: 999px; font-size: 0.78rem;
      background: var(--accent); color: var(--on-accent); border-color: var(--accent); box-shadow: 0 2px 8px rgb(0 0 0 / 0.18); }
    @media (max-width: 1100px) { .log-wrap { flex: none; height: clamp(280px, 50vh, 480px); } }
    .empty { color: var(--muted); margin: auto; text-align: center; max-width: 40ch; }
    .msg { max-width: 88%; min-width: 0; align-self: flex-start; display: flex; flex-direction: column; gap: 0.25rem; }
    .msg.mine { align-self: flex-end; align-items: flex-end; }
    .msg header { display: flex; gap: 0.5rem; align-items: baseline; font-size: 0.72rem; color: var(--muted); }
    .who { font-weight: 600; }
    .state { padding: 0 0.4rem; border: 1px solid var(--border); border-radius: 999px; }
    .state.live { border-color: var(--info); color: var(--info); }
    .bubble { padding: 0.65rem 0.85rem; border-radius: 10px; background: var(--surface); border: 1px solid var(--border); font-size: 0.9rem;
      min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
    .bubble ::ng-deep pre { max-height: 24rem; overflow: auto; }  /* long command output scrolls inside its message */
    .mine .bubble { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
    .mine .bubble ::ng-deep pre, .mine .bubble ::ng-deep code { color: var(--text); }
    .agent .bubble { background: var(--aws-fill); border-color: var(--info); }
    .note { display: flex; align-items: center; gap: 0.45rem; margin: 0; padding: 0.3rem 0.5rem; font-size: 0.76rem; color: var(--info-strong);
      border-left: 2px solid var(--info); background: color-mix(in srgb, var(--info) 8%, transparent); border-radius: 0 6px 6px 0; }
    .note .icon { width: 0.9rem; height: 0.9rem; flex: none; }
    .divider { display: flex; align-items: center; gap: 0.5rem; margin: 0; font-size: 0.7rem; color: var(--muted);
      font-variant-numeric: tabular-nums; }
    .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: var(--border); }
    .divider.gap span { font-style: italic; }
    .note.system { color: var(--muted); border-left-color: var(--border); background: var(--surface); }
    .bubble.status { display: flex; align-items: flex-start; gap: 0.55rem; background: var(--surface); border: 1px dashed var(--muted);
      color: var(--text); font-size: 0.85rem; }
    .bubble.status .icon { width: 1.05rem; height: 1.05rem; flex: none; margin-top: 0.1rem; fill: none; stroke: currentColor; stroke-width: 2.4;
      stroke-linecap: round; stroke-linejoin: round; }
    .bubble.failed { border-color: var(--danger, #da291c); }
    .msg.flash .bubble { outline: 2px solid var(--highlight); outline-offset: 2px; }
    .spin { animation: spin 1.2s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .shot { display: block; max-width: 16rem; max-height: 10rem; margin-top: 0.5rem; border-radius: 6px; border: 1px solid var(--border); }
    .steps { list-style: none; margin: 0.5rem 0 0; padding: 0; font-size: 0.8rem; display: grid; gap: 0.25rem; }
    .progress { display: flex; align-items: center; gap: 0.5rem; color: var(--info-strong); font-size: 0.85rem; margin: 0; }
    .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--info); animation: pulse 1s infinite; }
    .foot { margin: 0; padding: 0.45rem 0.9rem; border-top: 1px solid var(--border); }
    @keyframes pulse { 50% { opacity: 0.3; } }
    @media (prefers-reduced-motion: reduce) { .dot, .spin { animation: none; } }
  `,
  host: { '[class.view-only]': 'viewOnly()' },
})
export class ThreadComponent {
  /** Whose thread this is. */
  readonly thread = input.required<Role>();
  /** The signed-in viewer's role: their own thread is writable, the other one view only. */
  readonly viewerRole = input.required<Role>();
  readonly sessionId = input.required<string>();
  readonly ownerLabel = input('Application owner');
  readonly names = input<Partial<Record<Role, string>>>({});
  /** The signed-in user's id: a message is "mine" only if this person wrote it, not just someone in this place. */
  readonly viewerId = input<string | null>(null);
  readonly emptyText = input('');
  protected readonly live = inject(LiveSession);
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private readonly injector = inject(Injector);
  /** Follow mode (research R19): the log keeps up with new messages only while the reader is at the bottom. */
  private atBottom = true;
  private seen = 0;
  private seenCount = 0;
  private started = false;
  readonly unseen = signal(0);
  readonly newMessagesText = computed(() => $localize`:@@thread.newMessages:New messages (${this.unseen()}:count:)`);
  protected readonly readOnly = $localize`:@@tag.readOnly:read-only`;
  protected readonly change = $localize`:@@tag.change:change`;
  protected readonly onlineText = $localize`:@@presence.online:online`;
  protected readonly offlineText = $localize`:@@presence.offline:offline`;

  readonly viewOnly = computed(() => this.thread() !== this.viewerRole());
  readonly messages = computed(() => this.live.threadMessages(this.thread()));
  /** Shared minute dividers when this thread scrolls in step with the other one (FR-006i); null: no dividers. */
  readonly dividerTimes = input<number[] | null>(null);
  readonly items = computed(() => withDividers(this.messages(), this.dividerTimes()));
  readonly online = computed(() => this.live.online()[this.thread()]);
  readonly participantName = computed(() => this.names()[this.thread()] ?? '');
  readonly roleLabel = computed(() => this.roleName(this.thread()));
  readonly pairLabel = computed(() =>
    this.viewOnly()
      ? $localize`:@@thread.pairOther:${this.roleLabel()}:role: ↔ Agent`
      : $localize`:@@thread.pairYou:You ↔ Agent`,
  );
  readonly ariaLabel = computed(() =>
    this.viewOnly()
      ? $localize`:@@thread.ariaOther:${this.roleLabel()}:role:'s thread with the agent`
      : $localize`:@@thread.ariaYou:Your thread with the agent`,
  );
  readonly initials = computed(() => {
    const parts = this.participantName().trim().split(/[\s.]+/).filter(Boolean);
    return (parts.length ? parts.map((p) => p[0]).join('').slice(0, 2) : this.roleLabel().slice(0, 2)).toUpperCase();
  });
  /** The separate progress line, only when no reply bubble in this thread already shows it (FR-006h). */
  readonly progressText = computed(() => {
    const p = this.live.progress();
    if (!p || p.thread !== this.thread()) return null;
    return this.messages().some((m) => m.reply_state === 'working') ? null : p.text;
  });
  constructor() {
    // New messages, streamed text or a progress line: follow them only while the reader is at the bottom, or when
    // the newest message is the viewer's own; otherwise keep the reading position and count what is below.
    effect(() => {
      const msgs = this.messages();
      this.progressText();
      untracked(() => {
        // A reply bubble exists from the moment a message is sent (FR-006h): its answer arriving counts as new too.
        const weight = msgs.length + msgs.filter((m) => m.speaker === 'agent' && !!m.text).length;
        const added = Math.max(0, weight - this.seen);
        this.seen = weight;
        // The viewer's own new message brings the log to the end, even with its reply bubble created after it.
        const newMsgs = msgs.slice(this.seenCount);
        this.seenCount = msgs.length;
        const follow = !this.started || this.atBottom || newMsgs.some((m) => this.isMine(m));
        if (msgs.length) this.started = true;
        if (follow) {
          afterNextRender({ write: () => this.toEnd() }, { injector: this.injector });
        } else if (added) {
          this.unseen.update((n) => n + added);
        }
      });
    });
  }

  /** The scrolling log, for the time sync between the two threads (research R26). */
  logElement(): HTMLElement | null {
    return this.log()?.nativeElement ?? null;
  }

  /** Scroll so the given message is at the top of the log ("Show in thread"). */
  scrollToMessage(id: string): void {
    const el = this.logElement()?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(id)}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'start' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1600);
  }

  protected trackItem(item: ThreadItem): string {
    return item.kind === 'msg' ? item.m.id : `${item.kind}-${item.ts}`;
  }

  protected noMessagesAt(t: string): string {
    return $localize`:@@thread.noMessagesAt:No messages at ${t}:time:`;
  }

  protected noMessagesFromTo(from: string, to: string): string {
    return $localize`:@@thread.noMessagesFromTo:No messages from ${from}:from: to ${to}:to:`;
  }

  protected onScroll(): void {
    const el = this.log()?.nativeElement;
    if (!el) return;
    this.atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    if (this.atBottom && this.unseen()) this.unseen.set(0);
  }

  protected toEnd(): void {
    const el = this.log()?.nativeElement;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    this.atBottom = true;
    this.unseen.set(0);
  }

  private roleName(r: Role): string {
    return r === 'iam_engineer' ? $localize`:@@role.iam:IAM engineer` : this.ownerLabel();
  }

  /** A reply still waiting or working, before any of its answer has streamed in. */
  protected pending(m: Message): boolean {
    return m.speaker === 'agent' && (m.reply_state === 'received' || m.reply_state === 'working') && !m.text;
  }

  /** "Received." / "Working on it:" in bold, the rest of the status after it. */
  protected statusLead(m: Message): string {
    return this.splitStatus(m)[0];
  }

  protected statusRest(m: Message): string {
    return this.splitStatus(m)[1];
  }

  private splitStatus(m: Message): [string, string] {
    const text = m.status_text || (m.reply_state === 'working'
      ? $localize`:@@reply.working:Working on it…` : $localize`:@@reply.received:Received.`);
    const match = /^(.+?[.:…])\s+(.*)$/.exec(text);
    return match ? [match[1], match[2]] : [text, ''];
  }

  protected isMine(m: Message): boolean {
    if (m.speaker !== this.viewerRole()) return false;
    const me = this.viewerId();
    return !me || !m.speaker_user_id || m.speaker_user_id === me;
  }

  protected who(m: Message): string {
    if (m.speaker === 'agent') return $localize`:@@chat.agent:Agent`;
    if (this.isMine(m)) return $localize`:@@chat.you:You`;
    return m.speaker_name || this.roleName(m.speaker);
  }
}
