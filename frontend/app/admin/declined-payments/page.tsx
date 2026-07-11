"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { DECLINED_PAYMENTS_PAGE_EVENTS } from "@/lib/live-events";
import {
  deletePayment,
  dismissDeclinedPaymentReview,
  listDeclinedPayments,
  PAYMENT_HISTORY_PAGE_SIZE,
} from "@/services/payments";
import type { Payment } from "@/types/api";

function formatMoney(value: string | null): string {
  if (value === null) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function AdminDeclinedPaymentsPage() {
  const [payments, setPayments] = useState<Payment[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const requestVersion = useRef(0);

  const loadFirstPage = useCallback(async () => {
    const requestId = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const page = await listDeclinedPayments({
        limit: PAYMENT_HISTORY_PAGE_SIZE,
        offset: 0,
      });
      if (requestId !== requestVersion.current) return;
      setPayments(page.items);
      setTotal(page.total);
      setHasMore(page.has_more);
    } catch (loadError) {
      if (requestId === requestVersion.current) {
        setError(friendlyError(loadError));
      }
    } finally {
      if (requestId === requestVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  useLiveUpdates(DECLINED_PAYMENTS_PAGE_EVENTS, () => {
    void loadFirstPage();
  }, true);

  const loadMore = async () => {
    if (loadingMore || !hasMore) return;
    const requestId = requestVersion.current;
    setLoadingMore(true);
    try {
      const page = await listDeclinedPayments({
        limit: PAYMENT_HISTORY_PAGE_SIZE,
        offset: payments.length,
      });
      if (requestId !== requestVersion.current) return;
      setPayments((current) => [...current, ...page.items]);
      setTotal(page.total);
      setHasMore(page.has_more);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoadingMore(false);
    }
  };

  const handleDismiss = async (paymentId: number) => {
    setActionId(paymentId);
    setError("");
    setMessage("");
    try {
      await dismissDeclinedPaymentReview(paymentId);
      setPayments((current) => current.filter((payment) => payment.id !== paymentId));
      setMessage("Payment removed from admin review.");
    } catch (dismissError) {
      setError(friendlyError(dismissError));
    } finally {
      setActionId(null);
    }
  };

  const handleDelete = async (payment: Payment) => {
    const confirmed = window.confirm(
      `Permanently delete this ${formatMoney(payment.amount)} payment from TeleLedger? This cannot be undone.`,
    );
    if (!confirmed) return;

    setActionId(payment.id);
    setError("");
    setMessage("");
    try {
      await deletePayment(payment.id);
      setPayments((current) => current.filter((item) => item.id !== payment.id));
      setMessage("Payment permanently deleted.");
    } catch (deleteError) {
      setError(friendlyError(deleteError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <AppShell
      title="Declined Payments"
      description="Payments marked Not Ours by every active coadmin team, awaiting admin review."
      requiredRole="admin"
    >
      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
        >
          {error}
        </div>
      ) : null}
      {message ? (
        <div
          role="status"
          className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700"
        >
          {message}
        </div>
      ) : null}

      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-slate-900">Admin Review Queue</h2>
          <p className="text-sm font-medium text-slate-600">
            {loading
              ? "Loading declined payments…"
              : total === null
                ? `${payments.length}${hasMore ? "+" : ""} payments`
                : `${payments.length} of ${total} payments`}
          </p>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => void loadFirstPage()}
          className="text-sm font-bold text-indigo-600 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {!loading && payments.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <p className="font-bold text-slate-800">No declined payments awaiting review</p>
          <p className="mt-2 text-sm text-slate-600">
            Payments appear here once every active coadmin team has marked them Not Ours.
          </p>
        </div>
      ) : (
        <section className="grid gap-4">
          {payments.map((payment) => {
            const busy = actionId === payment.id;
            return (
              <article
                key={payment.id}
                className="overflow-hidden rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 shadow-sm"
              >
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                      <span className="rounded-full border border-amber-400 bg-white px-2.5 py-1 text-xs font-bold text-amber-900">
                        Declined by all coadmins
                      </span>
                      <h2 className="text-xl font-black text-slate-950 sm:text-2xl">
                        {formatMoney(payment.amount)}
                      </h2>
                      <p className="text-sm text-slate-700">
                        <strong>{payment.payment_sender_name}</strong>
                        {" → "}
                        <strong>{payment.recipient_tag}</strong>
                      </p>
                      <span className="text-xs font-medium text-slate-500">
                        Payment date: {formatDate(payment.payment_datetime)}
                      </span>
                      <span className="text-xs font-medium text-slate-500">
                        All declined: {formatDate(payment.all_coadmins_declined_at)}
                      </span>
                    </div>

                    <div className="rounded-lg border border-amber-200 bg-white p-3">
                      <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-600">
                        Coadmin Declines
                      </h3>
                      <ul className="space-y-2">
                        {payment.coadmin_dismissals.map((dismissal) => (
                          <li
                            key={`${dismissal.coadmin_id}-${dismissal.created_at}`}
                            className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-slate-700"
                          >
                            <strong>
                              {dismissal.coadmin_username ?? "Deleted Coadmin"}
                            </strong>
                            <span>
                              Not Ours by{" "}
                              {dismissal.dismissed_by_staff_username ?? "Unknown staff"}
                            </span>
                            <span className="text-slate-500">
                              {formatDate(dismissal.created_at)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 lg:justify-end">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleDismiss(payment.id)}
                      className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-bold text-slate-700 disabled:opacity-50"
                    >
                      Dismiss
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleDelete(payment)}
                      className="rounded-lg border border-red-300 bg-white px-3.5 py-2 text-sm font-bold text-red-700 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </article>
            );
          })}

          {hasMore ? (
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
              className="mx-auto rounded-lg border border-indigo-300 bg-white px-5 py-2.5 text-sm font-bold text-indigo-700"
            >
              {loadingMore ? "Loading more…" : "Load more"}
            </button>
          ) : null}
        </section>
      )}
    </AppShell>
  );
}
