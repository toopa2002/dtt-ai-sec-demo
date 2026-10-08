import { inject } from '@angular/core';
import { ActivatedRouteSnapshot, CanActivateFn, Router } from '@angular/router';
import { Role } from '../shared/models';
import { AuthService } from './auth.service';

/** Signed in at all. */
// inject() only works before the first await (injection context), so every guard resolves its services up front.
export const signedIn: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return (await auth.load()) ? true : router.createUrlTree(['/login']);
};

/** Each role reaches only its own screen (FR-003); the API enforces the same rules. */
export function roleGuard(role: Role): CanActivateFn {
  return async (route: ActivatedRouteSnapshot) => {
    const auth = inject(AuthService);
    const router = inject(Router);
    const me = await auth.load();
    if (!me) return router.createUrlTree(['/login']);
    if (me.role === role) return true;
    const id = route.paramMap.get('id');
    return id
      ? router.createUrlTree(['/sessions', id, me.role === 'iam_engineer' ? 'iam' : 'owner'])
      : router.createUrlTree(['/sessions']);
  };
}

export const adminGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const me = await auth.load();
  return me?.is_admin ? true : router.createUrlTree(['/sessions']);
};
