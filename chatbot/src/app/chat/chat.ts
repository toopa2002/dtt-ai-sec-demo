import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { AgentService } from '../agent.service';

interface Message {
  role: 'user' | 'agent' | 'error';
  text: string;
  tools?: string[];
}

const SERVICES = ['weather', 'hr-directory'];
type Access = 'allowed' | 'denied' | 'unavailable' | 'unknown';

@Component({
  selector: 'app-chat',
  imports: [FormsModule],
  templateUrl: './chat.html',
  styleUrl: './chat.css',
})
export class Chat {
  private readonly agent = inject(AgentService);
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');

  protected readonly messages = signal<Message[]>([]);
  protected readonly busy = signal(false);
  protected readonly access = signal<Record<string, Access>>(Object.fromEntries(SERVICES.map((s) => [s, 'unknown'])));
  protected readonly services = SERVICES;
  protected readonly examples = [
    "What's the weather in Bangkok today?",
    "What's the weather in Bangkok, and who is Somchai's manager?",
    "What's the weather where Somchai lives?",
  ];
  protected draft = '';

  protected send(text = this.draft): void {
    const prompt = text.trim();
    if (!prompt || this.busy()) return;
    this.draft = '';
    this.push({ role: 'user', text: prompt });
    this.busy.set(true);
    this.agent.ask(prompt).subscribe({
      next: (reply) => {
        if (reply.error) {
          this.push({ role: 'error', text: reply.error });
        } else {
          this.push({ role: 'agent', text: reply.answer ?? '', tools: reply.tools_used });
          const next: Record<string, Access> = {};
          for (const s of SERVICES) {
            next[s] = reply.services_allowed?.includes(s)
              ? 'allowed'
              : reply.services_denied?.includes(s)
                ? 'denied'
                : reply.services_unavailable?.includes(s)
                  ? 'unavailable'
                  : 'unknown';
          }
          this.access.set(next);
        }
        this.busy.set(false);
      },
      error: (err: HttpErrorResponse) => {
        this.agent.resetSession();
        const detail = err.status === 401 || err.status === 403 ? 'the agent rejected your token' : err.message;
        this.push({ role: 'error', text: `Request failed (${err.status}): ${detail}` });
        this.busy.set(false);
      },
    });
  }

  private push(message: Message): void {
    this.messages.update((m) => [...m, message]);
    queueMicrotask(() => {
      const el = this.log()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}
