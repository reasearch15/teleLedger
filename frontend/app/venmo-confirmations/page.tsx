"use client";

import Link from "next/link";
import type { FormEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { VENMO_CONFIRMATION_EVENTS } from "@/lib/live-events";
import {
  createVenmoConfirmation,
  listVenmoConfirmations,
} from "@/services/venmo-confirmations";
import type { VenmoConfirmationSummary } from "@/types/api";

const PAGE_SIZE = 30;

const statusLabels: Record<string, string> = {
  pending: "Pending",
  confirmed: "Confirmed",
  not_received: "Not received",
  cancelled: "Cancelled",
};

const cardClasses: Record<string, string> = {
  confirmed: "border-emerald-300 bg-emerald-50 shadow-sm hover:border-emerald-400",
  not_received: "border-amber-300 bg-amber-50 shadow-sm hover:border-amber-400",
  pending: "border-slate-200 bg-white shadow-sm hover:border-indigo-300",
  cancelled: "border-slate-200 bg-slate-50 shadow-sm hover:border-slate-300",
};

const badgeClasses: Record<string, string> = {
  confirmed: "border-emerald-600 bg-emerald-600 text-white",
  not_received: "border-amber-500 bg-amber-100 text-amber-900",
  pending: "border-slate-300 bg-white text-slate-700",
  cancelled: "border-slate-300 bg-slate-100 text-slate-700",
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
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [loadMoreError, setLoadMoreError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [paymentNote, setPaymentNote] = useState("");
  const [createStatus, setCreateStatus] = useState("");
  const [createError, setCreateError] = useState("");
  const loadingMoreRef = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    setLoadMoreError("");
    try {
      const response = await listVenmoConfirmations({ limit: PAGE_SIZE });
      setItems(response.items);
      setHasMore(response.has_more);
      setNextCursor(response.next_cursor);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMore || !nextCursor) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    setLoadMoreError("");
    try {
      const response = await listVenmoConfirmations({
        limit: PAGE_SIZE,
        cursor: nextCursor,
      });
      setItems((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [
          ...current,
          ...response.items.filter((item) => !seen.has(item.id)),
        ];
      });
      setHasMore(response.has_more);
      setNextCursor(response.next_cursor);
    } catch (loadError) {
      setLoadMoreError(friendlyError(loadError));
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, [hasMore, nextCursor]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);
  useLiveUpdates(VENMO_CONFIRMATION_EVENTS, refresh, true);

  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  async function submitConfirmation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) {
      setCreateError("Choose an evidence image before submitting.");
      return;
    }
    setCreating(true);
    setCreateError("");
    setCreateStatus("");
    try {
      const created = await createVenmoConfirmation(selectedFile, paymentNote);
      setItems((current) => [
        {
          id: created.id,
          coadmin_id: created.coadmin_id,
          requested_by_staff_id: created.requested_by_staff_id,
          requested_by_username: created.requested_by_username,
          coadmin_username: created.coadmin_username,
          screenshot_media_asset_id: created.screenshot_media_asset_id,
          status: created.status,
          payment_note: created.payment_note,
          metadata: created.metadata,
          confirmed_at: created.confirmed_at,
          confirmed_by_display_name: created.confirmed_by_display_name,
          created_at: created.created_at,
          updated_at: created.updated_at,
          media: created.media,
        },
        ...current.filter((item) => item.id !== created.id),
      ]);
      setLoadMoreError("");
      setCreateStatus(`Confirmation request #${created.id} created.`);
      setSelectedFile(null);
      setPreviewUrl("");
      setPaymentNote("");
    } catch (createRequestError) {
      setCreateError(friendlyError(createRequestError));
    } finally {
      setCreating(false);
    }
  }

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
        <div className="flex items-center gap-3">
          <a href="#new-confirmation" className="text-sm font-bold text-indigo-600">
            New Confirmation
          </a>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading || loadingMore}
            className="text-sm font-bold text-indigo-600 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      </div>
      <section
        id="new-confirmation"
        className="mb-5 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-black text-slate-950">New Confirmation</h2>
          </div>
        </div>
        <form className="mt-4 grid gap-4" onSubmit={(event) => void submitConfirmation(event)}>
          <label className="grid gap-2 text-sm font-bold text-slate-700">
            Evidence image
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={creating}
              onChange={(event) => {
                setCreateError("");
                setCreateStatus("");
                const file = event.target.files?.[0] ?? null;
                setSelectedFile(file);
                setPreviewUrl(file ? URL.createObjectURL(file) : "");
              }}
              className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-bold file:text-indigo-700"
            />
          </label>
          {previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewUrl}
              alt="Selected confirmation evidence preview"
              className="max-h-56 w-fit max-w-full rounded-lg border border-slate-200 object-contain"
            />
          ) : null}
          <label className="grid gap-2 text-sm font-bold text-slate-700">
            Note / context
            <textarea
              value={paymentNote}
              disabled={creating}
              onChange={(event) => setPaymentNote(event.target.value)}
              rows={3}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800"
              placeholder="Optional reference, player, amount, or context"
            />
          </label>
          {createError ? (
            <p role="alert" className="text-sm font-bold text-red-700">
              {createError}
            </p>
          ) : null}
          {createStatus ? (
            <p className="text-sm font-bold text-emerald-700">{createStatus}</p>
          ) : null}
          <div>
            <button
              type="submit"
              disabled={creating}
              className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-black text-white disabled:opacity-50"
            >
              {creating ? "Sending..." : "Submit & Send"}
            </button>
          </div>
        </form>
      </section>
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
            className={`rounded-lg border p-4 transition ${
              cardClasses[request.status] ?? cardClasses.pending
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-bold uppercase tracking-[0.16em] text-slate-500">
                  Request #{request.id}
                </p>
                <h2
                  className={`mt-1 text-lg font-black ${
                    request.status === "confirmed" ? "text-emerald-900" : "text-slate-950"
                  }`}
                >
                  {request.status === "confirmed" ? "✅ " : ""}
                  {statusLabels[request.status]}
                </h2>
                <p className="mt-1 text-sm text-slate-600">
                  Staff: {request.requested_by_username ?? "Unknown"} · Coadmin:{" "}
                  {request.coadmin_username ?? request.coadmin_id}
                </p>
              </div>
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-black ${
                  badgeClasses[request.status] ?? badgeClasses.pending
                }`}
              >
                {request.status === "confirmed" ? "✓ " : ""}
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
        {loadMoreError ? (
          <p role="alert" className="text-sm font-bold text-red-700">
            {loadMoreError}
          </p>
        ) : null}
        {hasMore ? (
          <div className="flex justify-center pt-1">
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-black text-slate-800 shadow-sm hover:border-indigo-300 disabled:opacity-50"
            >
              {loadingMore ? "Loading..." : "Load More"}
            </button>
          </div>
        ) : null}
      </section>
    </AppShell>
  );
}
