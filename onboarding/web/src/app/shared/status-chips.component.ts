import { Component, input } from '@angular/core';
import { STEP_KEYS, StepKey, StepState } from './models';

/** The six onboarding steps (FR-008), each not started / in progress / passed / failed, live on both screens. */
@Component({
  selector: 'app-status-chips',
  template: `
    <ol aria-label="Onboarding status" i18n-aria-label="@@status.aria">
      @for (k of keys; track k) {
        <li class="chip" [class]="steps()[k]" [attr.aria-label]="label(k) + ': ' + stateLabel(steps()[k])">
          @switch (steps()[k]) {
            @case ('passed') { <svg class="icon" viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"></path></svg> }
            @case ('failed') { <svg class="icon" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"></path></svg> }
            @case ('in_progress') { <span class="dot"></span> }
          }
          {{ label(k) }}
        </li>
      }
    </ol>
  `,
  styles: `
    ol { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--info); animation: pulse 1s infinite; }
    @keyframes pulse { 50% { opacity: 0.3; } }
    @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
  `,
})
export class StatusChipsComponent {
  readonly steps = input.required<Record<StepKey, StepState>>();
  /** Connector-specific label for the first step, e.g. "AWS role ready" (FR-008, SC-009). */
  readonly firstStepLabel = input($localize`:@@step.application_ready:Application ready`);
  protected readonly keys = STEP_KEYS;

  protected label(k: StepKey): string {
    switch (k) {
      case 'application_ready':
        return this.firstStepLabel();
      case 'source_created':
        return $localize`:@@step.source_created:Source created`;
      case 'configured':
        return $localize`:@@step.configured:Configured`;
      case 'connection_check':
        return $localize`:@@step.connection_check:Connection check`;
      case 'aggregation':
        return $localize`:@@step.aggregation:Aggregation`;
      case 'test_connection':
        return $localize`:@@step.test_connection:Test Connection`;
    }
  }

  protected stateLabel(s: StepState): string {
    return {
      not_started: $localize`:@@state.not_started:not started`,
      in_progress: $localize`:@@state.in_progress:in progress`,
      passed: $localize`:@@state.passed:passed`,
      failed: $localize`:@@state.failed:failed`,
    }[s];
  }
}
