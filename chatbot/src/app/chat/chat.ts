import { Component, computed, ElementRef, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentService } from '../agent.service';
import { applyHop, Step } from '../flow.model';
import { FlowDiagram } from '../flow/flow-diagram';

interface Message {
  id: number;
  role: 'user' | 'agent' | 'error';
  text: string;
  tools?: string[];
  /** Traffic behind this answer (agent/error messages only). */
  steps?: Step[];
  pending?: boolean;
}

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
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private nextId = 1;

  protected readonly messages = signal<Message[]>([]);
  protected readonly busy = signal(false);
  protected readonly selectedId = signal<number | null>(null);
  protected readonly panelOpen = signal(true);
  protected readonly access = signal<Record<string, Access>>(Object.fromEntries(SERVICES.map((s) => [s, 'unknown'])));
  protected readonly services = SERVICES;
  protected readonly examples = [
    "What's the weather in Bangkok today?",
    "What's the weather in Bangkok, and who is Somchai's manager?",
    "What's the weather where Somchai lives?",
  ];
  protected draft = '';

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
    this.push({ id: replyId, role: 'agent', text: '', steps: [], pending: true });
    this.selectedId.set(null); // follow the live request
    this.busy.set(true);

    this.agent.ask(prompt).subscribe({
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
