"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { LoadingScreen } from "@/components/loading-screen";
import { friendlyError } from "@/lib/api-client";
import { NOTIFICATION_EVENTS } from "@/lib/live-events";
import {
  listNotifications,
  markNotificationRead,
} from "@/services/notifications";
import type { PersistentNotification, UserRole } from "@/types/api";

type AppShellProps = {
  title: string;
  description?: string;
  requiredRole?: UserRole;
  children: React.ReactNode;
};

const navigation = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/payments", label: "Payments" },
  { href: "/payment-history", label: "Payment History" },
  { href: "/cashout", label: "Cashout" },
  { href: "/venmo-confirmations", label: "Venmo" },
  { href: "/inquiry", label: "Inquiry" },
];

const adminNavigation = [
  ...navigation,
  { href: "/admin/ledger", label: "Ledger" },
  { href: "/admin/declined-payments", label: "Declined Payments" },
  { href: "/admin/coadmin-summary", label: "Coadmin Summary" },
  { href: "/admin/staff-balances", label: "Staff Balances" },
  { href: "/admin/adjustment-history", label: "Adjustment History" },
  { href: "/admin/settlement-history", label: "Settlement History" },
  { href: "/admin/staff", label: "Staff" },
  { href: "/settings", label: "Settings" },
];

const staffNavigation = [...navigation, { href: "/settings", label: "Settings" }];

const headerActionButtonClass =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50";

export function AppShell({
  title,
  description,
  requiredRole,
  children,
}: AppShellProps) {
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [logoutError, setLogoutError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

  const links = useMemo(
    () => (user?.role === "admin" ? adminNavigation : staffNavigation),
    [user?.role],
  );

  useEffect(() => {
    if (!loading && !user) {
      const next = encodeURIComponent(pathname);
      router.replace(`/login?next=${next}`);
    } else if (!loading && user && requiredRole && user.role !== requiredRole) {
      router.replace("/dashboard");
    }
  }, [loading, pathname, requiredRole, router, user]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setMenuOpen(false), 0);
    return () => window.clearTimeout(timeoutId);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [menuOpen]);

  if (loading || !user || (requiredRole && user.role !== requiredRole)) {
    return <LoadingScreen label="Checking your session..." />;
  }

  const handleLogout = async () => {
    setLogoutError("");
    try {
      await logout();
      router.replace("/login");
    } catch (error) {
      setLogoutError(friendlyError(error));
    }
  };

  return (
    <div className="min-h-screen w-full min-w-0 bg-slate-50">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto max-w-7xl min-w-0 px-4 py-3 sm:px-6">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[auto_auto] gap-3 lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:grid-rows-1 lg:items-center">
            <Link
              href="/dashboard"
              className="col-start-1 row-start-1 flex min-w-0 shrink items-center gap-3 lg:col-start-1"
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-600 text-sm font-black text-white">
                L
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-bold text-slate-950">Ledger</span>
                <span className="block text-xs text-slate-500">Payment operations</span>
              </span>
            </Link>

            <button
              type="button"
              onClick={handleLogout}
              className={`${headerActionButtonClass} col-start-2 row-start-1 shrink-0 px-2.5 py-1.5 text-xs lg:col-start-3 lg:row-start-1 lg:px-3 lg:py-2 lg:text-sm lg:font-semibold`}
            >
              Logout
            </button>

            <div className="col-span-2 row-start-2 grid min-w-0 grid-cols-2 gap-2 lg:col-span-1 lg:col-start-2 lg:row-start-1 lg:flex lg:items-center lg:justify-end lg:gap-3">
              <NotificationMenu
                mobileLabel="Alerts"
                desktopLabel="Notifications"
                buttonClassName="w-full justify-center lg:w-auto lg:justify-start"
              />
              <button
                type="button"
                onClick={() => setMenuOpen(true)}
                className={`${headerActionButtonClass} inline-flex w-full items-center justify-center lg:hidden`}
                aria-controls="mobile-admin-navigation"
                aria-expanded={menuOpen}
              >
                <span aria-hidden="true">&#9776;</span> Menu
              </button>
              <div className="hidden min-w-0 text-right lg:block">
                <p className="truncate text-sm font-semibold text-slate-800">
                  {user.username}
                </p>
                <p className="text-xs capitalize text-slate-500">{user.role}</p>
              </div>
            </div>
          </div>
        </div>

        <nav className="mx-auto hidden max-w-7xl gap-1 overflow-x-auto px-3 sm:px-5 lg:flex">
          {links.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-semibold transition ${
                  active
                    ? "border-indigo-600 text-indigo-700"
                    : "border-transparent text-slate-500 hover:text-slate-900"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </header>

      {menuOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation menu"
            className="absolute inset-0 bg-slate-950/40"
            onClick={() => setMenuOpen(false)}
          />
          <aside
            id="mobile-admin-navigation"
            aria-label="Admin navigation"
            className="relative h-full w-[min(20rem,calc(100vw-3rem))] translate-x-0 bg-white shadow-2xl transition-transform duration-200 ease-out"
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-4">
              <div>
                <p className="text-xs font-bold uppercase tracking-[0.18em] text-indigo-600">
                  Navigation
                </p>
                <p className="mt-1 font-black text-slate-950">Menu</p>
              </div>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                className={headerActionButtonClass}
              >
                Close
              </button>
            </div>
            <nav className="grid gap-1 p-3">
              {links.map((link) => {
                const active = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`rounded-xl px-4 py-3 text-sm font-bold transition ${
                      active
                        ? "bg-indigo-50 text-indigo-700"
                        : "text-slate-700 hover:bg-slate-50 hover:text-slate-950"
                    }`}
                  >
                    {link.label}
                  </Link>
                );
              })}
            </nav>
          </aside>
        </div>
      ) : null}

      <main className="mx-auto w-full min-w-0 max-w-7xl px-4 py-7 sm:px-6 sm:py-10">
        <div className="mb-7 min-w-0">
          <p className="mb-1 text-xs font-bold uppercase tracking-[0.18em] text-indigo-600">
            Operations
          </p>
          <h1 className="break-words text-2xl font-bold tracking-tight text-slate-950 sm:text-3xl">
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-2xl break-words text-sm leading-6 text-slate-600">
              {description}
            </p>
          ) : null}
          {logoutError ? (
            <p className="mt-3 text-sm font-medium text-red-700">{logoutError}</p>
          ) : null}
        </div>
        {children}
      </main>
    </div>
  );
}

type NotificationMenuProps = {
  mobileLabel: string;
  desktopLabel: string;
  buttonClassName?: string;
};

function NotificationMenu({
  mobileLabel,
  desktopLabel,
  buttonClassName = "",
}: NotificationMenuProps) {
  const router = useRouter();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<PersistentNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setError("");
    try {
      const response = await listNotifications({ limit: 20 });
      setItems(response.items);
      setUnreadCount(response.unread_count);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);

  useLiveUpdates(NOTIFICATION_EVENTS, refresh, Boolean(user));

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  const openNotification = async (notification: PersistentNotification) => {
    setError("");
    try {
      if (!notification.read_at) {
        await markNotificationRead(notification.id);
      }
      await refresh();
      setOpen(false);
      if (notification.navigation_href) {
        router.push(notification.navigation_href);
      }
    } catch (readError) {
      setError(friendlyError(readError));
    }
  };

  const panelPositionClass = "lg:left-auto lg:right-0";

  return (
    <div className="relative min-w-0">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={`relative inline-flex min-w-0 max-w-full items-center ${headerActionButtonClass} ${buttonClassName}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        {...(unreadCount > 0
          ? { "aria-label": `${desktopLabel}, ${unreadCount} unread` }
          : {})}
      >
        <span className="truncate lg:hidden">{mobileLabel}</span>
        <span className="hidden truncate lg:inline">{desktopLabel}</span>
        {unreadCount > 0 ? (
          <span className="ml-2 shrink-0 rounded-full bg-red-600 px-2 py-0.5 text-xs text-white">
            {unreadCount}
          </span>
        ) : null}
      </button>
      {open ? (
        <>
          <button
            type="button"
            aria-label="Close notifications"
            className="fixed inset-0 z-20 bg-transparent lg:hidden"
            onClick={() => setOpen(false)}
          />
          <div
            role="dialog"
            aria-label="Notifications"
            className={`fixed z-30 rounded-lg border border-slate-200 bg-white p-3 shadow-xl max-lg:inset-x-4 max-lg:top-24 max-lg:max-h-[min(24rem,calc(100dvh-7rem))] max-lg:overflow-y-auto lg:absolute lg:top-full lg:mt-2 lg:max-h-80 lg:w-[min(24rem,calc(100vw-2rem))] ${panelPositionClass}`}
          >
            <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
              <p className="truncate text-sm font-black text-slate-950">
                Notifications
              </p>
              <button
                type="button"
                onClick={() => void refresh()}
                className="shrink-0 text-xs font-bold text-indigo-600 disabled:opacity-50"
                disabled={loading}
              >
                Refresh
              </button>
            </div>
            {error ? (
              <p className="mb-2 break-words text-xs font-semibold text-red-700 [overflow-wrap:anywhere]">
                {error}
              </p>
            ) : null}
            {loading && items.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">Loading...</p>
            ) : null}
            {!loading && items.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">
                No notifications.
              </p>
            ) : null}
            <div className="max-h-80 overflow-y-auto">
              {items.map((notification) => (
                <button
                  key={notification.id}
                  type="button"
                  onClick={() => void openNotification(notification)}
                  className={`mb-2 block w-full min-w-0 rounded-lg border px-3 py-2 text-left text-sm transition hover:bg-slate-50 ${
                    notification.read_at
                      ? "border-slate-200 bg-white"
                      : "border-indigo-200 bg-indigo-50"
                  }`}
                >
                  <span className="block break-words font-bold text-slate-950 [overflow-wrap:anywhere]">
                    {notification.title}
                  </span>
                  {notification.body ? (
                    <span className="mt-1 block break-words text-xs text-slate-600 [overflow-wrap:anywhere]">
                      {notification.body}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
