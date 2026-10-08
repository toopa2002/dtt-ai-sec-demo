import { Component, computed, input, signal } from '@angular/core';
import { parse } from './markdown';

/** Renders a chat message's Markdown with Copy buttons on code blocks (the owner runs these commands). */
@Component({
  selector: 'app-rich-text',
  template: `
    @for (b of blocks(); track $index) {
      @switch (b.kind) {
        @case ('code') {
          <div class="code">
            <pre><code>{{ b.text }}</code></pre>
            <button type="button" class="copy" (click)="copy(b.text, $index, pre)" #pre>
              {{ copied() === $index ? copiedLabel : copyLabel }}
            </button>
          </div>
        }
        @case ('list') {
          <ul>
            @for (item of b.items; track $index) {
              <li>
                @for (p of item; track $index) {
                  @switch (p.kind) {
                    @case ('bold') { <strong>{{ p.text }}</strong> }
                    @case ('code') { <code>{{ p.text }}</code> }
                    @default { {{ p.text }} }
                  }
                }
              </li>
            }
          </ul>
        }
        @case ('heading') {
          <p class="heading">
            @for (p of b.parts; track $index) { {{ p.text }} }
          </p>
        }
        @default {
          <p>
            @for (p of b.parts; track $index) {
              @switch (p.kind) {
                @case ('bold') { <strong>{{ p.text }}</strong> }
                @case ('code') { <code>{{ p.text }}</code> }
                @default { {{ p.text }} }
              }
            }
          </p>
        }
      }
    }
  `,
  styles: `
    :host { display: block; }
    p { margin: 0 0 0.5rem; line-height: 1.5; }
    p:last-child { margin-bottom: 0; }
    .heading { font-weight: 700; }
    ul { margin: 0.25rem 0 0.5rem; padding-left: 1.2rem; }
    code { font-family: var(--mono); font-size: 0.85em; background: var(--surface-2); padding: 0 0.25rem; border-radius: 4px; }
    .code { position: relative; margin: 0.4rem 0 0.6rem; }
    pre { margin: 0; padding: 0.6rem 4.5rem 0.6rem 0.7rem; background: var(--surface); border: 1px solid var(--border);
      border-radius: 6px; white-space: pre-wrap; word-break: break-all; font-size: 0.8rem; line-height: 1.45; }
    pre code { background: none; padding: 0; color: var(--text); }
    .copy { position: absolute; top: 0.35rem; right: 0.35rem; min-height: 28px; padding: 0.15rem 0.6rem; font-size: 0.75rem; }
  `,
})
export class RichTextComponent {
  readonly text = input.required<string>();
  protected readonly blocks = computed(() => parse(this.text()));
  protected readonly copied = signal<number | null>(null);
  protected readonly copyLabel = $localize`:@@copy:Copy`;
  protected readonly copiedLabel = $localize`:@@copied:Copied`;

  protected copy(text: string, index: number, button: HTMLElement): void {
    const done = () => {
      this.copied.set(index);
      setTimeout(() => this.copied.set(null), 1500);
    };
    navigator.clipboard.writeText(text).then(done, () => {
      // Fallback: select the code so the person can copy it with the keyboard.
      const pre = button.previousElementSibling;
      if (pre) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
    });
  }
}
