"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { LoadingScreen } from "@/components/loading-screen";
import { friendlyError } from "@/lib/api-client";
import type { UserRole } from "@/types/api";

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
];

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

  useEffect(() => {
    if (!loading && !user) {
      const next = encodeURIComponent(pathname);
      router.replace(`/login?next=${next}`);
    } else if (!loading && user && requiredRole && user.role !== requiredRole) {
      router.replace("/dashboard");
    }
  }, [loading, pathname, requiredRole, router, user]);

  if (loading || !user || (requiredRole && user.role !== requiredRole)) {
    return <LoadingScreen label="Checking your session…" />;
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

  const links =
    user.role === "admin"
      ? [
          ...navigation,
          { href: "/admin/ledger", label: "Ledger" },
          { href: "/admin/staff", label: "Staff" },
          { href: "/settings", label: "Settings" },
        ]
      : [...navigation, { href: "/settings", label: "Settings" }];

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link href="/dashboard" className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-600 text-sm font-black text-white">
              L
            </span>
            <span>
              <span className="block text-sm font-bold text-slate-950">
                Ledger
              </span>
              <span className="block text-xs text-slate-500">
                Payment operations
              </span>
            </span>
          </Link>
          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm font-semibold text-slate-800">{user.username}</p>
              <p className="text-xs capitalize text-slate-500">{user.role}</p>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
            >
              Logout
            </button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-3 sm:px-5">
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

      <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 sm:py-10">
        <div className="mb-7">
          <p className="mb-1 text-xs font-bold uppercase tracking-[0.18em] text-indigo-600">
            Operations
          </p>
          <h1 className="text-2xl font-bold tracking-tight text-slate-950 sm:text-3xl">
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
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
