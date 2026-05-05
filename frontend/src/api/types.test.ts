import { describe, expect, it } from 'vitest';
import { roleAtLeast } from './types';

describe('roleAtLeast', () => {
  it('returns false when current role is null/undefined', () => {
    expect(roleAtLeast(null, 'Viewer')).toBe(false);
    expect(roleAtLeast(undefined, 'Viewer')).toBe(false);
  });

  it('Owner satisfies every minimum', () => {
    expect(roleAtLeast('Owner', 'Viewer')).toBe(true);
    expect(roleAtLeast('Owner', 'Editor')).toBe(true);
    expect(roleAtLeast('Owner', 'Admin')).toBe(true);
    expect(roleAtLeast('Owner', 'Owner')).toBe(true);
  });

  it('Viewer fails anything above Viewer', () => {
    expect(roleAtLeast('Viewer', 'Viewer')).toBe(true);
    expect(roleAtLeast('Viewer', 'Editor')).toBe(false);
    expect(roleAtLeast('Viewer', 'Admin')).toBe(false);
    expect(roleAtLeast('Viewer', 'Owner')).toBe(false);
  });

  it('Admin satisfies Admin and below, fails Owner', () => {
    expect(roleAtLeast('Admin', 'Viewer')).toBe(true);
    expect(roleAtLeast('Admin', 'Editor')).toBe(true);
    expect(roleAtLeast('Admin', 'Admin')).toBe(true);
    expect(roleAtLeast('Admin', 'Owner')).toBe(false);
  });
});
