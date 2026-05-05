// Phase 2-05: TanStack Query hooks around the bots REST API.
// Pattern: BOT_KEYS centralizes query keys so 02-06 can extend with mutations
// that invalidate the same scope.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cloneBot,
  createBot,
  deleteBot,
  listBots,
  renameBot,
  type ListBotsParams,
} from '@/api/bots';
import type {
  BotCloneIn,
  BotCreateIn,
  BotDeleteIn,
  BotRenameIn,
} from '@/api/types';

export const BOT_KEYS = {
  all: ['bots'] as const,
  list: (q?: string, status?: string, tag?: string) =>
    [...['bots'] as const, 'list', q ?? '', status ?? '', tag ?? ''] as const,
};

export function useBots(filter: ListBotsParams = {}) {
  return useQuery({
    queryKey: BOT_KEYS.list(filter.q, filter.status, filter.tag),
    queryFn: () => listBots(filter),
    staleTime: 5_000,
    refetchInterval: 5_000,
  });
}

export function useCreateBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BotCreateIn) => createBot(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: BOT_KEYS.all }),
  });
}

export function useCloneBot(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BotCloneIn) => cloneBot(name, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: BOT_KEYS.all }),
  });
}

export function useRenameBot(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BotRenameIn) => renameBot(name, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: BOT_KEYS.all }),
  });
}

export function useDeleteBot(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BotDeleteIn) => deleteBot(name, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: BOT_KEYS.all }),
  });
}
