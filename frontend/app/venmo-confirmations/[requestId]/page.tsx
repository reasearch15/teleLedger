"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { environment } from "@/lib/env";
import { VENMO_CONFIRMATION_EVENTS } from "@/lib/live-events";
import {
  confirmVenmoAttempt,
  dismissVenmoInquiry,
  getVenmoConfirmation,
  markVenmoAttemptNotReceived,
  resendVenmoConfirmation,
  uploadVenmoPaymentScreenshot,
} from "@/services/venmo-confirmations";
import type {
  VenmoConfirmationAttempt,
  VenmoConfirmationDetail,
  VenmoConfirmationInquiry,
} from "@/types/api";

const statusLabels: Record<string, string> = {
  pending: "Pending",
  posted: "Posted",
  confirmed: "Confirmed",
  not_received: "Not received",
  failed_to_send: "Failed to send",
  cancelled: "Cancelled",
  open: "Open",
  dismissed: "Dismissed",
  resent: "Resent",
};

const statusPanelClasses: Record<string, string> = {
  confirmed: "border-emerald-300 bg-emerald-50",
  not_received: "border-amber-300 bg-amber-50",
  pending: "border-slate-200 bg-white",
  cancelled: "border-slate-200 bg-slate-50",
};

const statusTextClasses: Record<string, string> = {
  confirmed: "text-emerald-900",
  not_received: "text-amber-900",
  pending: "text-slate-950",
  cancelled: "text-slate-700",
};

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function VenmoConfirmationDetailPage() {
  const params = useParams<{ requestId: string }>();
  const { user } = useAuth();
  const requestId = Number(params.requestId);
  const [detail, setDetail] = useState<VenmoConfirmationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionKey, setActionKey] = useState<string | null>(null);
  const [selectedScreenshot, setSelectedScreenshot] = useState<File | null>(null);
  const [screenshotPreviewUrl, setScreenshotPreviewUrl] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState("");

  const refresh = useCallback(async () => {
    if (!Number.isFinite(requestId)) return;
    setLoading(true);
    setError("");
    try {
      setDetail(await getVenmoConfirmation(requestId));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);
  useLiveUpdates(VENMO_CONFIRMATION_EVENTS, refresh, Number.isFinite(requestId));

  const mediaUrl = useMemo(() => {
    if (!detail?.media) return null;
    return `${environment.apiUrl}${detail.media.preview_url}`;
  }, [detail?.media]);
  const canMutate = user?.role !== "admin";

  useEffect(() => {
    return () => {
      if (screenshotPreviewUrl) URL.revokeObjectURL(screenshotPreviewUrl);
    };
  }, [screenshotPreviewUrl]);

  const runAction = async (
    key: string,
    action: () => Promise<VenmoConfirmationDetail>,
  ) => {
    if (actionKey) return;
    setActionKey(key);
    setError("");
    try {
      setDetail(await action());
    } catch (actionError) {
      setError(friendlyError(actionError));
    } finally {
      setActionKey(null);
    }
  };

  const chooseScreenshot = (file: File | undefined) => {
    setUploadMessage("");
    if (!file) {
      clearSelectedScreenshot();
      return;
    }
    if (!file.type.startsWith("image/")) {
      clearSelectedScreenshot();
      setUploadMessage("Choose a JPEG, PNG, or WEBP image.");
      return;
    }
    setSelectedScreenshot(file);
    const objectUrl = URL.createObjectURL(file);
    setScreenshotPreviewUrl((previousUrl) => {
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      return objectUrl;
    });
  };

  const clearSelectedScreenshot = () => {
    setSelectedScreenshot(null);
    setScreenshotPreviewUrl((previousUrl) => {
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      return null;
    });
  };

  const uploadScreenshot = async () => {
    if (!selectedScreenshot || actionKey) return;
    setActionKey("upload-screenshot");
    setError("");
    setUploadMessage("");
    try {
      setDetail(await uploadVenmoPaymentScreenshot(requestId, selectedScreenshot));
      clearSelectedScreenshot();
      setUploadMessage("Payment screenshot uploaded.");
    } catch (uploadError) {
      setUploadMessage(friendlyError(uploadError));
    } finally {
      setActionKey(null);
    }
  };

  return (
    <AppShell
      title={detail ? `Venmo Request #${detail.id}` : "Venmo Request"}
      description="Confirmation status, media evidence, attempts, inquiries, and event history."
    >
      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm font-semibold text-red-700"
        >
          {error}
        </div>
      ) : null}
      {loading && !detail ? (
        <div className="rounded-lg border border-slate-200 bg-white p-8 text-center text-sm text-slate-600">
          Loading request...
        </div>
      ) : null}
      {detail ? (
        <div className="grid gap-6">
          <section
            className={`rounded-lg border p-5 shadow-sm ${
              statusPanelClasses[detail.status] ?? statusPanelClasses.pending
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs font-bold uppercase tracking-[0.16em] text-slate-500">
                  Current status
                </p>
                <h2
                  className={`mt-1 text-2xl font-black ${
                    statusTextClasses[detail.status] ?? statusTextClasses.pending
                  }`}
                >
                  {detail.status === "confirmed" ? "✅ " : ""}
                  {statusLabels[detail.status]}
                </h2>
                {detail.status === "confirmed" ? (
                  <p className="mt-2 inline-flex rounded-full bg-emerald-600 px-3 py-1 text-xs font-black uppercase tracking-[0.12em] text-white">
                    ✓ Confirmed
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={loading}
                className="text-sm font-bold text-indigo-600 disabled:opacity-50"
              >
                Refresh
              </button>
            </div>
            <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
              <Info label="Staff" value={detail.requested_by_username ?? "Unknown"} />
              <Info
                label="Coadmin"
                value={detail.coadmin_username ?? String(detail.coadmin_id)}
              />
              <Info label="Created" value={formatDate(detail.created_at)} />
              <Info label="Confirmed" value={formatDate(detail.confirmed_at)} />
              <Info
                label="Confirmed by"
                value={detail.confirmed_by_display_name ?? "-"}
              />
              <Info label="Payment note" value={detail.payment_note ?? "-"} />
            </dl>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-black text-slate-950">Payment Screenshot</h2>
              {canMutate ? (
                <label className="rounded-lg border border-indigo-300 px-3 py-2 text-sm font-bold text-indigo-700">
                  Choose image
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="sr-only"
                    onChange={(event) => chooseScreenshot(event.currentTarget.files?.[0])}
                  />
                </label>
              ) : null}
            </div>
            {detail.media && mediaUrl ? (
              <div className="mt-4 grid gap-4 md:grid-cols-[16rem_1fr]">
                {detail.media.mime_type.startsWith("image/") ? (
                  // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
                  <img
                    src={mediaUrl}
                    alt={detail.media.original_filename ?? "Venmo evidence"}
                    className="aspect-video w-full rounded-lg border border-slate-200 object-cover"
                  />
                ) : (
                  <div className="grid aspect-video place-items-center rounded-lg border border-slate-200 bg-slate-50 text-sm font-semibold text-slate-500">
                    Preview unavailable
                  </div>
                )}
                <div className="text-sm text-slate-700">
                  <p className="font-bold text-slate-950">
                    {detail.media.original_filename ?? "Uploaded evidence"}
                  </p>
                  <p className="mt-1">{detail.media.mime_type}</p>
                  <p className="mt-1">{formatBytes(detail.media.size_bytes)}</p>
                  <p className="mt-1">Uploaded {formatDate(detail.media.created_at)}</p>
                  <a
                    href={mediaUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 inline-block rounded-lg border border-indigo-300 px-3 py-2 text-sm font-bold text-indigo-700"
                  >
                    Open image
                  </a>
                </div>
              </div>
            ) : (
              <p className="mt-4 text-sm text-slate-600">No media available.</p>
            )}
            {canMutate && selectedScreenshot ? (
              <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
                  {screenshotPreviewUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element -- local upload preview
                    <img
                      src={screenshotPreviewUrl}
                      alt="Selected payment screenshot"
                      className="aspect-video w-full rounded-lg border border-slate-200 object-cover"
                    />
                  ) : null}
                  <div className="text-sm text-slate-700">
                    <p className="font-bold text-slate-950">{selectedScreenshot.name}</p>
                    <p className="mt-1">{selectedScreenshot.type}</p>
                    <p className="mt-1">{formatBytes(selectedScreenshot.size)}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void uploadScreenshot()}
                        disabled={actionKey === "upload-screenshot"}
                        className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-bold text-white disabled:opacity-50"
                      >
                        {actionKey === "upload-screenshot"
                          ? "Uploading..."
                          : detail.media
                            ? "Replace screenshot"
                            : "Upload screenshot"}
                      </button>
                      <button
                        type="button"
                        onClick={clearSelectedScreenshot}
                        disabled={actionKey === "upload-screenshot"}
                        className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold text-slate-700 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
            {uploadMessage ? (
              <p
                role="status"
                className="mt-3 text-sm font-semibold text-slate-700"
              >
                {uploadMessage}
              </p>
            ) : null}
          </section>

          <AttemptsTable
            attempts={detail.attempts}
            busyKey={actionKey}
            canMutate={canMutate}
            onConfirm={(attempt) =>
              runAction(`confirm-${attempt.id}`, () => confirmVenmoAttempt(attempt.id))
            }
            onNotReceived={(attempt) =>
              runAction(`not-received-${attempt.id}`, () =>
                markVenmoAttemptNotReceived(attempt.id),
              )
            }
          />

          <InquiriesTable
            inquiries={detail.inquiries}
            busyKey={actionKey}
            canMutate={canMutate}
            onDismiss={(inquiry) =>
              runAction(`dismiss-${inquiry.id}`, () => dismissVenmoInquiry(inquiry.id))
            }
            onResend={() =>
              runAction(`resend-${detail.id}`, () => resendVenmoConfirmation(detail.id))
            }
          />

          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-black text-slate-950">Event History</h2>
            <ol className="mt-4 space-y-3 text-sm">
              {detail.events.map((event) => (
                <li key={event.id} className="rounded-lg border border-slate-100 p-3">
                  <p className="font-bold text-slate-950">
                    {event.event_type.replaceAll("_", " ")}
                  </p>
                  <p className="text-xs text-slate-500">
                    {formatDate(event.created_at)} ·{" "}
                    {event.actor_username ?? event.actor_source ?? "system"}
                  </p>
                </li>
              ))}
            </ol>
          </section>
        </div>
      ) : null}
    </AppShell>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-bold uppercase tracking-[0.14em] text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

function AttemptsTable({
  attempts,
  busyKey,
  canMutate,
  onConfirm,
  onNotReceived,
}: {
  attempts: VenmoConfirmationAttempt[];
  busyKey: string | null;
  canMutate: boolean;
  onConfirm: (attempt: VenmoConfirmationAttempt) => void;
  onNotReceived: (attempt: VenmoConfirmationAttempt) => void;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-black text-slate-950">Telegram Attempts</h2>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Attempt</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2">Posted</th>
              <th className="px-3 py-2">Resolved</th>
              <th className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {attempts.map((attempt) => {
              const terminal =
                attempt.status === "confirmed" || attempt.status === "not_received";
              return (
                <tr key={attempt.id}>
                  <td className="px-3 py-2">#{attempt.attempt_number}</td>
                  <td className="px-3 py-2">{statusLabels[attempt.status]}</td>
                  <td className="px-3 py-2">{formatDate(attempt.created_at)}</td>
                  <td className="px-3 py-2">{formatDate(attempt.posted_at)}</td>
                  <td className="px-3 py-2">{formatDate(attempt.resolved_at)}</td>
                  <td className="px-3 py-2">
                    {canMutate ? (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={terminal || Boolean(busyKey)}
                          onClick={() => onConfirm(attempt)}
                          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          disabled={terminal || Boolean(busyKey)}
                          onClick={() => onNotReceived(attempt)}
                          className="rounded-lg border border-amber-300 px-3 py-1.5 text-xs font-bold text-amber-800 disabled:opacity-50"
                        >
                          Not received
                        </button>
                      </div>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function InquiriesTable({
  inquiries,
  busyKey,
  canMutate,
  onDismiss,
  onResend,
}: {
  inquiries: VenmoConfirmationInquiry[];
  busyKey: string | null;
  canMutate: boolean;
  onDismiss: (inquiry: VenmoConfirmationInquiry) => void;
  onResend: () => void;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-black text-slate-950">Not-Received Inquiries</h2>
        <button
          type="button"
          disabled={!canMutate || Boolean(busyKey)}
          onClick={onResend}
          className="rounded-lg border border-indigo-300 px-3 py-2 text-sm font-bold text-indigo-700 disabled:opacity-50"
        >
          Request resend
        </button>
      </div>
      <div className="mt-4 grid gap-3">
        {inquiries.length === 0 ? (
          <p className="text-sm text-slate-600">No inquiries.</p>
        ) : null}
        {inquiries.map((inquiry) => (
          <div key={inquiry.id} className="rounded-lg border border-slate-100 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-bold text-slate-950">
                  Inquiry #{inquiry.id} · {statusLabels[inquiry.status]}
                </p>
                <p className="text-xs text-slate-500">
                  Created {formatDate(inquiry.created_at)}
                </p>
              </div>
              <button
                type="button"
                disabled={
                  !canMutate || inquiry.status !== "open" || Boolean(busyKey)
                }
                onClick={() => onDismiss(inquiry)}
                className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold text-slate-700 disabled:opacity-50"
              >
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
