import {
  History,
  LayoutDashboard,
  Menu,
  Moon,
  Plus,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAppTheme } from "./AppThemeProvider";

const navigation = [
  { to: "/", label: "控制台", icon: LayoutDashboard, end: true },
  { to: "/runs/new", label: "新建分析", icon: Plus },
  { to: "/runs", label: "任务历史", icon: History },
  { to: "/settings", label: "设置", icon: Settings },
];

export function AppShell() {
  const [open, setOpen] = useState(false);
  const { theme, toggleTheme } = useAppTheme();

  return (
    <div className="app-shell">
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">TA</div>
          <div>
            <strong>TradingAgents</strong>
            <span>Analysis Center</span>
          </div>
          <button className="icon-button mobile-only" onClick={() => setOpen(false)} aria-label="关闭导航">
            <X size={18} />
          </button>
        </div>
        <nav className="side-nav" aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setOpen(false)}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <Icon size={19} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="local-panel">
          <div className="live-dot" />
          <div>
            <strong>本机服务</strong>
            <span>127.0.0.1 · 单任务队列</span>
          </div>
        </div>
      </aside>
      {open && <button className="sidebar-scrim" onClick={() => setOpen(false)} aria-label="关闭导航" />}
      <section className="workspace">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setOpen(true)} aria-label="打开导航">
            <Menu size={20} />
          </button>
          <div className="topbar-context">
            <span className="live-dot" />
            数据与模型仅在本机运行
          </div>
          <button
            className="icon-button"
            onClick={toggleTheme}
            aria-label="切换明暗主题"
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </header>
        <main className="page-container">
          <Outlet />
        </main>
      </section>
    </div>
  );
}
