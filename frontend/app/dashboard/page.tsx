"use client";

import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <AppShell
      title="Dashboard"
      description="Your starting point for today’s payment operations."
    >
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Link
          href="/payments"
          className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
        >
          <span className="mb-5 grid h-11 w-11 place-items-center rounded-xl bg-red-50 text-lg font-black text-red-600">
            $
          </span>
          <h2 className="text-lg font-bold text-slate-950">Payment ledger</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Review new payments, claim work, and mark completed operations.
          </p>
          <p className="mt-5 text-sm font-bold text-indigo-600 group-hover:text-indigo-700">
            Open payments →
          </p>
        </Link>

        <Link
          href="/cashout"
          className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
        >
          <span className="mb-5 grid h-11 w-11 place-items-center rounded-xl bg-amber-50 text-sm font-black text-amber-700">
            CO
          </span>
          <h2 className="text-lg font-bold text-slate-950">Cashout requests</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Create a cashout request and follow its delivery status.
          </p>
          <p className="mt-5 text-sm font-bold text-indigo-600 group-hover:text-indigo-700">
            Open cashouts →
          </p>
        </Link>

        {user?.role === "admin" ? (
          <Link
            href="/admin/staff"
            className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
          >
            <span className="mb-5 grid h-11 w-11 place-items-center rounded-xl bg-indigo-50 text-sm font-black text-indigo-600">
              ST
            </span>
            <h2 className="text-lg font-bold text-slate-950">Staff management</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              Create accounts, disable access, and reset staff passwords.
            </p>
            <p className="mt-5 text-sm font-bold text-indigo-600 group-hover:text-indigo-700">
              Manage staff →
            </p>
          </Link>
        ) : null}

        <Link
          href="/settings"
          className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
        >
          <span className="mb-5 grid h-11 w-11 place-items-center rounded-xl bg-emerald-50 text-sm font-black text-emerald-600">
            ME
          </span>
          <h2 className="text-lg font-bold text-slate-950">Account</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Check your signed-in identity and current access level.
          </p>
          <p className="mt-5 text-sm font-bold text-indigo-600 group-hover:text-indigo-700">
            View settings →
          </p>
        </Link>
      </section>
    </AppShell>
  );
}
