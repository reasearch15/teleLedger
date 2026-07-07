"use client";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { usePaymentNotificationPreference } from "@/lib/payment-notifications";

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function SettingsPage() {
  const { user } = useAuth();
  const {
    enabled: paymentSoundEnabled,
    setEnabled: setPaymentSoundEnabled,
    setVolume: setPaymentSoundVolume,
    volume: paymentSoundVolume,
  } = usePaymentNotificationPreference();

  return (
    <AppShell title="Settings" description="Your local account and session details.">
      <div className="grid max-w-2xl gap-4">
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-5 py-4 sm:px-6">
            <h2 className="font-bold text-slate-950">Notifications</h2>
          </div>
          <div className="flex items-center justify-between gap-4 px-5 py-4 sm:px-6">
            <div>
              <h3 className="text-sm font-bold text-slate-950">
                Payment notification sound
              </h3>
              <p className="mt-1 text-sm text-slate-600">
                Play a short sound when a new active payment arrives.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={paymentSoundEnabled}
              aria-label="Payment notification sound"
              onClick={() => setPaymentSoundEnabled(!paymentSoundEnabled)}
              className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition ${
                paymentSoundEnabled ? "bg-indigo-600" : "bg-slate-300"
              }`}
            >
              <span
                className={`size-5 rounded-full bg-white shadow-sm transition ${
                  paymentSoundEnabled ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>
          <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
            <label
              htmlFor="payment-notification-volume"
              className="text-sm font-bold text-slate-950"
            >
              Notification Volume
            </label>
            <select
              id="payment-notification-volume"
              value={paymentSoundVolume}
              onChange={(event) =>
                setPaymentSoundVolume(
                  event.target.value as "low" | "medium" | "high",
                )
              }
              className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-900 sm:max-w-48"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>
        </section>

        {user ? (
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
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
      </div>
    </AppShell>
  );
}
