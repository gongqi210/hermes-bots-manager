import { beforeEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { RoleGuard } from './RoleGuard';
import { useAuth } from '@/stores/auth';
import type { TokenPair, UserOut } from '@/api/types';

const tokens: TokenPair = {
  access_token: 'a',
  refresh_token: 'r',
  token_type: 'bearer',
  access_expires_in: 7200,
  refresh_expires_in: 604800,
};

function setUserRole(role: UserOut['role'] | null) {
  if (role === null) {
    useAuth.getState().clear();
    return;
  }
  useAuth.getState().setAuth({
    user: { id: 1, username: 'u', role, created_at: '2026-04-23T00:00:00Z' },
    tokens,
  });
}

describe('<RoleGuard>', () => {
  beforeEach(() => {
    useAuth.getState().clear();
    localStorage.clear();
  });

  it('renders nothing when logged out', () => {
    render(
      <RoleGuard role="Viewer">
        <span>secret</span>
      </RoleGuard>,
    );
    expect(screen.queryByText('secret')).toBeNull();
  });

  it('hides Admin-only children from Viewer', () => {
    setUserRole('Viewer');
    render(
      <RoleGuard role="Admin">
        <span>admin-only</span>
      </RoleGuard>,
    );
    expect(screen.queryByText('admin-only')).toBeNull();
  });

  it('shows Admin-only children to Admin and Owner', () => {
    setUserRole('Admin');
    const { rerender } = render(
      <RoleGuard role="Admin">
        <span>admin-only</span>
      </RoleGuard>,
    );
    expect(screen.getByText('admin-only')).toBeTruthy();

    act(() => setUserRole('Owner'));
    rerender(
      <RoleGuard role="Admin">
        <span>admin-only</span>
      </RoleGuard>,
    );
    expect(screen.getByText('admin-only')).toBeTruthy();
  });

  it('renders fallback when below minimum role', () => {
    setUserRole('Viewer');
    render(
      <RoleGuard role="Owner" fallback={<span>denied</span>}>
        <span>owner-only</span>
      </RoleGuard>,
    );
    expect(screen.getByText('denied')).toBeTruthy();
    expect(screen.queryByText('owner-only')).toBeNull();
  });
});
