import { Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { NewRunPage } from "./pages/NewRunPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/new" element={<NewRunPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
