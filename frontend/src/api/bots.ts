// Phase 2-05: Typed REST client for /api/v1/bots/*.
// apiClient.baseURL is `/api/v1` (see api/client.ts), so paths here are relative.

import { apiClient } from '@/api/client';
import type {
  BotCloneIn,
  BotCreateIn,
  BotDeleteIn,
  BotOut,
  BotRenameIn,
} from '@/api/types';

export interface ListBotsParams {
  q?: string;
  status?: string;
  // B1: backend supports a single `tag` query param (exact membership).
  // Multi-tag intersection is a Phase 5 enhancement.
  tag?: string;
}

export async function listBots(params?: ListBotsParams): Promise<BotOut[]> {
  const { data } = await apiClient.get<BotOut[]>('/bots', { params });
  return data;
}

export async function createBot(payload: BotCreateIn): Promise<BotOut> {
  const { data } = await apiClient.post<BotOut>('/bots', payload);
  return data;
}

export async function cloneBot(name: string, payload: BotCloneIn): Promise<BotOut> {
  const { data } = await apiClient.post<BotOut>(
    `/bots/${encodeURIComponent(name)}/clone`,
    payload,
  );
  return data;
}

export async function renameBot(name: string, payload: BotRenameIn): Promise<BotOut> {
  const { data } = await apiClient.patch<BotOut>(
    `/bots/${encodeURIComponent(name)}`,
    payload,
  );
  return data;
}

export async function deleteBot(name: string, payload: BotDeleteIn): Promise<void> {
  await apiClient.delete(`/bots/${encodeURIComponent(name)}`, { data: payload });
}
