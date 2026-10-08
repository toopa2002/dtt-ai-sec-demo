import { DatePipe } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, apiError } from '../shared/api.service';
import { AdminSession, Role, Tenant, User } from '../shared/models';

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
      <!-- US8: every session, with reopen and hand over (FR-032, FR-033); admins never chat in sessions -->
      <section class="card sessions" aria-labelledby="admin-sessions-h">
        <div class="sess-head">
          <h2 id="admin-sessions-h" i18n="@@admin.sessions">Sessions</h2>
          <span class="muted small" i18n="@@admin.sessionsHint">Every onboarding session. Reopen a finished one, or hand a place to another person with the same role. Admins don't chat in sessions; every reopen and handover is recorded.</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th i18n="@@admin.sSession">Session</th><th i18n="@@admin.sConnector">Connector · tenant</th>
                <th i18n="@@role.iam">IAM engineer</th><th i18n="@@role.owner">Application owner</th>
                <th i18n="@@admin.sStatus">Status</th><th i18n="@@admin.sPlan">Plan</th>
                <th i18n="@@admin.sLast">Last activity</th><th><span class="sr-only" i18n="@@admin.actions">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              @for (s of sessions(); track s.id) {
                <tr [class.open-form]="handing() === s.id" [attr.data-session]="s.id">
                  <td><b>{{ s.title }}</b></td>
                  <td>{{ s.connector_type }} · {{ s.tenant_name }}</td>
                  <td class="mono">{{ s.iam_engineer?.username }}
                    @if (s.iam_engineer && s.iam_engineer.status !== 'active') { <span class="bad small">{{ s.iam_engineer.status }}</span> }</td>
                  <td class="mono">{{ s.application_owner?.username ?? '—' }}
                    @if (s.application_owner && s.application_owner.status !== 'active') { <span class="bad small">{{ s.application_owner.status }}</span> }</td>
                  <td><span class="pill" [class.done]="s.status === 'finished'">{{ s.status === 'open' ? openText : finishedText }}</span>
                    @if (s.status === 'finished' && s.expires_at) { <span class="muted small">{{ deletedIn(s.expires_at) }}</span> }
                    @if (s.pending_handover) { <span class="muted small" i18n="@@admin.pending">handover after the current answer</span> }</td>
                  <td>{{ s.plan_done }} / {{ s.plan_total }}</td>
                  <td class="muted">{{ s.last_activity | date: 'd MMM, HH:mm' }}</td>
                  <td class="acts">
                    @if (s.status === 'finished') {
                      <button type="button" (click)="reopen(s)" [disabled]="busy()" i18n="@@admin.reopen">Reopen</button>
                    }
                    <button type="button" (click)="startHandover(s)" [attr.aria-expanded]="handing() === s.id" i18n="@@admin.handOver">Hand over</button>
                  </td>
                </tr>
                @if (handing() === s.id) {
                  <tr class="open-form">
                    <td colspan="8">
                      <form class="handover" (ngSubmit)="handOver(s)">
                        <label><span i18n="@@admin.place">Place</span>
                          <select name="place" [(ngModel)]="ho.place" (ngModelChange)="ho.user_id = ''">
                            <option value="application_owner">{{ ownerPlaceLabel(s) }}</option>
                            <option value="iam_engineer">{{ iamPlaceLabel(s) }}</option>
                          </select>
                        </label>
                        <label><span i18n="@@admin.handTo">Hand to</span>
                          <select name="user" [(ngModel)]="ho.user_id" required>
                            <option value="" disabled i18n="@@admin.pickUser">Choose a person…</option>
                            @for (u of candidates(s); track u.id) { <option [value]="u.id">{{ u.username }} · {{ u.display_name }}</option> }
                          </select>
                        </label>
                        <p class="muted small" i18n="@@admin.handoverHint">Only active people with that role are listed. The previous person loses access at once; the new one sees the full history and the plan. Both threads get a note about the handover.</p>
                        <button type="button" (click)="handing.set(null)" i18n="@@admin.cancel">Cancel</button>
                        <button type="submit" class="primary" [disabled]="busy() || !ho.user_id" i18n="@@admin.handOver">Hand over</button>
                      </form>
                    </td>
                  </tr>
                }
              } @empty {
                <tr><td colspan="8" class="muted" i18n="@@admin.noSessions">No sessions yet.</td></tr>
              }
            </tbody>
          </table>
        </div>
      </section>
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
    .sessions { margin-bottom: 1rem; }
    .sess-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.3rem 0.9rem; margin-bottom: 0.6rem; }
    .sess-head h2 { margin: 0; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.84rem; min-width: 56rem; }
    th { text-align: left; font-size: 0.75rem; color: var(--muted); font-weight: 600; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--border); }
    td { padding: 0.55rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    tr.open-form { background: color-mix(in srgb, var(--warn) 7%, transparent); }
    .acts { text-align: right; white-space: nowrap; }
    .acts button { min-height: 36px; margin-left: 0.3rem; }
    .pill { padding: 0.05rem 0.55rem; border-radius: 999px; background: color-mix(in srgb, var(--ok) 15%, transparent); color: var(--ok); font-weight: 600; font-size: 0.75rem; }
    .pill.done { background: var(--surface-2); color: var(--muted); }
    .bad { color: var(--bad); font-weight: 600; }
    .handover { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.75rem; }
    .handover label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8rem; font-weight: 600; }
    .handover p { flex: 1 1 18rem; margin: 0; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
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
  protected readonly sessions = signal<AdminSession[]>([]);
  protected readonly handing = signal<string | null>(null);
  protected ho: { place: Role; user_id: string } = { place: 'application_owner', user_id: '' };
  protected readonly openText = $localize`:@@admin.statusOpen:Open`;
  protected readonly finishedText = $localize`:@@admin.statusFinished:Finished`;
  protected nu = { username: '', display_name: '', role: 'application_owner' as Role, is_admin: false, initial_password: '' };
  protected nt = { name: '', api_host: '', client_id: '', client_secret: '' };
  protected cred = { client_id: '', client_secret: '' };

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  private async reload(): Promise<void> {
    try {
      const [users, tenants, sessions] = await Promise.all([this.api.users(), this.api.tenants(),
        this.api.adminSessions()]);
      this.users.set(users);
      this.tenants.set(tenants);
      this.sessions.set(sessions);
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

  protected deletedIn(iso: string): string {
    const days = Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
    return $localize`:@@admin.deletedIn:deleted in ${days}:days: days`;
  }

  protected ownerPlaceLabel(s: AdminSession): string {
    return $localize`:@@admin.ownerPlace:Application owner (${s.application_owner?.username ?? '—'}:user:)`;
  }

  protected iamPlaceLabel(s: AdminSession): string {
    return $localize`:@@admin.iamPlace:IAM engineer (${s.iam_engineer?.username ?? '—'}:user:)`;
  }

  /** Active people with the place's role who hold neither place in this session (FR-033). */
  protected candidates(s: AdminSession): User[] {
    const taken = new Set([s.iam_engineer?.id, s.application_owner?.id]);
    return this.users().filter((u) => u.role === this.ho.place && u.status === 'active' && !taken.has(u.id));
  }

  protected startHandover(s: AdminSession): void {
    this.ho = { place: 'application_owner', user_id: '' };
    this.handing.set(this.handing() === s.id ? null : s.id);
  }

  protected reopen(s: AdminSession): Promise<void> {
    return this.run(() => this.api.reopenSession(s.id), $localize`:@@admin.reopened:Session reopened.`);
  }

  protected handOver(s: AdminSession): Promise<void> {
    const { place, user_id } = this.ho;
    return this.run(async () => {
      const r = await this.api.handOver(s.id, place, user_id);
      this.handing.set(null);
      if (!r.applied) this.notice.set($localize`:@@admin.handoverPending:The handover takes effect when the agent's current answer ends.`);
    }, $localize`:@@admin.handedOver:Place handed over.`);
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
