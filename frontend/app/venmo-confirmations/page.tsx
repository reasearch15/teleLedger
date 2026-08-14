"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { friendlyError } from "@/lib/api-client";
import { listVenmoConfirmations } from "@/services/venmo-confirmations";
import type { VenmoConfirmationSummary } from "@/types/api";

const statusLabels: Record<string, string> = {
  pending: "Pending",
  confirmed: "Confirmed",
  not_received: "Not received",
  cancelled: "Cancelled",
};

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function VenmoConfirmationsPage() {
  const [items, setItems] = useState<VenmoConfirmationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems((await listVenmoConfirmations()).items);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);

  return (
    <AppShell
      title="Venmo Confirmations"
      description="Review confirmation requests, evidence, attempts, and follow-up status."
    >
      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm font-semibold text-red-700"
        >
          {error}
        </div>
      ) : null}
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate-600">
          {loading ? "Loading..." : `${items.length} requests`}
        </p>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="text-sm font-bold text-indigo-600 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>
      <section className="grid gap-3">
        {!loading && items.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm font-semibold text-slate-600">
            No Venmo confirmation requests found.
          </div>
        ) : null}
        {items.map((request) => (
          <Link
            key={request.id}
            href={`/venmo-confirmations/${request.id}`}
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-bold uppercase tracking-[0.16em] text-slate-500">
                  Request #{request.id}
                </p>
                <h2 className="mt-1 text-lg font-black text-slate-950">
                  {statusLabels[request.status]}
                </h2>
                <p className="mt-1 text-sm text-slate-600">
                  Staff: {request.requested_by_username ?? "Unknown"} · Coadmin:{" "}
                  {request.coadmin_username ?? request.coadmin_id}
                </p>
              </div>
              <span className="rounded-full border border-slate-300 px-2.5 py-1 text-xs font-black text-slate-700">
                {statusLabels[request.status]}
              </span>
            </div>
            {request.payment_note ? (
              <p className="mt-3 text-sm text-slate-700">{request.payment_note}</p>
            ) : null}
            <p className="mt-3 text-xs text-slate-500">
              Created {formatDate(request.created_at)}
              {request.confirmed_at
                ? ` · Confirmed ${formatDate(request.confirmed_at)}`
                : ""}
            </p>
          </Link>
        ))}
      </section>
    </AppShell>
  );
}
