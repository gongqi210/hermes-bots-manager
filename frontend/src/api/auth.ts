import { apiClient } from '@/api/client';
import type { LoginResponse, UserOut } from '@/api/types';

export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>('/auth/login', { username, password });
  return data;
}

export async function bootstrap(username: string, password: string): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>('/auth/bootstrap', { username, password });
  return data;
}

export async function getMe(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/auth/me');
  return data;
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout');
}

export async function createUser(input: {
  username: string;
  password: string;
  role: 'Admin' | 'Editor' | 'Viewer';
}): Promise<UserOut> {
  const { data } = await apiClient.post<UserOut>('/auth/users', input);
  return data;
}
