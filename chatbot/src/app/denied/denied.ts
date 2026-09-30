import { Component, inject } from '@angular/core';
import { Router } from '@angular/router';
import { MsalService } from '@azure/msal-angular';

@Component({
  selector: 'app-denied',
  template: `
    <section class="denied">
      <h2>🔒 Access denied</h2>
      @if (notAssigned) {
        <p>Your account is not assigned to the chatbot. Ask an admin to assign you to <code>mcpdemo-chatbot</code> in Entra ID.</p>
      } @else {
        <p>Sign-in did not complete.</p>
      }
      @if (message) { <pre>{{ message }}</pre> }
      <button type="button" (click)="retry()">Sign in with a different account</button>
    </section>
  `,
  styles: `
    :host { flex: 1; display: flex; justify-content: center; }
    .denied { max-width: 640px; padding: 2rem 1rem; }
    pre { white-space: pre-wrap; font-size: 0.8rem; color: var(--muted); background: var(--surface); padding: 0.75rem; border-radius: 6px; border: 1px solid var(--border); }
  `,
})
export class Denied {
  private readonly msal = inject(MsalService);
  protected readonly message: string = inject(Router).currentNavigation()?.extras.state?.['message'] ?? history.state?.message ?? '';
  protected readonly notAssigned = this.message.includes('AADSTS50105');

  protected retry(): void {
    this.msal.logoutRedirect();
  }
}
