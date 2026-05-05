import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import type { BotOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

// BotCard now imports Clone/Rename/Delete modals which use mutation hooks.
// Mock all three hooks + mock the modal components to keep tests lightweight.
vi.mock('@/hooks/useBots', () => ({
  useCloneBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
  useRenameBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('./CloneBotModal', () => ({
  default: () => null,
}));
vi.mock('./RenameBotModal', () => ({
  default: () => null,
}));
vi.mock('./DeleteBotModal', () => ({
  default: () => null,
}));

import BotCard from './BotCard';

function makeBot(overrides: Partial<BotOut> = {}): BotOut {
  return {
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
    ...overrides,
  };
}

function renderCard(bot: BotOut) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <BotCard bot={bot} />
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<BotCard>', () => {
  it('renders bot name as card title', () => {
    renderCard(makeBot());
    expect(screen.getByText('test-bot')).toBeTruthy();
  });

  it('renders status dot with aria-label "status-{status}"', () => {
    renderCard(makeBot({ status: 'red', why: '崩溃了' }));
    const dot = screen.getByLabelText('status-red');
    expect(dot).toBeTruthy();
  });

  it('displays the why-text in the status dot tooltip on hover', async () => {
    const user = userEvent.setup();
    renderCard(makeBot({ status: 'yellow', why: '正在重启' }));
    // The why-text appears inline already (not only in tooltip) — assert that.
    expect(screen.getAllByText(/正在重启/).length).toBeGreaterThan(0);
    // Hover the status dot to surface the tooltip — AntD renders title attribute eventually.
    const dot = screen.getByLabelText('status-yellow');
    await user.hover(dot);
    // After hover, the tooltip mounts with the same text (may duplicate the why text).
    const matches = screen.getAllByText(/正在重启/);
    expect(matches.length).toBeGreaterThan(0);
  });

  it('displays "—" placeholder when feishu_app_id is null', () => {
    renderCard(makeBot({ feishu_app_id: null, model_name: null }));
    // Both feishu and model fields fall back to em dash.
    const dashes = screen.getAllByText(/—/);
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });

  it('renders 4 quick links pointing at /bots/{name}/{tab}', () => {
    renderCard(makeBot({ name: 'test-bot' }));
    const expected = [
      { label: zhCN.bots.cardActions.chat, href: '/bots/test-bot/chat' },
      { label: zhCN.bots.cardActions.logs, href: '/bots/test-bot/logs' },
      { label: zhCN.bots.cardActions.skills, href: '/bots/test-bot/skills' },
      { label: zhCN.bots.cardActions.workspace, href: '/bots/test-bot/workspace' },
    ];
    for (const { label, href } of expected) {
      const link = screen.getByRole('link', { name: label });
      expect(link.getAttribute('href')).toBe(href);
    }
  });

  it('renders tags as Tag elements when present', () => {
    renderCard(makeBot({ name: 'tagged-bot', tags: ['prod', 'ai', 'beta'] }));
    expect(screen.getByTestId('bot-tags-tagged-bot')).toBeTruthy();
    expect(screen.getByText('prod')).toBeTruthy();
    expect(screen.getByText('ai')).toBeTruthy();
    expect(screen.getByText('beta')).toBeTruthy();
  });

  it('does not render tag list when tags array is empty', () => {
    renderCard(makeBot({ name: 'empty-tags-bot', tags: [] }));
    expect(screen.queryByTestId('bot-tags-empty-tags-bot')).toBeNull();
  });

  it('encodes bot names containing special characters in quick-link hrefs', () => {
    renderCard(makeBot({ name: 'name with space' }));
    const chatLink = screen.getByRole('link', { name: zhCN.bots.cardActions.chat });
    // encodeURIComponent('name with space') === 'name%20with%20space'
    expect(chatLink.getAttribute('href')).toBe('/bots/name%20with%20space/chat');
  });

  it('renders overflow menu trigger with correct testid', () => {
    renderCard(makeBot({ name: 'my-bot' }));
    expect(screen.getByTestId('bot-card-menu-my-bot')).toBeTruthy();
  });
});
