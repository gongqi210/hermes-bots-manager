import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { useAuth } from '@/stores/auth';
import type { AccessTokenOut } from '@/api/types';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export const apiClient = axios.create({
  baseURL: BASE,
  headers: { 'Content-Type': 'application/json' },
});

// Request interceptor — attach Bearer token if present.
apiClient.interceptors.request.use((config) => {
  const token = useAuth.getState().tokens?.access_token;
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor — on 401 (except from /auth/refresh itself), try one refresh+retry.
let isRefreshing = false;
let pending: Array<() => void> = [];

interface RetriableRequest extends InternalAxiosRequestConfig {
  _retried?: boolean;
}

apiClient.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as RetriableRequest | undefined;
    const status = error.response?.status;
    if (
      status !== 401 ||
      !original ||
      original._retried ||
      original.url?.includes('/auth/refresh') ||
      original.url?.includes('/auth/login') ||
      original.url?.includes('/auth/bootstrap')
    ) {
      throw error;
    }

    const refresh_token = useAuth.getState().tokens?.refresh_token;
    if (!refresh_token) {
      useAuth.getState().clear();
      throw error;
    }

    original._retried = true;

    if (isRefreshing) {
      // wait for ongoing refresh to finish, then retry
      await new Promise<void>((resolve) => pending.push(resolve));
      return apiClient(original);
    }

    isRefreshing = true;
    try {
      const resp = await axios.post<AccessTokenOut>(`${BASE}/auth/refresh`, { refresh_token });
      useAuth
        .getState()
        .updateAccessToken(resp.data.access_token, resp.data.access_expires_in);
      pending.forEach((r) => r());
      pending = [];
      return apiClient(original);
    } catch (refreshErr) {
      useAuth.getState().clear();
      pending.forEach((r) => r());
      pending = [];
      throw refreshErr;
    } finally {
      isRefreshing = false;
    }
  },
);
