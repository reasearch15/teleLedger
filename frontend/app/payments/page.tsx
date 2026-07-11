"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import {
  LIVE_EVENTS,
  PAYMENT_PAGE_EVENTS,
  STAFF_PAGE_EVENTS,
} from "@/lib/live-events";
import { usePaymentNotificationSound } from "@/lib/payment-notifications";
import {
  assignPayment,
  claimPayment,
  dismissDeclinedPaymentReview,
  dismissPaymentNotOurs,
  forceUnclaimPayment,
  listPaymentAudit,
  listPayments,
  markPaymentDone,
  PAYMENT_PAGE_SIZE,
  reopenPayment,
} from "@/services/payments";
import { listStaff } from "@/services/staff";
import type {
  Payment,
  PaymentAudit,
  PaymentFilters,
  PaymentStatus,
  User,
} from "@/types/api";

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

export default function PaymentsPage() {
  const { user } = useAuth();
  const userId = user?.id;
  const [payments, setPayments] = useState<Payment[]>([]);
  const [staff, setStaff] = useState<User[]>([]);
  const [filters, setFilters] = useState<PaymentFilters>({});
  const [draftFilters, setDraftFilters] = useState<PaymentFilters>({});
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [assignmentByPayment, setAssignmentByPayment] = useState<
    Record<number, number>
  >({});
  const [auditByPayment, setAuditByPayment] = useState<
    Record<number, PaymentAudit[]>
  >({});
  const [auditLoadingId, setAuditLoadingId] = useState<number | null>(null);
  const requestVersion = useRef(0);
  const knownPaymentIds = useRef<Set<number>>(new Set());
  const initialSyncComplete = useRef(false);
  const {
    acknowledgePayment,
    notifyNewPayment,
    notifyPaymentClaimed,
    notifyPaymentCompleted,
    notifyPaymentError,
    setVisiblePendingPayments,
  } = usePaymentNotificationSound();

  const visiblePayments = useMemo(() => {
    if (user?.role !== "staff") return payments;

    return payments.filter((payment) => {
      if (payment.status === "pending") {
        return true;
      }
      if (payment.status === "in_progress") {
        return payment.claimed_by_staff_id === userId;
      }
      return false;
    });
  }, [payments, user?.role, userId]);

  const applyPage = useCallback(
    (
      page: Awaited<ReturnType<typeof listPayments>>,
      options: { notifyForNewPayments?: boolean } = {},
    ) => {
      const incomingIds = page.items.map((payment) => payment.id);
      const hasNewPayment =
        initialSyncComplete.current &&
        options.notifyForNewPayments === true &&
        incomingIds.some((paymentId) => !knownPaymentIds.current.has(paymentId));

      for (const paymentId of incomingIds) {
        knownPaymentIds.current.add(paymentId);
      }

      setPayments(page.items);
      setTotal(page.total);
      setHasMore(page.has_more);

      if (!initialSyncComplete.current) {
        initialSyncComplete.current = true;
        return;
      }

      if (hasNewPayment) {
        notifyNewPayment();
      }
    },
    [notifyNewPayment],
  );

  const loadFirstPage = useCallback(
    async (options: { notifyForNewPayments?: boolean } = {}) => {
      if (!userId) return;
      const requestId = ++requestVersion.current;
      setLoading(true);
      setError("");
      try {
        const page = await listPayments(
          { ...filters, activeOnly: true },
          {
            limit: PAYMENT_PAGE_SIZE,
            offset: 0,
          },
        );
        if (requestId === requestVersion.current) applyPage(page, options);
      } catch (loadError) {
        if (requestId === requestVersion.current) {
          setError(friendlyError(loadError));
        }
      } finally {
        if (requestId === requestVersion.current) setLoading(false);
      }
    },
    [applyPage, filters, userId],
  );

  const refreshPayments = useCallback(
    (options: { notifyForNewPayments?: boolean } = {}) => {
      void loadFirstPage(options);
    },
    [loadFirstPage],
  );

  useEffect(() => {
    if (!userId) return;
    const requestId = ++requestVersion.current;
    listPayments(
      { ...filters, activeOnly: true },
      { limit: PAYMENT_PAGE_SIZE, offset: 0 },
    )
      .then((page) => {
        if (requestId === requestVersion.current) applyPage(page);
      })
      .catch((loadError: unknown) => {
        if (requestId === requestVersion.current) {
          setError(friendlyError(loadError));
        }
      })
      .finally(() => {
        if (requestId === requestVersion.current) setLoading(false);
      });
  }, [applyPage, filters, userId]);

  useEffect(() => {
    if (user?.role !== "admin") return;
    listStaff()
      .then(setStaff)
      .catch((loadError: unknown) => setError(friendlyError(loadError)));
  }, [user?.role]);

  useEffect(() => {
    setVisiblePendingPayments(
      visiblePayments
        .filter((payment) => payment.status === "pending")
        .map((payment) => payment.id),
    );
  }, [setVisiblePendingPayments, visiblePayments]);

  useLiveUpdates(
    PAYMENT_PAGE_EVENTS,
    (events) => {
      refreshPayments({
        notifyForNewPayments: events.some(
          (event) => event.event === LIVE_EVENTS.PAYMENT_CREATED,
        ),
      });
    },
    Boolean(userId),
  );
  useLiveUpdates(
    STAFF_PAGE_EVENTS,
    () => {
      if (user?.role !== "admin") return;
      listStaff()
        .then(setStaff)
        .catch((loadError: unknown) => setError(friendlyError(loadError)));
    },
    user?.role === "admin",
  );

  const loadMore = async () => {
    if (!userId || loadingMore || !hasMore) return;
    const requestId = requestVersion.current;
    setLoadingMore(true);
    try {
      const page = await listPayments(
        { ...filters, activeOnly: true },
        {
          limit: PAYMENT_PAGE_SIZE,
          offset: payments.length,
        },
      );
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

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      draftFilters.dateFrom &&
      draftFilters.dateTo &&
      draftFilters.dateFrom > draftFilters.dateTo
    ) {
      setError("The start date must be before the end date.");
      return;
    }
    requestVersion.current += 1;
    setPayments([]);
    setLoading(true);
    setFilters({ ...draftFilters });
  };

  const runAction = async (
    paymentId: number,
    action: (id: number) => Promise<Payment>,
    notification?: "claimed" | "completed",
  ) => {
    acknowledgePayment(paymentId);
    setActionId(paymentId);
    setError("");
    setMessage("");
    try {
      const updated = await action(paymentId);
      setPayments((current) => {
        if (user?.role === "staff" && updated.status === "done") {
          return current.filter((payment) => payment.id !== paymentId);
        }
        return current.map((payment) =>
          payment.id === paymentId
            ? {
                ...payment,
                ...updated,
                claimed_by_staff:
                  updated.status === "in_progress" &&
                  user?.role === "staff"
                    ? {
                        id: updated.claimed_by_staff_id ?? user.id,
                        username: user.username,
                        color: user.staff_color,
                      }
                    : payment.claimed_by_staff,
              }
            : payment,
        );
      });
      if (notification === "claimed") {
        notifyPaymentClaimed();
      } else if (notification === "completed") {
        notifyPaymentCompleted();
      }
      void loadFirstPage();
    } catch (actionError) {
      setError(friendlyError(actionError));
      notifyPaymentError();
    } finally {
      setActionId(null);
    }
  };

  const acknowledgeVisiblePayment = (payment: Payment) => {
    if (payment.status === "pending") {
      acknowledgePayment(payment.id);
    }
  };

  const ignorePaymentForCurrentStaff = async (paymentId: number) => {
    if (user?.role !== "staff" || !userId) return;
    if (
      !window.confirm("Mark this payment as Not Ours for your coadmin team?")
    ) {
      return;
    }

    acknowledgePayment(paymentId);
    setActionId(paymentId);
    setError("");
    setMessage("");
    try {
      await dismissPaymentNotOurs(paymentId);
      setMessage("Payment dismissed for your coadmin team.");
      await loadFirstPage();
    } catch (dismissError) {
      setError(friendlyError(dismissError));
      notifyPaymentError();
    } finally {
      setActionId(null);
    }
  };

  const dismissFullyDeclinedPayment = async (paymentId: number) => {
    if (user?.role !== "admin") return;
    setActionId(paymentId);
    setError("");
    setMessage("");
    try {
      await dismissDeclinedPaymentReview(paymentId);
      setPayments((current) =>
        current.filter((payment) => payment.id !== paymentId),
      );
      setMessage("Payment removed from admin review.");
      await loadFirstPage();
    } catch (dismissError) {
      setError(friendlyError(dismissError));
      notifyPaymentError();
    } finally {
      setActionId(null);
    }
  };

  const handleAssign = async (paymentId: number) => {
    const staffId = assignmentByPayment[paymentId];
    if (!staffId) {
      setError("Choose a staff member before assigning this payment.");
      return;
    }
    await runAction(paymentId, (id) => assignPayment(id, staffId), "claimed");
  };

  const toggleAudit = async (paymentId: number) => {
    if (auditByPayment[paymentId]) {
      setAuditByPayment((current) => {
        const next = { ...current };
        delete next[paymentId];
        return next;
      });
      return;
    }
    setAuditLoadingId(paymentId);
    try {
      const entries = await listPaymentAudit(paymentId);
      setAuditByPayment((current) => ({
        ...current,
        [paymentId]: entries,
      }));
    } catch (auditError) {
      setError(friendlyError(auditError));
    } finally {
      setAuditLoadingId(null);
    }
  };

  return (
    <AppShell
      title="Payments"
      description={
        user?.role === "admin"
          ? "Monitor every payment, assignment, and workflow event."
          : "Claim pending payments and complete the payments assigned to you."
      }
    >
      <form
        onSubmit={applyFilters}
        className="mb-6 grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-5"
      >
        <input
          value={draftFilters.search ?? ""}
          onChange={(event) =>
            setDraftFilters((current) => ({
              ...current,
              search: event.target.value,
            }))
          }
          placeholder="Recipient, sender, message"
          className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm"
        />
        <select
          aria-label="Status"
          value={draftFilters.status ?? ""}
          onChange={(event) =>
            setDraftFilters((current) => ({
              ...current,
              status: event.target.value as PaymentStatus | "",
            }))
          }
          className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm"
        >
          <option value="">All active statuses</option>
          <option value="pending">Pending</option>
          <option value="in_progress">Claimed</option>
        </select>
        <input
          aria-label="From date"
          type="date"
          value={draftFilters.dateFrom ?? ""}
          onChange={(event) =>
            setDraftFilters((current) => ({
              ...current,
              dateFrom: event.target.value,
            }))
          }
          className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm"
        />
        <input
          aria-label="To date"
          type="date"
          value={draftFilters.dateTo ?? ""}
          onChange={(event) =>
            setDraftFilters((current) => ({
              ...current,
              dateTo: event.target.value,
            }))
          }
          className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm"
        />
        <div className="flex gap-2">
          <button
            type="submit"
            className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white"
          >
            Filter
          </button>
          <button
            type="button"
            onClick={() => {
              setDraftFilters({});
              setFilters({});
            }}
            className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm font-semibold"
          >
            Clear
          </button>
        </div>
      </form>

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
          <h2 className="text-lg font-black text-slate-900">
            Active Payments
          </h2>
        <p className="text-sm font-medium text-slate-600">
          {loading
            ? "Loading payments…"
            : total === null
              ? `${visiblePayments.length}${hasMore ? "+" : ""} payments`
              : `${visiblePayments.length} of ${total} payments`}
        </p>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => void refreshPayments()}
          className="text-sm font-bold text-indigo-600 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {!loading && visiblePayments.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <p className="font-bold text-slate-800">No payments found</p>
        </div>
      ) : (
        <section className="grid gap-4">
          {visiblePayments.map((payment) => {
            const busy = actionId === payment.id;
            const claimedByMe =
              payment.status === "in_progress" &&
              payment.claimed_by_staff_id === userId;
            const cardColor =
              payment.status === "done"
                ? "border-emerald-300 bg-emerald-50"
                : payment.status === "in_progress"
                  ? "border-red-200 bg-red-50"
                  : "border-slate-200 bg-white";

            return (
              <article
                key={payment.id}
                onClick={() => acknowledgeVisiblePayment(payment)}
                className={`overflow-hidden rounded-xl border px-3 py-3 shadow-sm sm:px-4 ${cardColor} ${
                  user?.role === "staff" && claimedByMe
                    ? "ring-2 ring-emerald-400"
                    : ""
                }`}
              >
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
                    <span className="rounded-full border border-current px-2.5 py-1 text-xs font-bold">
                      {statusLabels[payment.status]}
                    </span>
                    <h2 className="text-xl font-black leading-none text-slate-950 sm:text-2xl">
                      {formatMoney(payment.amount)}
                    </h2>
                    <p className="min-w-0 text-sm text-slate-700">
                      <strong className="inline-block max-w-[9rem] truncate align-bottom sm:max-w-[12rem]">
                        {payment.payment_sender_name}
                      </strong>
                      {" → "}
                      <strong className="inline-block max-w-[9rem] truncate align-bottom sm:max-w-[12rem]">
                        {payment.recipient_tag}
                      </strong>
                    </p>
                    <span className="text-xs font-medium text-slate-500 sm:text-sm">
                      {formatDate(payment.payment_datetime)}
                    </span>
                    {payment.claimed_by_staff ? (
                      <StaffBadge
                        username={payment.claimed_by_staff.username}
                        color={payment.claimed_by_staff.color}
                        label="Claimed by"
                      />
                    ) : null}
                    {payment.completed_by_staff ? (
                      <StaffBadge
                        username={payment.completed_by_staff.username}
                        color={payment.completed_by_staff.color}
                        label="Completed by"
                      />
                    ) : null}
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600">
                      <span>Claimed: {formatDate(payment.claimed_at)}</span>
                      <span>Completed: {formatDate(payment.completed_at)}</span>
                    </div>
                    {user?.role === "admin" &&
                    payment.coadmin_dismissals.length > 0 ? (
                      <div className="flex flex-wrap gap-2 text-xs text-amber-800">
                        {payment.coadmin_dismissals.map((dismissal) => (
                          <span
                            key={`${dismissal.coadmin_id}-${dismissal.created_at}`}
                            className="rounded-full bg-amber-50 px-2 py-1 font-semibold"
                          >
                            Not Ours:{" "}
                            {dismissal.coadmin_username ?? "Deleted Coadmin"}
                            {dismissal.dismissed_by_staff_username
                              ? ` by ${dismissal.dismissed_by_staff_username}`
                              : ""}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  <div className="flex flex-wrap items-center justify-start gap-2 lg:justify-end">
                    {user?.role === "staff" && payment.status === "pending" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          void runAction(payment.id, claimPayment, "claimed");
                        }}
                        className="rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-bold text-white disabled:opacity-50"
                      >
                        Claim
                      </button>
                    ) : null}
                    {user?.role === "staff" && payment.status === "pending" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          void ignorePaymentForCurrentStaff(payment.id);
                        }}
                        className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-bold text-slate-700 disabled:opacity-50"
                      >
                        Not Ours
                      </button>
                    ) : null}
                    {user?.role === "staff" && claimedByMe ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          void runAction(
                            payment.id,
                            markPaymentDone,
                            "completed",
                          );
                        }}
                        className="rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-bold text-white disabled:opacity-50"
                      >
                        Done
                      </button>
                    ) : null}
                    {user?.role === "admin" &&
                    payment.status === "in_progress" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void runAction(payment.id, forceUnclaimPayment)
                        }
                        className="rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-bold text-red-700"
                      >
                        Force unclaim
                      </button>
                    ) : null}
                    {user?.role === "admin" && payment.status === "done" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void runAction(payment.id, reopenPayment)}
                        className="rounded-lg border border-emerald-300 bg-white px-3 py-2 text-sm font-bold text-emerald-700"
                      >
                        Reopen
                      </button>
                    ) : null}
                    {user?.role === "admin" && payment.can_dismiss ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          void dismissFullyDeclinedPayment(payment.id);
                        }}
                        className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-bold text-slate-700 disabled:opacity-50"
                      >
                        Dismiss
                      </button>
                    ) : null}
                    {user?.role === "admin" && payment.status !== "done" ? (
                      <div className="flex gap-2">
                        <select
                          aria-label={`Assign payment ${payment.id}`}
                          value={assignmentByPayment[payment.id] ?? ""}
                          onChange={(event) =>
                            setAssignmentByPayment((current) => ({
                              ...current,
                              [payment.id]: Number(event.target.value),
                            }))
                          }
                          className="rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
                        >
                          <option value="">Assign staff</option>
                          {staff
                            .filter((staffUser) => staffUser.is_active)
                            .map((staffUser) => (
                              <option key={staffUser.id} value={staffUser.id}>
                                {staffUser.username}
                              </option>
                            ))}
                        </select>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void handleAssign(payment.id)}
                          className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-bold text-white"
                        >
                          Assign
                        </button>
                      </div>
                    ) : null}
                    {user?.role === "admin" ? (
                      <button
                        type="button"
                        disabled={auditLoadingId === payment.id}
                        onClick={() => void toggleAudit(payment.id)}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold"
                      >
                        {auditByPayment[payment.id]
                          ? "Hide audit"
                          : "Audit history"}
                      </button>
                    ) : null}
                  </div>
                </div>

                {auditByPayment[payment.id] ? (
                  <ol className="mt-4 space-y-2 border-t border-slate-200 pt-4 text-xs">
                    {auditByPayment[payment.id].map((entry) => (
                      <li key={entry.id} className="flex flex-wrap gap-2">
                        <strong className="capitalize">{entry.action}</strong>
                        <span>{formatDate(entry.created_at)}</span>
                        <span>
                          by {entry.actor_username ?? "System"}
                          {entry.subject_username
                            ? ` · staff: ${entry.subject_username}`
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : null}
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
