// Phase 2-05 Task 4: Tests for BotDetailPlaceholderPage + nested /bots/:name/:tab routing (BOT-09).
// Validates that quick-link navigation from BotCard lands on the placeholder instead of
// being swallowed by the catch-all route.

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import BotDetailPlaceholderPage from './BotDetailPlaceholderPage';
import type { BotOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

// ─── Mock useBots hooks so tests don't need a QueryClient ─────────────────────
vi.mock('@/hooks/useBots', () => ({
  useBots: vi.fn(),
  useCreateBot: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false })),
  useCloneBot: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRenameBot: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteBot: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));
// Mock modal components rendered inside BotCard so tests stay lightweight
vi.mock('./CreateBotModal', () => ({ default: () => null }));
vi.mock('./CloneBotModal', () => ({ default: () => null }));
vi.mock('./RenameBotModal', () => ({ default: () => null }));
vi.mock('./DeleteBotModal', () => ({ default: () => null }));
// Mock WizardExecutionPage so the setup-tab routing test stays lightweight
vi.mock('./WizardExecutionPage', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-wizard-execution">wizard:{botName}</div>
  ),
}));
vi.mock('./LogStreamView', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-log-stream">logs:{botName}</div>
  ),
}));
vi.mock('./GatewayControlPanel', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-gateway-control">gateway:{botName}</div>
  ),
}));
vi.mock('./ModelConfigPage', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-model-config">chat:{botName}</div>
  ),
}));
vi.mock('./WorkspacePage', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-workspace-page">workspace:{botName}</div>
  ),
}));
vi.mock('./SkillsPage', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="mock-skills-page">skills:{botName}</div>
  ),
}));
import { useBots } from '@/hooks/useBots';
import BotsPage from './BotsPage';

const botFixture: BotOut = {
  id: 1,
  name: 'test-bot',
  feishu_app_id: 'cli_xxx',
  feishu_app_secret_last4: 'xxxx',
  model_name: 'gpt-4',
  tags: ['prod'],
  skills_count: 5,
  today_message_count: 12,
  last_heartbeat_at: null,
  status: 'green',
  why: '运行中',
  last_active_at: null,
  created_at: '2026-04-29T00:00:00Z',
};

// ─── Helper: render page at a specific /bots/:name/:tab route ─────────────────
function renderAtRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/bots" element={<BotsPage />} />
        <Route path="/bots/:name/:tab" element={<BotDetailPlaceholderPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('<BotDetailPlaceholderPage>', () => {
  // ─── Test 1: Valid tab renders the implemented page ──────────────────────
  it('chat tab renders the model config page', async () => {
    renderAtRoute('/bots/alpha/chat');
    const el = await screen.findByTestId('mock-model-config');
    expect(el).toBeTruthy();
    expect(el.textContent).toContain('alpha');
  });

  it('renders current bot name and active tab above implemented pages', async () => {
    renderAtRoute('/bots/alpha/workspace');
    expect(await screen.findByTestId('bot-detail-header')).toBeTruthy();
    expect(screen.getByTestId('bot-detail-name').textContent).toBe('alpha');
    expect(screen.getByTestId('bot-detail-tab').textContent).toContain('Workspace');
    expect(screen.getByTestId('mock-workspace-page')).toBeTruthy();
  });

  // ─── Test 2: Invalid tab renders 404-style result ──────────────────────────
  it('renders 404-style result for invalid tab', () => {
    renderAtRoute('/bots/alpha/unknown-tab');
    expect(screen.getByTestId('bot-detail-unknown-tab')).toBeTruthy();
  });

  // ─── Test 3: Unknown-tab fallback still has back link ──────────────────────
  it('unknown tab still offers back link to /bots', async () => {
    renderAtRoute('/bots/alpha/unknown');
    await screen.findByTestId('bot-detail-unknown-tab');
    const backLinks = screen.getAllByRole('link');
    const botsLink = backLinks.find((l) => l.getAttribute('href') === '/bots');
    expect(botsLink).toBeTruthy();
  });

  // ─── Test (Phase 3): setup tab renders WizardExecutionPage, not placeholder ──
  it('setup tab renders WizardExecutionPage instead of placeholder Result', () => {
    renderAtRoute('/bots/alpha/setup');
    expect(screen.getByTestId('mock-wizard-execution')).toBeTruthy();
    // The 404 branch should NOT have triggered for setup
    expect(screen.queryByTestId('bot-detail-unknown-tab')).toBeNull();
    // The placeholder Result should NOT render either
    expect(
      screen.queryByTestId('bot-detail-placeholder-alpha-setup'),
    ).toBeNull();
  });

  it('setup is recognized as a valid tab (no 404 Result)', () => {
    renderAtRoute('/bots/alpha/setup');
    expect(screen.queryByTestId('bot-detail-unknown-tab')).toBeNull();
  });

  it('logs tab renders LogStreamView instead of placeholder Result', () => {
    renderAtRoute('/bots/alpha/logs');
    expect(screen.getByTestId('mock-log-stream')).toBeTruthy();
    expect(screen.queryByTestId('bot-detail-placeholder-alpha-logs')).toBeNull();
  });

  // ─── Test 4 (B2): Clicking BotCard quick links lands on routed tab ─────────
  // Uses MemoryRouter with initialEntries so the full routing chain
  // (list → placeholder) can be exercised. Each tab is tested individually.
  it('clicking quick links from BotsPage lands on the routed detail tab, not list', async () => {
    const user = userEvent.setup();
    const tabs = ['chat', 'gateway', 'logs', 'skills', 'workspace'] as const;

    for (const tab of tabs) {
      vi.mocked(useBots).mockReturnValue({
        data: [botFixture],
        isLoading: false,
        error: null,
        isError: false,
        isSuccess: true,
      } as ReturnType<typeof useBots>);

      const { unmount } = render(
        <MemoryRouter initialEntries={['/bots']}>
          <Routes>
            <Route path="/bots" element={<BotsPage />} />
            <Route path="/bots/:name/:tab" element={<BotDetailPlaceholderPage />} />
          </Routes>
        </MemoryRouter>,
      );

      // Click the quick link for this tab (rendered inside BotCard)
      const linkText = zhCN.bots.cardActions[tab];
      const link = await screen.findByText(linkText);
      await user.click(link);

      const expectedTestId =
        tab === 'logs'
          ? 'mock-log-stream'
          : tab === 'gateway'
            ? 'mock-gateway-control'
          : tab === 'chat'
            ? 'mock-model-config'
            : tab === 'workspace'
              ? 'mock-workspace-page'
              : 'mock-skills-page';
      expect(await screen.findByTestId(expectedTestId)).toBeTruthy();

      unmount();
    }
  });
});
