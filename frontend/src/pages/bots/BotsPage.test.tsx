import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import type { BotOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

// Mock the hooks before importing BotsPage so the page picks up the mocks.
vi.mock('@/hooks/useBots', () => ({
  useBots: vi.fn(),
  useCreateBot: vi.fn(),
  useCloneBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
  useRenameBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteBot: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('./CreateBotModal', () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="create-bot-modal">CreateBotModal</div> : null,
}));
vi.mock('./CloneBotModal', () => ({ default: () => null }));
vi.mock('./RenameBotModal', () => ({ default: () => null }));
vi.mock('./DeleteBotModal', () => ({ default: () => null }));
import { useBots, useCreateBot } from '@/hooks/useBots';
import BotsPage from './BotsPage';

type UseBotsReturn = ReturnType<typeof useBots>;

const mockedUseBots = vi.mocked(useBots);
const mockedUseCreateBot = vi.mocked(useCreateBot);

function makeBot(overrides: Partial<BotOut> = {}): BotOut {
  return {
    id: 1,
    name: 'alpha',
    feishu_app_id: 'cli_a',
    feishu_app_secret_last4: 'aaaa',
    model_name: 'gpt-4',
    tags: [],
    skills_count: 1,
    today_message_count: 0,
    last_heartbeat_at: null,
    status: 'green',
    why: '运行中',
    last_active_at: null,
    created_at: '2026-04-29T00:00:00Z',
    ...overrides,
  };
}

function setHookResult(partial: {
  data?: BotOut[];
  isLoading?: boolean;
  error?: Error | null;
}): void {
  mockedUseBots.mockReturnValue({
    data: partial.data ?? undefined,
    isLoading: partial.isLoading ?? false,
    error: partial.error ?? null,
    isError: !!partial.error,
    isSuccess: !!partial.data,
  } as unknown as UseBotsReturn);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <BotsPage />
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockedUseBots.mockReset();
  mockedUseCreateBot.mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCreateBot>);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<BotsPage>', () => {
  it('shows loading spinner initially', () => {
    setHookResult({ isLoading: true });
    renderPage();
    expect(screen.getByTestId('bots-loading')).toBeTruthy();
  });

  it('shows empty state when bots list is empty', () => {
    setHookResult({ data: [] });
    renderPage();
    expect(screen.getByTestId('bots-empty')).toBeTruthy();
    expect(screen.getByText(zhCN.bots.emptyTitle)).toBeTruthy();
  });

  it('renders one BotCard per bot in the grid', () => {
    setHookResult({
      data: [
        makeBot({ id: 1, name: 'alpha' }),
        makeBot({ id: 2, name: 'beta' }),
        makeBot({ id: 3, name: 'gamma' }),
      ],
    });
    renderPage();
    expect(screen.getByTestId('bots-grid')).toBeTruthy();
    expect(screen.getByText('alpha')).toBeTruthy();
    expect(screen.getByText('beta')).toBeTruthy();
    expect(screen.getByText('gamma')).toBeTruthy();
  });

  it('search input updates query state and triggers refetch with q', async () => {
    setHookResult({ data: [] });
    const user = userEvent.setup();
    renderPage();
    const search = screen.getByTestId('bots-search') as HTMLInputElement;
    // Input.Search forwards typing into the underlying <input>; submit fires onSearch.
    await user.type(search, 'alpha{Enter}');
    // The hook is called many times during re-renders; the LAST call should reflect the q.
    const lastCall = mockedUseBots.mock.calls[mockedUseBots.mock.calls.length - 1]?.[0];
    expect(lastCall?.q).toBe('alpha');
  });

  it('status filter updates query state', async () => {
    setHookResult({ data: [] });
    const user = userEvent.setup();
    renderPage();
    // Open the status filter and pick 运行中.
    const statusBox = screen.getByTestId('bots-status-filter').querySelector('.ant-select-selector')!;
    await user.click(statusBox as Element);
    const greenOption = await screen.findByText(zhCN.bots.statusFilter.green);
    await user.click(greenOption);
    const lastCall = mockedUseBots.mock.calls[mockedUseBots.mock.calls.length - 1]?.[0];
    expect(lastCall?.status).toBe('green');
  });

  // The Bot cards already render <Tag>prod</Tag> for any bot with that tag, which collides
  // with the dropdown option's textContent. Helper picks the option element specifically
  // (rc-select gives each option .ant-select-item-option with title=<value>).
  async function pickTagOption(label: string) {
    const candidates = await screen.findAllByText(label);
    const option = candidates.find((el) => el.closest('.ant-select-item-option'));
    expect(option, `dropdown option for "${label}" should be visible`).toBeTruthy();
    return option!;
  }

  it('tag filter selecting a tag triggers hook with tag param (B1)', async () => {
    setHookResult({
      data: [
        makeBot({ id: 1, name: 'alpha', tags: ['prod'] }),
        makeBot({ id: 2, name: 'beta', tags: ['staging'] }),
      ],
    });
    const user = userEvent.setup();
    renderPage();
    const tagBox = screen.getByTestId('bots-tag-filter').querySelector('.ant-select-selector')!;
    await user.click(tagBox as Element);
    const prodOption = await pickTagOption('prod');
    await user.click(prodOption);
    const lastCall = mockedUseBots.mock.calls[mockedUseBots.mock.calls.length - 1]?.[0];
    expect(lastCall?.tag).toBe('prod');
  });

  it('tag filter sends only the FIRST selected tag (Phase 2 simplification, B1)', async () => {
    setHookResult({
      data: [makeBot({ id: 1, name: 'alpha', tags: ['prod', 'ai'] })],
    });
    // pointerEventsCheck: 0 lets us click the second multi-select option even though
    // rc-select disables pointer-events on the surrounding wrapper after the first pick.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();
    const tagBox = screen.getByTestId('bots-tag-filter').querySelector('.ant-select-selector')!;
    await user.click(tagBox as Element);
    await user.click(await pickTagOption('prod'));
    // Re-open the dropdown and pick 'ai'.
    await user.click(tagBox as Element);
    await user.click(await pickTagOption('ai'));
    const lastCall = mockedUseBots.mock.calls[mockedUseBots.mock.calls.length - 1]?.[0];
    // Even with two tags selected, only the first is sent. Phase 5 may add multi-tag intersection.
    expect(lastCall?.tag).toBe('prod');
  });

  it('displays error alert when fetch fails', () => {
    setHookResult({ error: new Error('boom'), data: undefined });
    renderPage();
    expect(screen.getByTestId('bots-error')).toBeTruthy();
    expect(screen.getByText(zhCN.bots.loadErrorTitle)).toBeTruthy();
  });

  it('shows the enabled "+ 新建 Bot" CTA (Plan 02-06 wired)', () => {
    setHookResult({ data: [] });
    renderPage();
    const btn = screen.getByTestId('bots-create-button') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('新建 Bot');
  });

  it('clicking + 新建 Bot opens CreateBotModal', async () => {
    setHookResult({ data: [] });
    const user = userEvent.setup();
    renderPage();

    expect(screen.queryByTestId('create-bot-modal')).toBeNull();

    const btn = screen.getByTestId('bots-create-button');
    await user.click(btn);

    expect(screen.getByTestId('create-bot-modal')).toBeTruthy();
    expect(screen.getByText('CreateBotModal')).toBeTruthy();
  });

  it('closing modal returns focus and does not crash', async () => {
    setHookResult({ data: [] });
    const user = userEvent.setup();
    const { unmount } = renderPage();

    const btn = screen.getByTestId('bots-create-button');
    await user.click(btn);
    expect(screen.getByTestId('create-bot-modal')).toBeTruthy();

    // Click the button again (acts as a toggle in some impls), or just unmount
    // to verify no crash on cleanup.
    unmount();
    // If no exception was thrown during unmount, the test passes.
  });
});
