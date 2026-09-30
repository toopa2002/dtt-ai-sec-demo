import { Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterOutlet } from '@angular/router';
import { MsalBroadcastService, MsalService } from '@azure/msal-angular';
import { EventMessage, EventType, InteractionStatus } from '@azure/msal-browser';
import { filter } from 'rxjs';

@Component({
  imports: [RouterOutlet],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App implements OnInit {
  private readonly msal = inject(MsalService);
  private readonly broadcast = inject(MsalBroadcastService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly user = signal<string | null>(null);

  ngOnInit(): void {
    this.msal.handleRedirectObservable().subscribe({
      error: (err) => this.showDenied(err),
    });

    // Sign-in failures (e.g. AADSTS50105: user not assigned to the chatbot app).
    this.broadcast.msalSubject$
      .pipe(
        filter((m: EventMessage) => m.eventType === EventType.ACQUIRE_TOKEN_FAILURE),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((m) => this.showDenied(m.error));

    this.broadcast.inProgress$
      .pipe(filter((s) => s === InteractionStatus.None), takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        const accounts = this.msal.instance.getAllAccounts();
        if (accounts.length && !this.msal.instance.getActiveAccount()) {
          this.msal.instance.setActiveAccount(accounts[0]);
        }
        this.user.set(this.msal.instance.getActiveAccount()?.username ?? null);
      });
  }

  protected switchPersona(): void {
    this.msal.logoutRedirect();
  }

  private showDenied(err: unknown): void {
    const message = err instanceof Error ? err.message : String(err ?? '');
    this.router.navigate(['/denied'], { state: { message } });
  }
}
