import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { apiError } from '../shared/api.service';
import { AuthService } from './auth.service';

/** Local sign-in (FR-001, FR-004): the role on the account decides which screen the person gets. */
@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="page">
      <section class="about">
        <div class="brand"><span class="dot"></span><span i18n="@@app.name">ISC Onboarding Agent</span></div>
        <div class="pitch">
          <h1 i18n="@@login.headline">Onboard applications into SailPoint ISC, together.</h1>
          <p i18n="@@login.lede">One live session for the application owner and the SailPoint IAM engineer. The agent gives the application owner setup steps with the values filled in, acts on SailPoint on the IAM engineer's order, and reads error screenshots to find the cause.</p>
          <ul>
            <li i18n="@@login.g1">The agent never acts on your application or asks for its credentials</li>
            <li i18n="@@login.g2">Every SailPoint change is recorded with who ordered it</li>
            <li i18n="@@login.g3">Secrets are masked before anyone sees them</li>
          </ul>
        </div>
      </section>
      <section class="form-side">
        <form class="card" (submit)="$event.preventDefault(); submit()">
          <h2 i18n="@@login.title">Sign in</h2>
          <p class="muted" i18n="@@login.hint">Local account. Your role decides which screen you get.</p>
          <label for="username" i18n="@@login.username">Username</label>
          <input id="username" name="username" autocomplete="username" [(ngModel)]="username" required />
          <label for="password" i18n="@@login.password">Password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" [(ngModel)]="password" required />
          @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
          <button type="submit" class="primary" [disabled]="busy() || !username || !password" i18n="@@login.submit">Sign in</button>
          <p class="muted small" i18n="@@login.noSignup">No self-registration. Ask an IAM engineer with admin rights for an account or a password reset.</p>
        </form>
      </section>
    </div>
  `,
  styles: `
    .page { min-height: 100vh; display: grid; grid-template-columns: repeat(auto-fit, minmax(min(26rem, 100%), 1fr)); }
    .about { background: #000; color: #fff; padding: 3rem; display: flex; flex-direction: column; gap: 3rem; }
    .brand { display: flex; align-items: center; gap: 0.6rem; font-weight: 700; font-size: 1.1rem; }
    .dot { width: 0.75rem; height: 0.75rem; border-radius: 50%; background: var(--highlight); }
    h1 { margin: 0 0 1rem; font-size: 2.1rem; line-height: 1.2; text-wrap: balance; }
    .pitch p { color: #d0d0ce; line-height: 1.6; max-width: 34rem; }
    .pitch ul { padding-left: 1.1rem; line-height: 1.9; color: #f2f2f2; }
    .form-side { display: flex; align-items: center; justify-content: center; padding: 3rem 1rem; }
    form { width: min(24rem, 100%); display: flex; flex-direction: column; gap: 0.45rem; padding: 1.75rem; }
    h2 { margin: 0; }
    input { margin-bottom: 0.4rem; }
    .primary { margin-top: 0.6rem; min-height: 44px; }
    .small { font-size: 0.78rem; }
  `,
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  protected username = '';
  protected password = '';
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);

  protected async submit(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.auth.login(this.username.trim().toLowerCase(), this.password);
      this.password = '';
      await this.router.navigate(this.auth.home());
    } catch (err) {
      this.error.set(apiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }
}
