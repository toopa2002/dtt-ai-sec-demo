import { Routes } from '@angular/router';
import { MsalGuard } from '@azure/msal-angular';
import { Chat } from './chat/chat';
import { Denied } from './denied/denied';

export const routes: Routes = [
  { path: '', component: Chat, canActivate: [MsalGuard] },
  { path: 'denied', component: Denied },
  { path: '**', redirectTo: '' },
];
