"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { CASHOUT_PAGE_EVENTS } from "@/lib/live-events";
import { friendlyError } from "@/lib/api-client";
import {
  cancelCashout,
  CASHOUT_PAGE_SIZE,
  completeCashout,
  createCashout,
  listCashoutAudit,
  listCashouts,
  retryCashoutTelegram,
  updateCashoutNotes,
} from "@/services/cashouts";
import type {
  Cashout,
  CashoutAudit,
  CashoutAuditAction,
  CashoutFilters,
  CashoutStatus,
  CashoutTelegramStatus,
} from "@/types/api";

const statusLabels: Record<CashoutStatus, string> = {
  pending: "Pending",
  sent: "Sent",
  completed: "Completed",
  cancelled: "Cancelled",
  failed_to_send: "Failed to send",
};

const statusColors: Record<CashoutStatus, string> = {
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  sent: "border-blue-300 bg-blue-50 text-blue-900",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-900",
  cancelled: "border-red-300 bg-red-50 text-red-900",
  failed_to_send: "border-slate-300 bg-slate-100 text-slate-800",
};

const telegramLabels: Record<CashoutTelegramStatus, string> = {
  pending: "Delivery pending",
  sent: "Delivery sent",
  failed_to_send: "Delivery failed",
};

const auditActionLabels: Record<CashoutAuditAction, string> = {
  created: "Created",
  telegram_sent: "Delivery sent",
  telegram_retry: "Delivery retry",
  telegram_reaction_completed: "Reaction completed",
  completed: "Completed",
  cancelled: "Cancelled",
  edited_notes: "Edited notes",
};

function formatMoney(value: string): string {
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

function providerNeutralText(value: string): string {
  return value.replaceAll(/telegram/gi, "delivery");
}

export default function CashoutPage() {
  const { user } = useAuth();
  const userId = user?.id;
  const [cashouts, setCashouts] = useState<Cashout[]>([]);
  const [filters, setFilters] = useState<CashoutFilters>({});
  const [draftFilters, setDraftFilters] = useState<CashoutFilters>({});
  const [playerTag, setPlayerTag] = useState("");
  const [amount, setAmount] = useState("");
  const [notes, setNotes] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [editingNotesId, setEditingNotesId] = useState<number | null>(null);
  const [notesDraft, setNotesDraft] = useState("");
  const [auditByCashout, setAuditByCashout] = useState<
    Record<number, CashoutAudit[]>
  >({});
  const requestVersion = useRef(0);
  const submittingRef = useRef(false);
  const idempotencyKey = useRef("");

  const loadFirstPage = useCallback(async () => {
    if (!userId) return;
    const requestId = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const page = await listCashouts(filters, {
        limit: CASHOUT_PAGE_SIZE,
        offset: 0,
      });
      if (requestId !== requestVersion.current) return;
      setCashouts(page.items);
      setHasMore(page.has_more);
    } catch (loadError) {
      if (requestId === requestVersion.current) {
        setError(providerNeutralText(friendlyError(loadError)));
      }
    } finally {
      if (requestId === requestVersion.current) setLoading(false);
    }
  }, [filters, userId]);

  useEffect(() => {
    if (!userId) return;
    const timeoutId = window.setTimeout(() => {
      void loadFirstPage();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadFirstPage, userId]);

  useLiveUpdates(CASHOUT_PAGE_EVENTS, loadFirstPage, Boolean(userId));

  const submitCashout = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;

    const trimmedTag = playerTag.trim();
    const numericAmount = Number(amount);
    if (!trimmedTag) {
      setError("Player Tag is required.");
      return;
    }
    if (!Number.isFinite(numericAmount) || numericAmount <= 0) {
      setError("Amount must be greater than zero.");
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    setSuccess("");
    if (!idempotencyKey.current) {
      idempotencyKey.current = crypto.randomUUID();
    }
    try {
      const created = await createCashout({
        playerTag: trimmedTag,
        amount,
        notes: notes.trim(),
        idempotencyKey: idempotencyKey.current,
      });
      setCashouts((current) => [
        created,
        ...current.filter((cashout) => cashout.id !== created.id),
      ]);
      setPlayerTag("");
      setAmount("");
      setNotes("");
      idempotencyKey.current = "";
      setSuccess(`${created.request_number} was created and queued for delivery.`);
    } catch (submitError) {
      setError(providerNeutralText(friendlyError(submitError)));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const loadMore = async () => {
    if (!userId || loadingMore || !hasMore) return;
    const requestId = requestVersion.current;
    setLoadingMore(true);
    try {
      const page = await listCashouts(filters, {
        limit: CASHOUT_PAGE_SIZE,
        offset: cashouts.length,
      });
      if (requestId !== requestVersion.current) return;
      setCashouts((current) => [...current, ...page.items]);
      setHasMore(page.has_more);
    } catch (loadError) {
      setError(providerNeutralText(friendlyError(loadError)));
    } finally {
      setLoadingMore(false);
    }
  };

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    requestVersion.current += 1;
    setCashouts([]);
    setFilters({ ...draftFilters });
  };

  const runAction = async (
    cashoutId: number,
    action: (id: number) => Promise<Cashout>,
  ) => {
    setActionId(cashoutId);
    setError("");
    try {
      const updated = await action(cashoutId);
      setCashouts((current) =>
        current.map((cashout) =>
          cashout.id === cashoutId
            ? {
                ...cashout,
                ...updated,
                requested_by: updated.requested_by ?? cashout.requested_by,
                completed_by: updated.completed_by ?? cashout.completed_by,
              }
            : cashout,
        ),
      );
    } catch (actionError) {
      setError(providerNeutralText(friendlyError(actionError)));
    } finally {
      setActionId(null);
    }
  };

  const saveNotes = async (cashoutId: number) => {
    await runAction(cashoutId, (id) => updateCashoutNotes(id, notesDraft));
    setEditingNotesId(null);
  };

  const toggleAudit = async (cashoutId: number) => {
    if (auditByCashout[cashoutId]) {
      setAuditByCashout((current) => {
        const next = { ...current };
        delete next[cashoutId];
        return next;
      });
      return;
    }
    setActionId(cashoutId);
    try {
      const entries = await listCashoutAudit(cashoutId);
      setAuditByCashout((current) => ({ ...current, [cashoutId]: entries }));
    } catch (auditError) {
      setError(providerNeutralText(friendlyError(auditError)));
    } finally {
      setActionId(null);
    }
  };

  return (
    <AppShell
      title="Cashout"
      description={
        user?.role === "admin"
          ? "Review and administer cashout requests across all staff."
          : "Create cashout requests and follow your delivery history."
      }
    >
      {user?.role === "staff" ? (
        <form
          onSubmit={submitCashout}
          className="mb-8 grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
        >
          <div>
            <h2 className="text-lg font-black text-slate-950">
              New Cashout Request
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Your request is saved before delivery is attempted.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="grid gap-1.5 text-sm font-semibold text-slate-700">
              Player Tag
              <input
                required
                maxLength={128}
                value={playerTag}
                onChange={(event) => setPlayerTag(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2.5"
                placeholder="ABC12345"
              />
            </label>
            <label className="grid gap-1.5 text-sm font-semibold text-slate-700">
              Amount
              <input
                required
                min="0.01"
                step="0.01"
                type="number"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2.5"
                placeholder="250.00"
              />
            </label>
          </div>
          <label className="grid gap-1.5 text-sm font-semibold text-slate-700">
            Optional Notes
            <textarea
              maxLength={2000}
              rows={3}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2.5"
              placeholder="VIP Player"
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="w-fit rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Submitting…" : "Submit Cashout"}
          </button>
        </form>
      ) : null}

      {user?.role === "admin" ? (
        <form
          onSubmit={applyFilters}
          className="mb-6 grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-4"
        >
          <input
            value={draftFilters.search ?? ""}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                search: event.target.value,
              }))
            }
            placeholder="Request, tag, or notes"
            className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm"
          />
          <select
            aria-label="Cashout status"
            value={draftFilters.status ?? ""}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                status: event.target.value as CashoutStatus | "",
              }))
            }
            className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm"
          >
            <option value="">All statuses</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Delivery status"
            value={draftFilters.telegramStatus ?? ""}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                telegramStatus: event.target
                  .value as CashoutTelegramStatus | "",
              }))
            }
            className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm"
          >
            <option value="">All delivery statuses</option>
            <option value="pending">Pending</option>
            <option value="sent">Sent</option>
            <option value="failed_to_send">Failed</option>
          </select>
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
      ) : null}

      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
        >
          {error}
        </div>
      ) : null}
      {success ? (
        <div
          role="status"
          className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
        >
          {success}
        </div>
      ) : null}

      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-slate-950">
            {user?.role === "admin" ? "All Cashout Requests" : "Cashout History"}
          </h2>
          <p className="text-sm text-slate-600">
            {loading
              ? "Loading cashouts…"
              : `${cashouts.length}${hasMore ? "+" : ""} requests`}
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

      {!loading && cashouts.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <p className="font-bold text-slate-800">No cashout requests found</p>
        </div>
      ) : (
        <section className="grid gap-4">
          {cashouts.map((cashout) => {
            const busy = actionId === cashout.id;
            const immutable =
              cashout.status === "completed" || cashout.status === "cancelled";

            return (
              <article
                key={cashout.id}
                className={`rounded-2xl border p-5 shadow-sm ${statusColors[cashout.status]}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-current px-2.5 py-1 text-xs font-black">
                        {statusLabels[cashout.status]}
                      </span>
                      <span className="rounded-full border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700">
                        {telegramLabels[cashout.telegram_status]}
                      </span>
                      {user?.role === "admin" && cashout.requested_by ? (
                        <span className="rounded-full border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700">
                          Requested by: {cashout.requested_by.username}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-4 text-xs font-bold uppercase tracking-wider opacity-70">
                      {cashout.request_number}
                    </p>
                    <div className="mt-1 flex flex-wrap items-baseline gap-3">
                      <strong className="text-2xl font-black">
                        {formatMoney(cashout.amount)}
                      </strong>
                      <span className="font-bold">{cashout.player_tag}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {!immutable || user?.role === "admin" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setEditingNotesId(cashout.id);
                          setNotesDraft(cashout.notes ?? "");
                        }}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
                      >
                        Edit notes
                      </button>
                    ) : null}
                    {user?.role === "admin" && !immutable ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(cashout.id, completeCashout)
                          }
                          className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-bold text-white disabled:opacity-50"
                        >
                          Mark Completed
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runAction(cashout.id, cancelCashout)}
                          className="rounded-lg bg-red-600 px-3 py-2 text-sm font-bold text-white disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      </>
                    ) : null}
                    {user?.role === "admin" &&
                    cashout.telegram_status !== "sent" &&
                    cashout.status !== "cancelled" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void runAction(cashout.id, retryCashoutTelegram)
                        }
                        className="rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm font-bold text-blue-700 disabled:opacity-50"
                      >
                        Retry delivery
                      </button>
                    ) : null}
                    {user?.role === "admin" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void toggleAudit(cashout.id)}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
                      >
                        {auditByCashout[cashout.id] ? "Hide audit" : "View audit"}
                      </button>
                    ) : null}
                  </div>
                </div>

                {editingNotesId === cashout.id ? (
                  <div className="mt-4 flex flex-col gap-2 border-t border-current/20 pt-4 sm:flex-row">
                    <textarea
                      aria-label={`Notes for ${cashout.request_number}`}
                      maxLength={2000}
                      rows={2}
                      value={notesDraft}
                      onChange={(event) => setNotesDraft(event.target.value)}
                      className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800"
                    />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void saveNotes(cashout.id)}
                      className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
                    >
                      Save notes
                    </button>
                  </div>
                ) : cashout.notes ? (
                  <p className="mt-4 border-t border-current/20 pt-4 text-sm">
                    <strong>Notes:</strong> {cashout.notes}
                  </p>
                ) : null}

                <div className="mt-4 grid gap-2 border-t border-current/20 pt-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
                  <span>Created: {formatDate(cashout.created_at)}</span>
                  <span>Delivery sent: {formatDate(cashout.telegram_sent_at)}</span>
                  <span>Completed: {formatDate(cashout.completed_at)}</span>
                  <span>Cancelled: {formatDate(cashout.cancelled_at)}</span>
                </div>

                {cashout.telegram_last_error ? (
                  <p className="mt-3 text-xs">
                    <strong>Last delivery error:</strong>{" "}
                    {providerNeutralText(cashout.telegram_last_error)}
                  </p>
                ) : null}

                {auditByCashout[cashout.id] ? (
                  <ol className="mt-4 space-y-2 border-t border-current/20 pt-4 text-xs">
                    {auditByCashout[cashout.id].map((entry) => (
                      <li key={entry.id} className="flex flex-wrap gap-2">
                        <strong className="capitalize">
                          {auditActionLabels[entry.action]}
                        </strong>
                        <span>{formatDate(entry.created_at)}</span>
                        <span>by {entry.actor_username ?? "System"}</span>
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
              className="mx-auto rounded-lg border border-indigo-300 bg-white px-5 py-2.5 text-sm font-bold text-indigo-700 disabled:opacity-50"
            >
              {loadingMore ? "Loading more…" : "Load More"}
            </button>
          ) : null}
        </section>
      )}
    </AppShell>
  );
}
