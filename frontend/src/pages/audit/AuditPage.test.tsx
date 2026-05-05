import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AuditPage from './AuditPage';

vi.mock('@/api/management', async () => {
  const actual = await vi.importActual<typeof import('@/api/management')>(
    '@/api/management',
  );
  return { ...actual, listAudit: vi.fn() };
});

import { listAudit } from '@/api/management';
const mockedList = vi.mocked(listAudit);

const SAMPLE = [
  {
    id: 1,
    actor_id: 7,
    actor_ip: '127.0.0.1',
    method: 'PUT',
    path: '/api/v1/bots/foo/workspace',
    target_type: 'bot',
    target_id: 'foo',
    result: 'success',
    error: null,
    created_at: '2026-05-04T13:00:00Z',
  },
  {
    id: 2,
    actor_id: 7,
    actor_ip: '127.0.0.1',
    method: 'PUT',
    path: '/api/v1/bots/ghost/workspace',
    target_type: null,
    target_id: null,
    result: 'failure',
    error: 'bot not found',
    created_at: '2026-05-04T13:01:00Z',
  },
];

function renderPage() {
  mockedList.mockResolvedValue(SAMPLE);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuditPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedList.mockReset();
});
afterEach(() => {
  mockedList.mockReset();
});

describe('<AuditPage>', () => {
  it('renders rows with method/path/result tags', async () => {
    renderPage();
    expect(await screen.findByTestId('audit-page')).toBeTruthy();
    await waitFor(() => {
      expect(document.body.textContent).toContain('/api/v1/bots/foo/workspace');
      expect(document.body.textContent).toContain('failure');
    });
  });

  it('search applies filters', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('audit-page');
    const actorInput = screen.getByTestId('filter-actor-id');
    await user.type(actorInput, '7');
    await user.click(screen.getByTestId('btn-audit-search'));
    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith(
        expect.objectContaining({ actor_id: 7, limit: 100 }),
      ),
    );
  });
});
