/** UI navigation guards: backend RBAC and JWT signature checks remain authoritative. */
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth, type Role } from '../../hooks/useAuth';

/** Redirect unauthenticated browser navigation to login. */
export function PrivateRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
  if (!localStorage.getItem('secretary_token')) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

/** Role-specific UI navigation. API permissions are checked separately. */
export function RoleGuard({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { role } = useAuth();
  if (!roles.includes(role)) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
