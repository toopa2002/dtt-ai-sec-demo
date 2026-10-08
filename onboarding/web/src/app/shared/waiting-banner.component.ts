import { Component, computed, inject, input } from '@angular/core';
import { LiveSession } from './live-stream.service';
import { Role } from './models';

/**
 * The waiting banner (FR-006g, research R20): while the agent waits on a participant, every thread on both screens
 * shows who is waited on and their next step, worded for the viewer. Information only: it never says messages are
 * held (FR-006a). Not part of the scrolling log, so it is always in view.
 */
@Component({
  selector: 'app-waiting-banner',
  template: `
    <div class="banner" [class.idle]="!text()" role="status" aria-live="polite">
      @if (text(); as t) {
        <svg class="clock" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>
        <span><b>{{ t.lead }}</b> {{ t.reason }}</span>
      }
    </div>
  `,
  styles: `
    .banner { display: flex; align-items: center; gap: 0.6rem; padding: 0.6rem 0.9rem; font-size: 13px; line-height: 18px;
      background: var(--waiting-bg); border-top: 1px solid var(--waiting-border); color: var(--waiting-text); }
    .banner.idle { padding: 0; border: 0; }  /* stays in the page so screen readers announce the next wait */
    .clock { width: 18px; height: 18px; flex: none; fill: none; stroke: var(--waiting-icon); stroke-width: 2.2;
      stroke-linecap: round; stroke-linejoin: round; }
  `,
})
export class WaitingBannerComponent {
  /** The signed-in viewer's role: "Waiting for you" when they are the one awaited. */
  readonly viewerRole = input.required<Role>();
  readonly names = input<Partial<Record<Role, string>>>({});
  readonly ownerLabel = input('Application owner');
  private readonly live = inject(LiveSession);

  readonly text = computed(() => {
    const w = this.live.waiting();
    if (!w) return null;
    const reason = w.reason ?? this.defaultReason(w.on);
    if (w.on === this.viewerRole()) return { lead: $localize`:@@waiting.you:Waiting for you:`, reason };
    const name = this.names()[w.on] || (w.on === 'iam_engineer' ? $localize`:@@role.iam:IAM engineer` : this.ownerLabel());
    return { lead: $localize`:@@waiting.other:Waiting for ${name}:name::`, reason };
  });

  private defaultReason(on: Role): string {
    return on === 'application_owner'
      ? $localize`:@@waiting.defaultOwner:the application steps`
      : $localize`:@@waiting.defaultIam:the next SailPoint order`;
  }
}
