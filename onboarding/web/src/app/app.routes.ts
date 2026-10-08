import { Routes } from '@angular/router';
import { AdminComponent } from './admin/admin.component';
import { LoginComponent } from './auth/login.component';
import { adminGuard, roleGuard, signedIn } from './auth/role.guard';
import { CatalogComponent } from './catalog/catalog.component';
import { NewSessionComponent } from './session/new-session.component';
import { SessionListComponent } from './session/session-list.component';
import { SessionScreenComponent } from './session/session-screen.component';

export const routes: Routes = [
  { path: 'login', component: LoginComponent, title: $localize`:@@title.login:Sign in · ISC Onboarding Agent` },
  { path: 'sessions', component: SessionListComponent, canActivate: [signedIn], title: $localize`:@@title.sessions:Sessions · ISC Onboarding Agent` },
  {
    path: 'sessions/new/:type',
    component: NewSessionComponent,
    canActivate: [roleGuard('iam_engineer')],
    title: $localize`:@@title.new:New session · ISC Onboarding Agent`,
  },
  {
    path: 'sessions/:id/iam',
    component: SessionScreenComponent,
    canActivate: [roleGuard('iam_engineer')],
    data: { role: 'iam_engineer' },
    title: $localize`:@@title.iam:IAM engineer · ISC Onboarding Agent`,
  },
  {
    path: 'sessions/:id/owner',
    component: SessionScreenComponent,
    canActivate: [roleGuard('application_owner')],
    data: { role: 'application_owner' },
    title: $localize`:@@title.owner:Application owner · ISC Onboarding Agent`,
  },
  { path: 'catalog', component: CatalogComponent, canActivate: [signedIn], title: $localize`:@@title.catalog:Connector catalog · ISC Onboarding Agent` },
  { path: 'admin', component: AdminComponent, canActivate: [adminGuard], title: $localize`:@@title.admin:Administration · ISC Onboarding Agent` },
  { path: '', pathMatch: 'full', redirectTo: 'sessions' },
  { path: '**', redirectTo: 'sessions' },
];
