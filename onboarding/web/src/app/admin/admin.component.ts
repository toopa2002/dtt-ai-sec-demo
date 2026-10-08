import { DatePipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, apiError } from '../shared/api.service';
import { Role, Tenant, User } from '../shared/models';

/**
 * Admin (FR-002): accounts and SailPoint tenants. Only IAM engineers carry the admin flag. A tenant's credential is
 * write-only: it goes to the server once and is never shown again (FR-025).
 */
@Component({
  selector: 'app-admin',
  imports: [FormsModule, DatePipe],
  template: `
    <div class="wrap">
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      @if (notice()) { <p class="notice" role="status">{{ notice() }}</p> }
      <div class="cols">
        <section class="card">
          <h2 i18n="@@admin.accounts">Accounts</h2>
          <div class="table">
            <table>
              <thead>
                <tr>
                  <th scope="col" i18n="@@admin.username">Username</th>
                  <th scope="col" i18n="@@admin.name">Name</th>
                  <th scope="col" i18n="@@admin.role">Role</th>
                  <th scope="col" i18n="@@admin.admin">Admin</th>
                  <th scope="col" i18n="@@admin.status">Status</th>
                  <th scope="col"><span class="sr-only" i18n="@@admin.actions">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                @for (u of users(); track u.id) {
                  <tr>
                    <td class="mono">{{ u.username }}</td>
                    <td>{{ u.display_name }}</td>
                    <td><span class="badge" [class.iam]="u.role === 'iam_engineer'" [class.owner]="u.role === 'application_owner'">{{ roleLabel(u.role) }}</span></td>
                    <td>{{ u.is_admin ? '✓' : '' }}</td>
                    <td [class.error]="u.status !== 'active'">{{ statusLabel(u) }}</td>
                    <td class="acts">
                      @if (u.status === 'locked') { <button type="button" (click)="update(u, { unlock: true })" i18n="@@admin.unlock">Unlock</button> }
                      @if (u.status === 'disabled') { <button type="button" (click)="update(u, { status: 'active' })" i18n="@@admin.enable">Enable</button> }
                      @else { <button type="button" (click)="update(u, { status: 'disabled' })" i18n="@@admin.disable">Disable</button> }
                      <button type="button" (click)="reset(u)" i18n="@@admin.reset">Reset password</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <form (submit)="$event.preventDefault(); createUser()">
            <h3 i18n="@@admin.newAccount">New account</h3>
            <div class="grid">
              <label for="nu-username" i18n="@@admin.username">Username</label>
              <input id="nu-username" name="username" [(ngModel)]="nu.username" required />
              <label for="nu-name" i18n="@@admin.name">Name</label>
              <input id="nu-name" name="display_name" [(ngModel)]="nu.display_name" required />
              <label for="nu-role" i18n="@@admin.role">Role</label>
              <select id="nu-role" name="role" [(ngModel)]="nu.role">
                <option value="iam_engineer" i18n="@@role.iam">IAM engineer</option>
                <option value="application_owner" i18n="@@role.owner">Application owner</option>
              </select>
              <label for="nu-admin" i18n="@@admin.adminFlag">Admin</label>
              <input id="nu-admin" type="checkbox" name="is_admin" [(ngModel)]="nu.is_admin" [disabled]="nu.role !== 'iam_engineer'" />
              <label for="nu-pass" i18n="@@admin.initialPassword">Initial password (12+ characters)</label>
              <input id="nu-pass" type="password" name="initial_password" autocomplete="new-password" [(ngModel)]="nu.initial_password" required minlength="12" />
            </div>
            <button type="submit" class="primary" i18n="@@admin.create">Create account</button>
          </form>
        </section>

        <section class="card">
          <h2 i18n="@@admin.tenants">SailPoint tenants</h2>
          <p class="muted small" i18n="@@admin.tenantsHint">One service credential per tenant (a personal access token with source-admin rights). It's stored outside this app and never shown again; the agent uses it for every SailPoint change.</p>
          @for (t of tenants(); track t.id) {
            <article class="tenant" [class.bad]="t.status !== 'usable'">
              <h3>{{ t.name }} <span class="chip" [class.passed]="t.status === 'usable'" [class.failed]="t.status === 'credential_rejected'">{{ tenantStatus(t) }}</span></h3>
              <dl>
                <dt i18n="@@admin.host">Address</dt><dd class="mono">{{ t.api_host }}</dd>
                <dt i18n="@@side.externalId">External ID</dt><dd class="mono">{{ t.external_id ?? '—' }}</dd>
                <dt i18n="@@admin.credential">Credential</dt><dd class="mono">client id …{{ t.credential_hint }} · secret ••••••••</dd>
                <dt i18n="@@admin.checked">Last checked</dt><dd>{{ t.last_checked_at ? (t.last_checked_at | date: 'd MMM, HH:mm') : '—' }}</dd>
              </dl>
              <button type="button" (click)="check(t)" i18n="@@admin.checkNow">Check now</button>
              <button type="button" (click)="replacing.set(t.id)" i18n="@@admin.replace">Replace credential</button>
              @if (replacing() === t.id) {
                <form (submit)="$event.preventDefault(); replace(t)" class="inline">
                  <label [for]="'rc-id-' + t.id" i18n="@@admin.clientId">Client ID</label>
                  <input [id]="'rc-id-' + t.id" name="client_id" [(ngModel)]="cred.client_id" autocomplete="off" />
                  <label [for]="'rc-secret-' + t.id" i18n="@@admin.clientSecret">Client secret</label>
                  <input [id]="'rc-secret-' + t.id" type="password" name="client_secret" [(ngModel)]="cred.client_secret" autocomplete="off" />
                  <button type="submit" class="primary" i18n="@@admin.save">Save</button>
                </form>
              }
            </article>
          } @empty {
            <p class="muted" i18n="@@admin.noTenants">No tenants yet.</p>
          }
          <form (submit)="$event.preventDefault(); addTenant()">
            <h3 i18n="@@admin.addTenant">Add tenant</h3>
            <div class="grid">
              <label for="nt-name" i18n="@@admin.tenantName">Name</label>
              <input id="nt-name" name="name" [(ngModel)]="nt.name" placeholder="acme-demo" required />
              <label for="nt-host" i18n="@@admin.tenantHost">Tenant host</label>
              <input id="nt-host" name="api_host" [(ngModel)]="nt.api_host" placeholder="acme.api.identitynow.com" required />
              <label for="nt-id" i18n="@@admin.clientId">Client ID</label>
              <input id="nt-id" name="client_id" [(ngModel)]="nt.client_id" autocomplete="off" required />
              <label for="nt-secret" i18n="@@admin.clientSecret">Client secret</label>
              <input id="nt-secret" type="password" name="client_secret" [(ngModel)]="nt.client_secret" autocomplete="off" required />
            </div>
            <button type="submit" class="primary" [disabled]="busy()" i18n="@@admin.addTenantButton">Add and check</button>
          </form>
        </section>
      </div>
    </div>
  `,
  styles: `
    .wrap { max-width: 90rem; margin: 0 auto; padding: 1.5rem; width: 100%; }
    .cols { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 1rem; align-items: start; }
    @media (max-width: 1100px) { .cols { grid-template-columns: minmax(0, 1fr); } }
    h2 { margin: 0 0 0.6rem; font-size: 1.05rem; }
    h3 { margin: 1rem 0 0.5rem; font-size: 0.9rem; display: flex; gap: 0.5rem; align-items: center; }
    .small { font-size: 0.78rem; }
    .table { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; min-width: 36rem; }
    th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); }
    thead th { font-size: 0.72rem; color: var(--muted); }
    .acts { display: flex; gap: 0.3rem; justify-content: flex-end; flex-wrap: wrap; }
    .acts button { min-height: 30px; padding: 0.15rem 0.5rem; font-size: 0.78rem; }
    .grid { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.4rem 0.75rem; align-items: center; }
    form .primary { margin-top: 0.75rem; }
    .tenant { border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem; margin-bottom: 0.75rem; }
    .tenant.bad { border-color: var(--bad); }
    .tenant h3 { margin-top: 0; }
    dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.25rem 0.75rem; font-size: 0.8rem; margin: 0 0 0.6rem; }
    dt { color: var(--muted); }
    dd { margin: 0; word-break: break-all; }
    .inline { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.4rem 0.75rem; margin-top: 0.6rem; }
    .inline .primary { grid-column: 2; justify-self: start; }
    .notice { color: var(--ok); }
  `,
})
export class AdminComponent implements OnInit {
  private readonly api = inject(ApiService);
  protected readonly users = signal<User[]>([]);
  protected readonly tenants = signal<Tenant[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly replacing = signal<string | null>(null);
  protected nu = { username: '', display_name: '', role: 'application_owner' as Role, is_admin: false, initial_password: '' };
  protected nt = { name: '', api_host: '', client_id: '', client_secret: '' };
  protected cred = { client_id: '', client_secret: '' };

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  private async reload(): Promise<void> {
    try {
      const [users, tenants] = await Promise.all([this.api.users(), this.api.tenants()]);
      this.users.set(users);
      this.tenants.set(tenants);
    } catch (err) {
      this.error.set(apiError(err).message);
    }
  }

  private async run(fn: () => Promise<unknown>, done: string): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.notice.set(null);
    try {
      await fn();
      this.notice.set(done);
      await this.reload();
    } catch (err) {
      this.error.set(apiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  protected roleLabel(r: Role): string {
    return r === 'iam_engineer' ? $localize`:@@role.iam:IAM engineer` : $localize`:@@role.owner:Application owner`;
  }

  protected statusLabel(u: User): string {
    if (u.status === 'locked' && u.locked_until) {
      const minutes = Math.max(1, Math.ceil((new Date(u.locked_until).getTime() - Date.now()) / 60000));
      return $localize`:@@admin.lockedFor:Locked · ${minutes}:minutes: min left`;
    }
    return { active: $localize`:@@admin.active:Active`, locked: $localize`:@@admin.locked:Locked`, disabled: $localize`:@@admin.disabled:Disabled` }[u.status];
  }

  protected tenantStatus(t: Tenant): string {
    return {
      usable: $localize`:@@admin.usable:credential OK`,
      credential_rejected: $localize`:@@admin.rejected:credential rejected`,
      unchecked: $localize`:@@admin.unchecked:not checked`,
    }[t.status];
  }

  protected update(u: User, body: Record<string, unknown>): Promise<void> {
    return this.run(() => this.api.updateUser(u.id, body), $localize`:@@admin.updated:Account updated.`);
  }

  protected reset(u: User): Promise<void> {
    const pw = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => 'abcdefghjkmnpqrstuvwxyz23456789'[b % 31]).join('');
    return this.run(
      () => this.api.updateUser(u.id, { new_password: pw }),
      $localize`:@@admin.resetDone:New password for ${u.username}:user:: ${pw}:password: — give it to them directly; it isn't shown again.`,
    );
  }

  protected createUser(): Promise<void> {
    const body = { ...this.nu, is_admin: this.nu.role === 'iam_engineer' && this.nu.is_admin };
    return this.run(async () => {
      await this.api.createUser(body);
      this.nu = { username: '', display_name: '', role: 'application_owner', is_admin: false, initial_password: '' };
    }, $localize`:@@admin.created:Account created.`);
  }

  protected addTenant(): Promise<void> {
    return this.run(async () => {
      await this.api.addTenant(this.nt);
      this.nt = { name: '', api_host: '', client_id: '', client_secret: '' };
    }, $localize`:@@admin.tenantAdded:Tenant added and checked.`);
  }

  protected replace(t: Tenant): Promise<void> {
    return this.run(async () => {
      await this.api.replaceCredential(t.id, this.cred);
      this.cred = { client_id: '', client_secret: '' };
      this.replacing.set(null);
    }, $localize`:@@admin.replaced:Credential replaced and checked.`);
  }

  protected check(t: Tenant): Promise<void> {
    return this.run(() => this.api.checkTenant(t.id), $localize`:@@admin.checkedDone:Checked.`);
  }
}
