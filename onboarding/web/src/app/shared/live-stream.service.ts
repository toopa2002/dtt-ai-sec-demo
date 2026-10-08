import { computed, inject, Injectable, NgZone, OnDestroy, signal } from '@angular/core';
import { API, ApiService } from './api.service';
import { Action, Message, PlanStep, QueueState, Role, Session, StepKey, StepState, Suggestion } from './models';

/**
 * One onboarding session, live (contracts/live-events.md): history first, then the SSE stream with Last-Event-ID
 * replay on reconnect (research R7). Both threads come over the same stream and are filtered by `thread`; agent
 * replies stream in as `agent.delta` (always in the writer's thread) and are replaced by `agent.message`. Each
 * participant receives suggestions for their own thread only (FR-006e).
 */
@Injectable()
export class LiveSession implements OnDestroy {
  private readonly api = inject(ApiService);
  private readonly zone = inject(NgZone);

  readonly session = signal<Session | null>(null);
  readonly messages = signal<Message[]>([]);
  readonly actions = signal<Action[]>([]);
  /** The progress line of the turn in flight, and the thread it is answering in. */
  readonly progress = signal<{ thread: Role; text: string } | null>(null);
  readonly online = signal<Record<Role, boolean>>({ iam_engineer: false, application_owner: false });
  /** Who the agent waits for and their next step (FR-006g); information only, never holds a message back. */
  readonly waiting = signal<{ on: Role; reason: string | null } | null>(null);
  readonly suggestions = signal<Suggestion[]>([]);
  readonly connected = signal(false);
  readonly heldNotices = signal<{ attachment_id: string; reason: string }[]>([]);
  readonly checkedAttachments = signal<Record<string, 'passed' | 'held'>>({});
  readonly error = signal<string | null>(null);
  /** Set when an admin handed this viewer's place to someone else (FR-033): the screen leaves the session. */
  readonly revoked = signal(false);
  /** The viewer's role; the application owner never loads the SailPoint action record (FR-020, research R24). */
  private role: Role = 'iam_engineer';

  readonly iamThread = computed(() => this.messages().filter((m) => m.thread === 'iam_engineer'));
  readonly ownerThread = computed(() => this.messages().filter((m) => m.thread === 'application_owner'));
  /** The thread whose message the agent is answering right now, if any. */
  readonly busyThread = computed<Role | null>(() => this.messages().find((m) => m.queue_state === 'processing')?.thread ?? null);

  private source: EventSource | null = null;
  private lastEventId = 0;
  private retry = 0;
  private resyncing = false;
  private downTimer: ReturnType<typeof setTimeout> | undefined;
  private watchdog: ReturnType<typeof setInterval> | undefined;
  /** When the stream last delivered an event (the API sends `ping` every 15 s). */
  private streamAt = 0;
  private polling = false;
  /** Event handlers by type, shared by the stream and the long-poll fallback. */
  private readonly handlers: Record<string, (data: any) => void> = {};
  private closed = false;
  private id = '';

  threadMessages(thread: Role): Message[] {
    return thread === 'iam_engineer' ? this.iamThread() : this.ownerThread();
  }

  async open(id: string, role: Role = 'iam_engineer'): Promise<void> {
    this.id = id;
    this.role = role;
    this.closed = false;
    const [session, messages, actions, suggestions] = await Promise.all([
      this.api.getSession(id),
      this.api.messages(id),
      this.loadActions(id),
      this.api.suggestions(id).catch(() => null),
    ]);
    this.session.set(session);
    this.lastEventId = session.event_seq ?? 0;
    this.messages.set(messages);
    this.actions.set(actions);
    this.waiting.set(waitingOf(session.waiting_on, session.waiting_reason));
    if (suggestions) this.suggestions.set(suggestions.items);
    this.online.set({
      iam_engineer: !!session.participants.iam_engineer?.online,
      application_owner: !!session.participants.application_owner?.online,
    });
    this.connect();
    // A proxy can hold the stream back while the request itself succeeds (seen behind a TLS-inspecting corporate
    // proxy, which buffers event streams): when the stream has delivered nothing for STALE_MS, or nothing at all,
    // the screen long-polls the same events as plain JSON responses until the stream delivers again.
    clearInterval(this.watchdog);
    this.watchdog = setInterval(() => {
      if (!this.streamFresh()) void this.longPoll();
    }, CHECK_MS);
  }

  private streamFresh(): boolean {
    return this.streamAt > 0 && Date.now() - this.streamAt < STALE_MS;
  }

  private async longPoll(): Promise<void> {
    if (this.polling || this.closed) return;
    this.polling = true;
    try {
      // The catch-up reload shows the API is reachable: the screen is live again, through polling.
      if (await this.resync()) this.connected.set(true);
      while (!this.closed && !this.streamFresh()) {
        try {
          const { events } = await this.api.pollEvents(this.id, this.lastEventId);
          this.zone.run(() => {
            this.connected.set(true);
            for (const e of events) this.apply(e.id, e.type, e.data);
          });
        } catch (err: any) {
          this.connected.set(false);
          // 429: the tunnel's request limit; back off longer. A 401 is handled by the sign-in redirect.
          await new Promise((r) => setTimeout(r, err?.status === 429 ? 30000 : CHECK_MS));
        }
      }
    } finally {
      this.polling = false;
    }
  }

  /** Apply one event once, in order: events at or below the last applied id are skipped. */
  private apply(id: number, type: string, data: unknown): void {
    if (id && id <= this.lastEventId) return;
    if (id) this.lastEventId = id;
    this.handlers[type]?.(data);
  }

  ngOnDestroy(): void {
    this.closed = true;
    clearTimeout(this.downTimer);
    clearInterval(this.watchdog);
    this.source?.close();
  }

  private connect(): void {
    if (this.closed) return;
    // EventSource sends Last-Event-ID itself on its own reconnects; a fresh connection passes ?after=, so only events
    // newer than the loaded snapshot (or the last one applied) are replayed. Anything older is ignored below.
    const es = new EventSource(`${API}/sessions/${this.id}/events?after=${this.lastEventId}`, { withCredentials: true });
    this.source = es;
    es.onopen = () => this.zone.run(() => {
      const reconnect = this.retry > 0;
      clearTimeout(this.downTimer);
      this.connected.set(true);
      this.retry = 0;
      if (reconnect) void this.resync();
    });
    es.onerror = () => this.zone.run(() => {
      // Show "reconnecting…" only if the stream stays down: the browser's own reconnects take a few seconds.
      clearTimeout(this.downTimer);
      this.downTimer = setTimeout(() => {
        if ((es.readyState !== EventSource.OPEN || this.source !== es) && !this.polling) this.connected.set(false);
      }, 5000);
      if (es.readyState === EventSource.CLOSED && !this.closed) {
        const wait = Math.min(30000, 1000 * 2 ** this.retry++);
        setTimeout(() => this.connect(), wait);
      }
    });
    const on = (type: string, handler: (data: any) => void) => {
      this.handlers[type] = handler;
      es.addEventListener(type, (ev) => this.zone.run(() => {
        if (this.source !== es) return;
        this.streamAt = Date.now();
        this.connected.set(true);
        const id = Number((ev as MessageEvent).lastEventId || 0);
        if (id && id <= this.lastEventId) return; // already applied
        let data: unknown;
        try {
          data = JSON.parse((ev as MessageEvent).data);
        } catch {
          // A proxy mangled the stream (seen with ngrok): don't guess, reload the conversation from the server.
          void this.resync();
          return;
        }
        this.apply(id, type, data);
      }));
    };

    on('ping', () => undefined);
    on('message.created', (m: Message) => this.upsert(m));
    on('reply.status', (d: { message_id: string; reply_state: Message['reply_state']; ahead?: number | null; status_text?: string | null }) =>
      this.messages.update((list) =>
        list.map((m) => (m.id === d.message_id
          ? { ...m, reply_state: d.reply_state, ahead: d.ahead ?? null, status_text: d.status_text ?? null } : m)),
      ),
    );
    on('message.queue', (d: { message_id: string; queue_state: QueueState }) =>
      this.messages.update((list) => list.map((m) => (m.id === d.message_id ? { ...m, queue_state: d.queue_state } : m))),
    );
    on('agent.delta', (d: { message_id: string; thread: Role; text_delta: string }) => {
      this.messages.update((list) => {
        const existing = list.find((m) => m.id === d.message_id);
        if (existing) {
          return list.map((m) => (m.id === d.message_id ? { ...m, text: m.text + d.text_delta } : m));
        }
        const draft: Message = {
          id: d.message_id,
          seq: Number.MAX_SAFE_INTEGER,
          thread: d.thread,
          kind: 'message',
          speaker: 'agent',
          speaker_name: 'Agent',
          relay_ref: null,
          relayed_from: null,
          text: d.text_delta,
          masked: false,
          queue_state: null,
          attachments: [],
          created_at: new Date().toISOString(),
          streaming: true,
        };
        return [...list, draft];
      });
    });
    on('agent.progress', (d: { thread?: Role; text: string | null }) =>
      this.progress.set(d.text ? { thread: d.thread ?? this.busyThread() ?? 'iam_engineer', text: d.text } : null),
    );
    on('agent.message', (m: Message & { replaces?: string }) => {
      this.messages.update((list) => list.filter((x) => x.id !== m.replaces && x.id !== m.id));
      this.upsert(m);
      // End of a turn: line the screen up with the server, so an update lost in transit can't stick.
      void this.resync();
    });
    on('step.changed', (d: { step: StepKey; state: StepState }) =>
      this.session.update((s) => (s ? { ...s, steps: { ...s.steps, [d.step]: d.state } } : s)),
    );
    on('action.recorded', (a: Action) => this.actions.update((list) => [...list.filter((x) => x.id !== a.id), a]));
    on('action.updated', (a: Action) => this.actions.update((list) => list.map((x) => (x.id === a.id ? a : x))));
    on('plan.updated', (d: { plan: PlanStep[]; done: number; total: number; next_step_id: string | null }) =>
      this.session.update((s) => (s ? { ...s, plan: d.plan, plan_done: d.done, plan_total: d.total, next_step_id: d.next_step_id } : s)),
    );
    on('participant.changed', () => void this.resync());
    on('access.revoked', () => this.leave());
    on('session.updated', (d: { source?: Session['source']; status?: Session['status']; reopened_at?: string }) =>
      this.session.update((s) =>
        s ? { ...s, ...('source' in d ? { source: d.source ?? null } : {}), ...(d.status ? { status: d.status } : {}),
              ...(d.reopened_at ? { reopened_at: d.reopened_at } : {}) } : s,
      ),
    );
    on('participant.presence', (d: { role: Role; online: boolean }) =>
      this.online.update((o) => ({ ...o, [d.role]: d.online })),
    );
    on('thread.waiting', (d: { waiting_on: Role | null; reason?: string | null }) => {
      this.waiting.set(waitingOf(d.waiting_on, d.reason));
      this.session.update((s) => (s ? { ...s, waiting_on: d.waiting_on, waiting_reason: d.reason ?? null } : s));
    });
    on('suggestions.updated', (d: { thread: Role; items: Suggestion[] }) => this.suggestions.set(d.items));
    on('attachment.checked', (d: { attachment_id: string }) => {
      this.checkedAttachments.update((c) => ({ ...c, [d.attachment_id]: 'passed' }));
      this.messages.update((list) =>
        list.map((m) => ({
          ...m,
          attachments: m.attachments.map((a) =>
            a.id === d.attachment_id ? { ...a, secret_check: 'passed', url: `attachments/${a.id}` } : a,
          ),
        })),
      );
    });
    on('attachment.held', (d: { attachment_id: string; reason: string }) => {
      this.checkedAttachments.update((c) => ({ ...c, [d.attachment_id]: 'held' }));
      this.heldNotices.update((h) => [...h, d]);
    });
    on('turn.failed', (d: { reason: string }) => this.error.set(d.reason));
  }

  /** Reload messages, actions, steps and suggestions from the API; keeps any agent reply still streaming. */
  private async resync(): Promise<boolean> {
    if (this.resyncing || !this.id) return false;
    this.resyncing = true;
    try {
      const [session, messages, actions, suggestions] = await Promise.all([
        this.api.getSession(this.id),
        this.api.messages(this.id),
        this.loadActions(this.id),
        this.api.suggestions(this.id).catch(() => null),
      ]);
      const streaming = this.messages().filter((m) => m.streaming && !messages.some((x) => x.id === m.id));
      // A snapshot read before events this screen already applied (e.g. the wait cleared right after a reply) must
      // not bring back stale event-driven state: keep the live waiting state then.
      const stale = (session.event_seq ?? 0) < this.lastEventId;
      const live = this.waiting();
      this.session.set(stale ? { ...session, waiting_on: live?.on ?? null, waiting_reason: live?.reason ?? null } : session);
      if (!stale) this.waiting.set(waitingOf(session.waiting_on, session.waiting_reason));
      this.online.set({
        iam_engineer: !!session.participants.iam_engineer?.online,
        application_owner: !!session.participants.application_owner?.online,
      });
      this.messages.set([...messages, ...streaming]);
      this.actions.set(actions);
      if (suggestions) this.suggestions.set(suggestions.items);
      this.lastEventId = Math.max(this.lastEventId, session.event_seq ?? 0);
      return true;
    } catch (err: any) {
      if (err?.status === 404) this.leave();  // no longer a participant (handed over)
      // Otherwise the next turn or reconnect tries again.
      return false;
    } finally {
      this.resyncing = false;
    }
  }

  private loadActions(id: string): Promise<Action[]> {
    return this.role === 'iam_engineer' ? this.api.actions(id) : Promise.resolve([]);
  }

  private leave(): void {
    this.revoked.set(true);
    this.ngOnDestroy();
  }

  private upsert(m: Message): void {
    this.messages.update((list) => {
      const rest = list.filter((x) => x.id !== m.id);
      return [...rest, m].sort((a, b) => a.seq - b.seq);
    });
  }

  dismissHeld(attachmentId: string): void {
    this.heldNotices.update((h) => h.filter((x) => x.attachment_id !== attachmentId));
  }
}

const CHECK_MS = 5000;
const STALE_MS = 35000;

function waitingOf(on: Role | null | undefined, reason: string | null | undefined) {
  return on ? { on, reason: reason || null } : null;
}
