/* eslint-disable react-refresh/only-export-components */
import { Navigate, createBrowserRouter } from 'react-router-dom';
import AppLayout from '@/components/AppLayout';
import LoginPage from '@/pages/login/LoginPage';
import BotsPage from '@/pages/bots/BotsPage';
import BotDetailPlaceholderPage from '@/pages/bots/BotDetailPlaceholderPage';
import AuditPage from '@/pages/audit/AuditPage';
import SettingsPage from '@/pages/settings/SettingsPage';
import PairingsCenterPage from '@/pages/pairings/PairingsCenterPage';
import { useAuth } from '@/stores/auth';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useAuth((s) => s.user);
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RedirectIfAuth({ children }: { children: React.ReactNode }) {
  const user = useAuth((s) => s.user);
  if (user) return <Navigate to="/bots" replace />;
  return <>{children}</>;
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <RedirectIfAuth>
        <LoginPage />
      </RedirectIfAuth>
    ),
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/bots" replace /> },
      { path: 'bots', element: <BotsPage /> },
      // BOT-09 — quick-link landing routes for /bots/{name}/{tab}.
      // Phase 3-02: 'setup' tab is now wired to WizardExecutionPage inside
      // BotDetailPlaceholderPage; chat/logs/skills/workspace stay as placeholders
      // until Phases 4-5 replace them with real per-tab pages.
      { path: 'bots/:name/:tab', element: <BotDetailPlaceholderPage /> },
      { path: 'pairings', element: <PairingsCenterPage /> },
      { path: 'audit', element: <AuditPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]);
