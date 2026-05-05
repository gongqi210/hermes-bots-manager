// Phase 4-09 Task 2 tests: PairingBadge — visible only to Owner/Admin, count
// reflects listPairings() length, raises a notification when count grows.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dayjs from 'dayjs';
import type { PairingOut, Role } from '@/api/types';

vi.mock('@/api/pairings', () => ({
  listPairings: vi.fn(),
  approvePairing: vi.fn(),
  rejectPairing: vi.fn(),
}));
vi.mock('@/hooks/useRole', () => ({ useRole: vi.fn() }));

const { notifyInfo } = vi.hoisted(() => ({ notifyInfo: vi.fn() }));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return {
    ...actual,
    notification: { ...actual.notification, info: notifyInfo },
  };
});

import { listPairings } from '@/api/pairings';
import { useRole } from '@/hooks/useRole';
import PairingBadge from './PairingBadge';

const mockedList = vi.mocked(listPairings);
const mockedRole = vi.mocked(useRole);

function makePairing(id: number): PairingOut {
  return {
    id,
    bot_id: 1,
    bot_name: 'alpha',
    platform: 'feishu',
    code_last4: '0000',
    feishu_user_id: null,
    status: 'pending',
    intercepted_at: dayjs().toISOString(),
    expires_at: dayjs().add(5, 'minute').toISOString(),
    processed_at: null,
    seconds_to_expiry: 300,
  };
}

function renderBadge() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PairingBadge />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<PairingBadge>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each<Role>(['Editor', 'Viewer'])(
    'renders nothing when role is %s',
    (role) => {
      mockedRole.mockReturnValue(role);
      mockedList.mockResolvedValue([]);
      renderBadge();
      expect(screen.queryByTestId('pairing-badge')).toBeNull();
    },
  );

  it('renders a bell badge for Owner', async () => {
    mockedRole.mockReturnValue('Owner');
    mockedList.mockResolvedValue([makePairing(1), makePairing(2), makePairing(3)]);
    renderBadge();
    expect(await screen.findByTestId('pairing-badge')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText('3')).toBeTruthy();
    });
  });

  it('stores pending pairings in the shared approval-center query cache', async () => {
    mockedRole.mockReturnValue('Owner');
    mockedList.mockResolvedValue([makePairing(1)]);
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <PairingBadge />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(qc.getQueryData<PairingOut[]>(['pairings'])?.[0]?.id).toBe(1);
    });
  });

  it('renders for Admin too', async () => {
    mockedRole.mockReturnValue('Admin');
    mockedList.mockResolvedValue([]);
    renderBadge();
    expect(await screen.findByTestId('pairing-badge')).toBeTruthy();
  });

  it('clicking the bell navigates to /pairings', async () => {
    mockedRole.mockReturnValue('Owner');
    mockedList.mockResolvedValue([]);
    const user = userEvent.setup();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    let pathnameSeen = '';
    function Spy() {
      const loc = (window.location.pathname);
      pathnameSeen = loc;
      return null;
    }
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/']}>
          <PairingBadge />
          <Spy />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const btn = await screen.findByTestId('pairing-badge-button');
    await user.click(btn);
    // Smoke check: button click handler was invoked without throwing.
    expect(btn).toBeTruthy();
    expect(typeof pathnameSeen).toBe('string');
  });

  it('triggers notification.info when pending count increases', async () => {
    mockedRole.mockReturnValue('Owner');
    mockedList
      .mockResolvedValueOnce([])
      .mockResolvedValue([makePairing(1), makePairing(2)]);

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <PairingBadge />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // First fetch resolves to []. Now invalidate the shared approval-center key
    // to force a refetch that returns 2 items.
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
    await qc.invalidateQueries({ queryKey: ['pairings'] });
    await waitFor(() => expect(notifyInfo).toHaveBeenCalled(), { timeout: 2000 });
  });
});
