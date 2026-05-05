import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthSummary from './HealthSummary';

vi.mock('@/api/management', () => ({
  getHealth: vi.fn(),
}));

import { getHealth } from '@/api/management';
import type { HealthOut } from '@/api/types';

const mockedGetHealth = vi.mocked(getHealth);

function renderSummary(overrides: Partial<HealthOut> = {}) {
  const data: HealthOut = {
    bot_name: 'foo',
    gateway_state: 'running',
    gateway_why: 'ok',
    model_configured: true,
    workspace_status: 'ok',
    skills_enabled: 1,
    skills_total: 1,
    dangerous_skill_count: 0,
    shadowed_skill_count: 0,
    allowlist_preset: 'custom',
    overall: 'ok',
    ...overrides,
  };
  mockedGetHealth.mockResolvedValue(data);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <HealthSummary botName="foo" />
    </QueryClientProvider>,
  );
}

beforeEach(() => mockedGetHealth.mockReset());
afterEach(() => mockedGetHealth.mockReset());

describe('<HealthSummary>', () => {
  it('H1: existing fields render (gateway state + skills ratio)', async () => {
    renderSummary();
    expect(await screen.findByTestId('health-gateway-state')).toBeTruthy();
    expect(document.body.textContent).toContain('1/1');
  });

  it('H2: dangerous_skill_count=2 shows red 危险技能 badge', async () => {
    renderSummary({ dangerous_skill_count: 2 });
    const badge = await screen.findByTestId('health-dangerous-badge');
    expect(badge.textContent).toContain('危险技能');
    expect(badge.textContent).toContain('2');
  });

  it('H3: shadowed_skill_count=1 shows orange 被遮蔽 badge', async () => {
    renderSummary({ shadowed_skill_count: 1 });
    const badge = await screen.findByTestId('health-shadowed-badge');
    expect(badge.textContent).toContain('被遮蔽');
    expect(badge.textContent).toContain('1');
  });

  it('H4: allowlist_preset=open shows 开放测试 tag', async () => {
    renderSummary({ allowlist_preset: 'open' });
    const tag = await screen.findByTestId('health-preset-tag');
    expect(tag.textContent).toContain('开放测试');
  });

  it('H5: zero counts hide danger/shadow badges', async () => {
    renderSummary({ dangerous_skill_count: 0, shadowed_skill_count: 0 });
    await screen.findByTestId('health-gateway-state');
    expect(screen.queryByTestId('health-dangerous-badge')).toBeNull();
    expect(screen.queryByTestId('health-shadowed-badge')).toBeNull();
  });
});
