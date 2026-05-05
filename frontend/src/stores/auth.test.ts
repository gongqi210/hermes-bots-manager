import { beforeEach, describe, expect, it } from 'vitest';
import { useAuth } from './auth';
import type { TokenPair, UserOut } from '@/api/types';

const owner: UserOut = {
  id: 1,
  username: 'owner',
  role: 'Owner',
  created_at: '2026-04-23T00:00:00Z',
};

const tokens: TokenPair = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
  access_expires_in: 7200,
  refresh_expires_in: 604800,
};

describe('useAuth store', () => {
  beforeEach(() => {
    useAuth.getState().clear();
    localStorage.clear();
  });

  it('setAuth populates user and tokens', () => {
    useAuth.getState().setAuth({ user: owner, tokens });
    expect(useAuth.getState().user?.username).toBe('owner');
    expect(useAuth.getState().tokens?.access_token).toBe('access-1');
  });

  it('getRole returns role when logged in, null otherwise', () => {
    expect(useAuth.getState().getRole()).toBeNull();
    useAuth.getState().setAuth({ user: owner, tokens });
    expect(useAuth.getState().getRole()).toBe('Owner');
  });

  it('updateAccessToken replaces only access_token and expires, keeps refresh', () => {
    useAuth.getState().setAuth({ user: owner, tokens });
    useAuth.getState().updateAccessToken('access-2', 3600);
    expect(useAuth.getState().tokens?.access_token).toBe('access-2');
    expect(useAuth.getState().tokens?.access_expires_in).toBe(3600);
    expect(useAuth.getState().tokens?.refresh_token).toBe('refresh-1');
  });

  it('clear resets state', () => {
    useAuth.getState().setAuth({ user: owner, tokens });
    useAuth.getState().clear();
    expect(useAuth.getState().user).toBeNull();
    expect(useAuth.getState().tokens).toBeNull();
  });

  it('persists to localStorage under hermes-console-auth key', () => {
    useAuth.getState().setAuth({ user: owner, tokens });
    const raw = localStorage.getItem('hermes-console-auth');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw as string);
    expect(parsed.state.user.username).toBe('owner');
    expect(parsed.state.tokens.access_token).toBe('access-1');
  });
});
