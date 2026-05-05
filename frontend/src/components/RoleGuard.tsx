import type { PropsWithChildren } from 'react';
import { useRole } from '@/hooks/useRole';
import { roleAtLeast, type Role } from '@/api/types';

interface Props {
  role: Role; // minimum role required
  fallback?: React.ReactNode;
}

/** Hide children from users below `role`. Backend must ALSO enforce — UI hiding is not a security boundary. */
export function RoleGuard({ role, children, fallback = null }: PropsWithChildren<Props>) {
  const current = useRole();
  if (!roleAtLeast(current, role)) return <>{fallback}</>;
  return <>{children}</>;
}
