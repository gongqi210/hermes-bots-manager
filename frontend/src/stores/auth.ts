import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { TokenPair, UserOut, Role } from '@/api/types';

interface AuthState {
  user: UserOut | null;
  tokens: TokenPair | null;
  setAuth: (payload: { user: UserOut; tokens: TokenPair }) => void;
  updateAccessToken: (access_token: string, access_expires_in: number) => void;
  clear: () => void;
  getRole: () => Role | null;
}

export const useAuth = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      tokens: null,
      setAuth: ({ user, tokens }) => set({ user, tokens }),
      updateAccessToken: (access_token, access_expires_in) =>
        set((s) =>
          s.tokens
            ? {
                tokens: {
                  ...s.tokens,
                  access_token,
                  access_expires_in,
                },
              }
            : s,
        ),
      clear: () => set({ user: null, tokens: null }),
      getRole: () => get().user?.role ?? null,
    }),
    { name: 'hermes-console-auth' },
  ),
);
