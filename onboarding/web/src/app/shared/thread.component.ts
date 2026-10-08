import { DatePipe } from '@angular/common';
import { afterNextRender, Component, computed, effect, ElementRef, inject, Injector, input, signal, untracked, viewChild } from '@angular/core';
import { LiveSession } from './live-stream.service';
import { Message, Role } from './models';
import { RichTextComponent } from './rich-text.component';
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
      @for (m of messages(); track m.id) {
        @if (m.kind === 'relay_note') {
          <p class="note" [attr.data-kind]="m.kind">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h16M14 6l6 6-6 6"></path></svg>
            <span>{{ m.created_at | date: 'HH:mm' }} · {{ m.text }}</span>
          </p>
        } @else {
          <article class="msg" [class.mine]="isMine(m)" [class.agent]="m.speaker === 'agent'" [attr.data-kind]="m.kind">
            <header>
              <span class="who">{{ who(m) }}</span>
              <time>{{ m.created_at | date: 'HH:mm' }}</time>
              @if (m.queue_state === 'queued') { <span class="state" i18n="@@chat.queued">queued</span> }
              @if (m.queue_state === 'processing') { <span class="state live" i18n="@@chat.processing">agent is answering</span> }
              @if (m.masked) {
                <span class="state" title="Secrets in this message were masked before anyone saw them" i18n-title="@@chat.maskedTitle"
                      i18n="@@chat.masked">secrets masked</span>
              }
              @if (m.relayed_from) { <span class="state" i18n="@@chat.relayed">passed on</span> }
            </header>
            <div class="bubble">
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
          </article>
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
    .log { flex: 1 1 0; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable;
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
    .shot { display: block; max-width: 16rem; max-height: 10rem; margin-top: 0.5rem; border-radius: 6px; border: 1px solid var(--border); }
    .steps { list-style: none; margin: 0.5rem 0 0; padding: 0; font-size: 0.8rem; display: grid; gap: 0.25rem; }
    .progress { display: flex; align-items: center; gap: 0.5rem; color: var(--info-strong); font-size: 0.85rem; margin: 0; }
    .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--info); animation: pulse 1s infinite; }
    .foot { margin: 0; padding: 0.45rem 0.9rem; border-top: 1px solid var(--border); }
    @keyframes pulse { 50% { opacity: 0.3; } }
    @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
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
  readonly emptyText = input('');
  protected readonly live = inject(LiveSession);
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private readonly injector = inject(Injector);
  /** Follow mode (research R19): the log keeps up with new messages only while the reader is at the bottom. */
  private atBottom = true;
  private seen = 0;
  private started = false;
  readonly unseen = signal(0);
  readonly newMessagesText = computed(() => $localize`:@@thread.newMessages:New messages (${this.unseen()}:count:)`);
  protected readonly readOnly = $localize`:@@tag.readOnly:read-only`;
  protected readonly change = $localize`:@@tag.change:change`;
  protected readonly onlineText = $localize`:@@presence.online:online`;
  protected readonly offlineText = $localize`:@@presence.offline:offline`;

  readonly viewOnly = computed(() => this.thread() !== this.viewerRole());
  readonly messages = computed(() => this.live.threadMessages(this.thread()));
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
  readonly progressText = computed(() => {
    const p = this.live.progress();
    return p && p.thread === this.thread() ? p.text : null;
  });
  constructor() {
    // New messages, streamed text or a progress line: follow them only while the reader is at the bottom, or when
    // the newest message is the viewer's own; otherwise keep the reading position and count what is below.
    effect(() => {
      const msgs = this.messages();
      this.progressText();
      untracked(() => {
        const added = Math.max(0, msgs.length - this.seen);
        this.seen = msgs.length;
        const newest = msgs[msgs.length - 1];
        const follow = !this.started || this.atBottom || (added > 0 && !!newest && this.isMine(newest));
        if (msgs.length) this.started = true;
        if (follow) {
          afterNextRender({ write: () => this.toEnd() }, { injector: this.injector });
        } else if (added) {
          this.unseen.update((n) => n + added);
        }
      });
    });
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

  protected isMine(m: Message): boolean {
    return m.speaker === this.viewerRole();
  }

  protected who(m: Message): string {
    if (m.speaker === 'agent') return $localize`:@@chat.agent:Agent`;
    if (this.isMine(m)) return $localize`:@@chat.you:You`;
    return m.speaker_name || this.roleName(m.speaker);
  }
}
