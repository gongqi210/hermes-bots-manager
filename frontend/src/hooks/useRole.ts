import { useAuth } from '@/stores/auth';
import type { Role } from '@/api/types';

export function useRole(): Role | null {
  return useAuth((s) => s.user?.role ?? null);
}
