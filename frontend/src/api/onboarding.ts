// Phase 4-10: Frontend onboarding API client.
// Backend contract: GET /onboarding/runs?limit=N, POST /onboarding/{id}/mark-message-received.

import { apiClient } from '@/api/client';
import type { OnboardingRunOut } from '@/api/types';

export async function listMyRuns(limit = 10): Promise<OnboardingRunOut[]> {
  const { data } = await apiClient.get<OnboardingRunOut[]>('/onboarding/runs', {
    params: { limit },
  });
  return data;
}

export async function markMessageReceived(
  runId: number,
): Promise<{ id: number; status: 'success' }> {
  const { data } = await apiClient.post<{ id: number; status: 'success' }>(
    `/onboarding/${runId}/mark-message-received`,
  );
  return data;
}
