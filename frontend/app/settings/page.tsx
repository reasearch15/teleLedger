"use client";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <AppShell title="Settings" description="Your local account and session details.">
      {user ? (
        <section className="max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-5 py-4 sm:px-6">
            <h2 className="font-bold text-slate-950">Account profile</h2>
          </div>
          <dl className="divide-y divide-slate-100">
            {[
              ["Username", user.username],
              ["Role", user.role],
              ["Status", user.is_active ? "Active" : "Disabled"],
              ["Last login", formatDate(user.last_login_at)],
              ["Account created", formatDate(user.created_at)],
            ].map(([label, value]) => (
              <div
                key={label}
                className="grid gap-1 px-5 py-4 sm:grid-cols-[160px_1fr] sm:px-6"
              >
                <dt className="text-sm font-medium text-slate-500">{label}</dt>
                <dd className="text-sm font-semibold capitalize text-slate-900">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
    </AppShell>
  );
}

