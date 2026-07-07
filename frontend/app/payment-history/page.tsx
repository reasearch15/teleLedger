"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { PAYMENT_HISTORY_EVENTS } from "@/lib/live-events";
import { friendlyError } from "@/lib/api-client";
import {
  listMyPaymentHistory,
  listPaymentHistory,
  PAYMENT_HISTORY_PAGE_SIZE,
  reopenPayment,
} from "@/services/payments";
import type { Payment, PaymentPage, PaymentStatus } from "@/types/api";

const statusLabels: Record<PaymentStatus, string> = {
  pending: "Pending",
  in_progress: "Claimed",
  done: "Done",
};

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

function StaffBadge({
  username,
  color,
  label,
}: {
  username: string;
  color: string;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700">
      <span
        className="grid size-5 place-items-center rounded-full text-[10px] font-black text-white"
        style={{ backgroundColor: color }}
      >
        {username.slice(0, 1).toUpperCase()}
      </span>
      {label}: {username}
    </span>
  );
}

export default function PaymentHistoryPage() {
  const { user } = useAuth();
  const userId = user?.id;
  const [payments, setPayments] = useState<Payment[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);

  const fetchPage = useCallback(
    (offset: number): Promise<PaymentPage> => {
      const pagination = {
        limit: PAYMENT_HISTORY_PAGE_SIZE,
        offset,
      };
      return user?.role === "admin"
        ? listPaymentHistory(pagination)
        : listMyPaymentHistory(pagination);
    },
    [user?.role],
  );

  const loadFirstPage = useCallback(async () => {
    if (!userId) return;
    const requestId = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const page = await fetchPage(0);
      if (requestId !== requestVersion.current) return;
      setPayments(page.items);
      setHasMore(page.has_more);
    } catch (loadError) {
      if (requestId === requestVersion.current) {
        setError(friendlyError(loadError));
      }
    } finally {
      if (requestId === requestVersion.current) setLoading(false);
    }
  }, [fetchPage, userId]);

  useEffect(() => {
    if (!userId) return;
    const timeoutId = window.setTimeout(() => {
      void loadFirstPage();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadFirstPage, userId]);

  const refreshHistory = useCallback(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  useLiveUpdates(PAYMENT_HISTORY_EVENTS, refreshHistory, Boolean(userId));

  const loadMore = async () => {
    if (!userId || loadingMore || !hasMore) return;
    const requestId = requestVersion.current;
    setLoadingMore(true);
    try {
      const page = await fetchPage(payments.length);
      if (requestId !== requestVersion.current) return;
      setPayments((current) => [...current, ...page.items]);
      setHasMore(page.has_more);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoadingMore(false);
    }
  };

  const reopen = async (paymentId: number) => {
    setActionId(paymentId);
    setError("");
    try {
      await reopenPayment(paymentId);
      setPayments((current) =>
        current.filter((payment) => payment.id !== paymentId),
      );
    } catch (actionError) {
      setError(friendlyError(actionError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <AppShell
      title="Payment History"
      description={
        user?.role === "admin"
          ? "Review claimed and completed payments across all staff."
          : "Review payments you have claimed or completed."
      }
    >
      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
        >
          {error}
        </div>
      ) : null}

      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="text-sm font-medium text-slate-600">
          {loading
            ? "Loading history…"
            : `${payments.length}${hasMore ? "+" : ""} payments`}
        </p>
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
          <p className="font-bold text-slate-800">No payment history yet</p>
        </div>
      ) : (
        <section className="grid gap-4">
          {payments.map((payment) => {
            const cardColor =
              payment.status === "done"
                ? "border-emerald-300 bg-emerald-50"
                : payment.status === "in_progress"
                  ? "border-amber-300 bg-amber-50"
                  : "border-slate-200 bg-white";

            return (
              <article
                key={payment.id}
                className={`rounded-2xl border p-5 shadow-sm ${cardColor}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-current px-2.5 py-1 text-xs font-bold">
                        {statusLabels[payment.status]}
                      </span>
                      {user?.role === "admin" && payment.claimed_by_staff ? (
                        <StaffBadge
                          username={payment.claimed_by_staff.username}
                          color={payment.claimed_by_staff.color}
                          label="Claimed by"
                        />
                      ) : null}
                      {user?.role === "admin" && payment.completed_by_staff ? (
                        <StaffBadge
                          username={payment.completed_by_staff.username}
                          color={payment.completed_by_staff.color}
                          label="Completed by"
                        />
                      ) : null}
                    </div>
                    <div className="mt-4 flex flex-wrap items-end gap-4">
                      <strong className="text-2xl font-black text-slate-950">
                        {formatMoney(payment.amount)}
                      </strong>
                      <span className="text-sm text-slate-600">
                        {payment.payment_sender_name} → {payment.recipient_tag}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-3">
                    <span className="text-sm text-slate-500">
                      Payment: {formatDate(payment.payment_datetime)}
                    </span>
                    {user?.role === "admin" && payment.status === "done" ? (
                      <button
                        type="button"
                        disabled={actionId === payment.id}
                        onClick={() => void reopen(payment.id)}
                        className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm font-bold text-amber-800 disabled:opacity-50"
                      >
                        {actionId === payment.id
                          ? "Reopening…"
                          : "Put Back Pending"}
                      </button>
                    ) : null}
                  </div>
                </div>

                <div className="mt-4 grid gap-2 border-t border-slate-200 pt-4 text-xs text-slate-600 sm:grid-cols-2 lg:grid-cols-4">
                  <span>Claimed: {formatDate(payment.claimed_at)}</span>
                  <span>Completed: {formatDate(payment.completed_at)}</span>
                  <span>Recorded: {formatDate(payment.created_at)}</span>
                  <span>Updated: {formatDate(payment.updated_at)}</span>
                </div>
              </article>
            );
          })}

          {hasMore ? (
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
              className="mx-auto rounded-lg border border-indigo-300 bg-white px-5 py-2.5 text-sm font-bold text-indigo-700 disabled:opacity-50"
            >
              {loadingMore ? "Loading more…" : "Load more"}
            </button>
          ) : null}
        </section>
      )}
    </AppShell>
  );
}
