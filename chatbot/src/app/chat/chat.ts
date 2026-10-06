import { Component, computed, ElementRef, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentService } from '../agent.service';
import { applyHop, Engine, Step } from '../flow.model';
import { IDENTITIES } from '../app.config';
import { FlowDiagram } from '../flow/flow-diagram';

interface Message {
  id: number;
  role: 'user' | 'agent' | 'error';
  text: string;
  tools?: string[];
  /** Traffic behind this answer (agent/error messages only). */
  steps?: Step[];
  pending?: boolean;
  /** Agent platform this answer ran on. */
  engine?: Engine;
}

const ENGINE_KEY = 'mcpdemo.engine';

const SERVICES = ['weather', 'hr-directory'];
type Access = 'allowed' | 'denied' | 'unavailable' | 'unknown';

@Component({
  selector: 'app-chat',
  imports: [FormsModule, FlowDiagram],
  templateUrl: './chat.html',
  styleUrl: './chat.css',
})
export class Chat {
  private readonly agent = inject(AgentService);
  private readonly ids = inject(IDENTITIES);
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private nextId = 1;

  protected readonly messages = signal<Message[]>([]);
  protected readonly busy = signal(false);
  protected readonly selectedId = signal<number | null>(null);
  /** Hide the chat to give the traffic panel the full width (the design's "Hide chat"). */
  protected readonly chatOpen = signal(true);
  /** Phone width (the design's Phone screen): the traffic panel is hidden behind a "Show traffic" button. */
  private readonly phoneQuery = window.matchMedia('(max-width: 700px)');
  protected readonly phone = signal(this.phoneQuery.matches);
  protected readonly trafficOpen = signal(false);
  protected readonly showTraffic = computed(() => !this.phone() || this.trafficOpen());

  constructor() {
    this.phoneQuery.addEventListener('change', (e) => this.phone.set(e.matches));
  }
  protected readonly access = signal<Record<string, Access>>(Object.fromEntries(SERVICES.map((s) => [s, 'unknown'])));
  protected readonly services = SERVICES;
  protected readonly examples = [
    "What's the weather in Bangkok today?",
    "What's the weather in Bangkok, and who is Somchai's manager?",
    "What's the weather where Somchai lives?",
  ];
  protected draft = '';

  /** Agent platform for the next question: AgentCore (Claude loop in the runtime) or Bedrock Agent. Per browser. */
  protected readonly engines: { id: Engine; label: string; title: string }[] = [
    { id: 'claude', label: 'AgentCore', title: 'AgentCore runtime runs the model loop (Claude Haiku 4.5 on Bedrock)' },
    { id: 'bedrock-agent', label: 'Bedrock Agent', title: 'Bedrock Agent mcpdemo-tools-agent runs the model loop; its action groups are the MCP tools' },
  ];
  protected readonly engine = signal<Engine>(this.initialEngine());

  private initialEngine(): Engine {
    try {
      const saved = localStorage.getItem(ENGINE_KEY);
      if (saved === 'claude' || saved === 'bedrock-agent') return saved;
    } catch {
      /* storage unavailable */
    }
    return this.ids.agentEngine === 'bedrock-agent' ? 'bedrock-agent' : 'claude';
  }

  protected setEngine(e: Engine): void {
    this.engine.set(e);
    try {
      localStorage.setItem(ENGINE_KEY, e);
    } catch {
      /* storage unavailable */
    }
  }

  /** Platform of the answer shown in the traffic panel (the selected one, or the latest). */
  protected readonly panelEngine = computed<Engine>(() => {
    const withSteps = this.messages().filter((m) => m.steps);
    const selected = withSteps.find((m) => m.id === this.selectedId()) ?? withSteps.at(-1);
    return selected?.engine ?? this.engine();
  });

  /** Steps shown in the traffic panel: the selected answer, or the latest one. */
  protected readonly panelSteps = computed<Step[]>(() => {
    const withSteps = this.messages().filter((m) => m.steps);
    const selected = withSteps.find((m) => m.id === this.selectedId()) ?? withSteps.at(-1);
    return selected?.steps ?? [];
  });
  protected readonly panelLive = computed(() => {
    const withSteps = this.messages().filter((m) => m.steps);
    const selected = withSteps.find((m) => m.id === this.selectedId()) ?? withSteps.at(-1);
    return !!selected?.pending;
  });

  protected send(text = this.draft): void {
    const prompt = text.trim();
    if (!prompt || this.busy()) return;
    this.draft = '';
    this.push({ id: this.nextId++, role: 'user', text: prompt });
    const replyId = this.nextId++;
    const engine = this.engine();
    this.push({ id: replyId, role: 'agent', text: '', steps: [], pending: true, engine });
    this.selectedId.set(null); // follow the live request
    this.busy.set(true);

    this.agent.ask(prompt, engine).subscribe({
      next: (ev) => {
        switch (ev.type) {
          case 'hop':
            this.update(replyId, (m) => ({ ...m, steps: applyHop(m.steps ?? [], ev) }));
            break;
          case 'final':
            this.update(replyId, (m) => ({ ...m, text: ev.answer, tools: ev.tools_used, pending: false }));
            this.access.set(
              Object.fromEntries(
                SERVICES.map((s): [string, Access] => [
                  s,
                  ev.services_allowed.includes(s)
                    ? 'allowed'
                    : ev.services_denied.includes(s)
                      ? 'denied'
                      : ev.services_unavailable.includes(s)
                        ? 'unavailable'
                        : 'unknown',
                ]),
              ),
            );
            break;
          case 'error':
            this.update(replyId, (m) => ({ ...m, role: 'error', text: ev.message, pending: false }));
            break;
        }
        this.scrollToEnd();
      },
      complete: () => {
        this.update(replyId, (m) =>
          m.pending ? { ...m, role: 'error', text: m.text || 'The agent ended without an answer.', pending: false } : m,
        );
        this.busy.set(false);
      },
    });
  }

  protected select(m: Message): void {
    if (m.steps) this.selectedId.set(m.id);
  }

  protected isSelected(m: Message): boolean {
    const withSteps = this.messages().filter((x) => x.steps);
    const selected = withSteps.find((x) => x.id === this.selectedId()) ?? withSteps.at(-1);
    return selected?.id === m.id;
  }

  private push(message: Message): void {
    this.messages.update((m) => [...m, message]);
    this.scrollToEnd();
  }

  private update(id: number, fn: (m: Message) => Message): void {
    this.messages.update((list) => list.map((m) => (m.id === id ? fn(m) : m)));
  }

  private scrollToEnd(): void {
    queueMicrotask(() => {
      const el = this.log()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}
