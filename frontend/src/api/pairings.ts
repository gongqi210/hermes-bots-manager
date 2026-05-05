// Phase 4-09: Pairing approval API client.
// Wraps the REST endpoints exposed by Phase 4-05's pairings router.

import { apiClient } from './client';
import type { PairingActionResponse, PairingOut } from './types';

export async function listPairings(botName?: string): Promise<PairingOut[]> {
  const params = botName ? { bot_name: botName } : undefined;
  const { data } = await apiClient.get<PairingOut[]>('/pairings', { params });
  return data;
}

export async function approvePairing(id: number): Promise<PairingActionResponse> {
  const { data } = await apiClient.post<PairingActionResponse>(`/pairings/${id}/approve`);
  return data;
}

export async function rejectPairing(id: number): Promise<PairingActionResponse> {
  const { data } = await apiClient.post<PairingActionResponse>(`/pairings/${id}/reject`);
  return data;
}
