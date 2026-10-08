import { Component, ElementRef, inject, input, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, apiError } from './api.service';
import { LiveSession } from './live-stream.service';
import { Suggestion } from './models';

interface Pending {
  id: string;
  name: string;
  preview: string;
}

/**
 * Message box with screenshot upload (FR-007) and suggested messages (FR-006e). A screenshot is checked for secrets
 * before anyone else sees it (FR-026a): while pending it shows "checking"; when held, only the uploader sees it with
 * Replace / Dismiss. Picking a suggestion only fills the box: nothing is sent until Send.
 */
@Component({
  selector: 'app-composer',
  imports: [FormsModule],
  template: `
    @for (h of live.heldNotices(); track h.attachment_id) {
      <div class="held" role="alert">
        <img [src]="'api/sessions/' + sessionId() + '/attachments/' + h.attachment_id" alt="" />
        <div>
          <strong i18n="@@held.title">Screenshot held</strong>
          <p>{{ h.reason }} <span i18n="@@held.body">Only you can see it. Upload a cropped or redacted version instead.</span></p>
          <button type="button" (click)="file.click()" i18n="@@held.replace">Replace</button>
          <button type="button" (click)="dismiss(h.attachment_id)" i18n="@@held.dismiss">Dismiss</button>
        </div>
      </div>
    }
    <form (submit)="$event.preventDefault(); send()">
      @if (live.suggestions().length) {
        <div class="chips" role="group" aria-label="Suggested messages" i18n-aria-label="@@suggest.aria">
          @for (s of live.suggestions(); track s.text) {
            <button type="button" class="chip suggestion" [attr.data-kind]="s.kind" (click)="pick(s)">{{ s.text }}</button>
          }
        </div>
      }
      @if (pending().length) {
        <div class="pending">
          @for (p of pending(); track p.id) {
            <span class="chip" [class.passed]="status(p.id) === 'passed'">
              <img [src]="p.preview" alt="" />
              {{ p.name }} ·
              @switch (status(p.id)) {
                @case ('passed') { <span i18n="@@upload.ready">ready</span> }
                @case ('held') { <span i18n="@@upload.held">held</span> }
                @default { <span i18n="@@upload.checking">checking for secrets…</span> }
              }
              <button type="button" class="x" (click)="remove(p.id)" aria-label="Remove screenshot" i18n-aria-label="@@upload.remove">×</button>
            </span>
          }
        </div>
      }
      <div class="row">
        <label class="sr-only" for="composer-text" i18n="@@composer.label">Message the agent</label>
        <textarea #box id="composer-text" name="text" rows="2" [(ngModel)]="text" [placeholder]="placeholder()"
                  (keydown.enter)="onEnter($any($event))"></textarea>
        <input #file type="file" accept="image/png,image/jpeg,image/webp" hidden (change)="upload($any($event.target))" />
        <button type="button" class="attach" (click)="file.click()" aria-label="Attach screenshot" i18n-aria-label="@@composer.attach"
                title="Attach screenshot" i18n-title="@@composer.attach">
          <svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M3 15l5-5 4 4 3-3 6 6"></path></svg>
        </button>
        <button type="submit" class="primary" [disabled]="busy() || (!text.trim() && !readyIds().length)" i18n="@@composer.send">Send</button>
      </div>
      <p class="hint">{{ hint() }}</p>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    </form>
  `,
  styles: `
    :host { display: block; border-top: 1px solid var(--border); padding: 0.75rem 1rem 0.9rem; background: var(--surface); }
    .row { display: flex; gap: 0.5rem; align-items: flex-end; }
    textarea { flex: 1; resize: vertical; min-height: 44px; max-height: 12rem; line-height: 1.4; }
    .attach { min-width: 44px; min-height: 44px; display: inline-flex; align-items: center; justify-content: center; }
    .primary { min-height: 44px; }
    .hint { margin: 0.4rem 0 0; font-size: 0.72rem; color: var(--muted); }
    .chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.55rem; }
    .suggestion { min-height: 32px; padding: 0.25rem 0.7rem; border-radius: 999px; border: 1px solid var(--border);
      background: var(--surface-2); font-size: 0.8rem; cursor: pointer; text-align: left; }
    .suggestion:hover, .suggestion:focus-visible { border-color: var(--accent); }
    .suggestion[data-kind="order"] { border-color: var(--accent); font-weight: 600; }
    .pending { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.5rem; }
    .pending img { width: 1.6rem; height: 1.2rem; object-fit: cover; border-radius: 3px; }
    .x { border: 0; background: none; min-height: 0; padding: 0 0.2rem; }
    .held { display: flex; gap: 0.75rem; padding: 0.6rem; margin-bottom: 0.6rem; border: 1px solid var(--bad); border-radius: 8px; background: var(--surface); }
    .held img { width: 6rem; height: 4rem; object-fit: cover; border-radius: 4px; filter: blur(3px); }
    .held p { margin: 0.2rem 0 0.4rem; font-size: 0.85rem; }
    .held button { margin-right: 0.4rem; }
  `,
})
export class ComposerComponent {
  readonly sessionId = input.required<string>();
  readonly placeholder = input('');
  readonly hint = input('');
  protected readonly live = inject(LiveSession);
  private readonly api = inject(ApiService);

  protected text = '';
  private readonly box = viewChild<ElementRef<HTMLTextAreaElement>>('box');
  protected readonly pending = signal<Pending[]>([]);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  /** A picked suggestion fills the box and focuses it; a starter ending in ":" leaves the caret after it (FR-006e). */
  protected pick(s: Suggestion): void {
    this.text = s.text.endsWith(':') ? `${s.text} ` : s.text;
    queueMicrotask(() => {
      const el = this.box()?.nativeElement;
      if (!el) return;
      el.focus();
      el.setSelectionRange(this.text.length, this.text.length);
    });
  }

  /** Enter sends; Shift+Enter adds a line. */
  protected onEnter(event: KeyboardEvent): void {
    if (event.shiftKey || event.isComposing) return;
    event.preventDefault();
    void this.send();
  }

  protected status(id: string): 'pending' | 'passed' | 'held' {
    return this.live.checkedAttachments()[id] ?? 'pending';
  }

  protected readyIds(): string[] {
    return this.pending().filter((p) => this.status(p.id) === 'passed').map((p) => p.id);
  }

  protected async upload(input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      this.error.set($localize`:@@upload.tooLarge:Screenshots are limited to 10 MB.`);
      return;
    }
    this.error.set(null);
    try {
      const att = await this.api.upload(this.sessionId(), file);
      this.pending.update((p) => [...p, { id: att.id, name: file.name, preview: URL.createObjectURL(file) }]);
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  protected remove(id: string): void {
    this.pending.update((p) => p.filter((x) => x.id !== id));
    if (this.status(id) === 'held') void this.dismiss(id);
  }

  protected async dismiss(id: string): Promise<void> {
    try {
      await this.api.dismissAttachment(this.sessionId(), id);
    } catch {
      /* already discarded */
    }
    this.live.dismissHeld(id);
    this.pending.update((p) => p.filter((x) => x.id !== id));
  }

  protected async send(): Promise<void> {
    const text = this.text.trim();
    const ids = this.readyIds();
    if ((!text && !ids.length) || this.busy()) return;
    if (this.pending().some((p) => this.status(p.id) === 'pending')) {
      this.error.set($localize`:@@upload.wait:Wait until the screenshot check finishes.`);
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.api.sendMessage(this.sessionId(), text, ids);
      this.text = '';
      this.pending.set([]);
    } catch (err) {
      this.error.set(apiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }
}
