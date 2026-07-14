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
import { INQUIRY_PAGE_EVENTS } from "@/lib/live-events";
import {
  fetchInquiryMediaBlob,
  INQUIRY_PAGE_SIZE,
  listInquiryMessages,
  sendInquiryMessage,
} from "@/services/inquiries";
import type { InquiryMessage } from "@/types/api";

type SenderBlock = {
  key: string;
  senderName: string;
  senderUsername: string | null;
  isOutbound: boolean;
  sentByUsername: string | null;
  latestAt: string;
  messages: InquiryMessage[];
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function displaySender(message: InquiryMessage): {
  name: string;
  username: string | null;
  isOutbound: boolean;
  sentByUsername: string | null;
} {
  if (message.message_source === "inquiry") {
    return {
      name: "TeleLedger",
      username: null,
      isOutbound: true,
      sentByUsername: message.sent_by_username,
    };
  }
  return {
    name: message.sender_display_name ?? "Unknown sender",
    username: message.sender_username,
    isOutbound: message.direction === "outbound",
    sentByUsername: null,
  };
}

function buildSenderBlocks(messages: InquiryMessage[]): SenderBlock[] {
  const blocks: SenderBlock[] = [];
  for (const message of messages) {
    const sender = displaySender(message);
    const previous = blocks[blocks.length - 1];
    if (
      message.starts_new_sender_block ||
      previous === undefined ||
      previous.key !==
        `${sender.isOutbound ? "outbound" : "inbound"}:${sender.name}:${sender.username ?? ""}`
    ) {
      blocks.push({
        key: `${message.id}`,
        senderName: sender.name,
        senderUsername: sender.username,
        isOutbound: sender.isOutbound,
        sentByUsername: sender.sentByUsername,
        latestAt: message.message_date,
        messages: [message],
      });
      continue;
    }
    previous.messages.push(message);
    previous.latestAt = message.message_date;
  }
  return blocks;
}

function InquiryMediaPreview({ message }: { message: InquiryMessage }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    if (message.media_download_status !== "ready") {
      setSrc(null);
      setFailed(message.media_download_status === "failed");
      return () => undefined;
    }
    fetchInquiryMediaBlob(message.id)
      .then((url) => {
        if (!active) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setSrc(url);
        setFailed(false);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [message.id, message.media_download_status]);

  if (failed || message.media_download_status === "failed") {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-6 text-center text-xs font-semibold text-slate-500">
        Image unavailable
        {message.media_error ? (
          <p className="mt-2 text-[11px] font-normal text-slate-400">
            {message.media_error}
          </p>
        ) : null}
      </div>
    );
  }
  if (!src) {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-100 px-3 py-6 text-center text-xs font-semibold text-slate-500">
        Loading image…
      </div>
    );
  }
  return (
    <a href={src} target="_blank" rel="noreferrer" className="block max-w-full">
      <img
        src={src}
        alt={message.caption ?? message.media_filename ?? "Inquiry attachment"}
        className="max-h-72 w-full max-w-full rounded-xl border border-slate-200 object-contain"
      />
    </a>
  );
}

export default function InquiryPage() {
  const { user } = useAuth();
  const [messages, setMessages] = useState<InquiryMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sendKey, setSendKey] = useState(() => crypto.randomUUID());
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const requestVersion = useRef(0);

  const blocks = useMemo(() => buildSenderBlocks(messages), [messages]);

  const loadLatest = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    const version = ++requestVersion.current;
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    setError("");
    try {
      const page = await listInquiryMessages({ limit: INQUIRY_PAGE_SIZE });
      if (version !== requestVersion.current) return;
      setMessages(page.items);
      setHasMore(page.pagination.hasMore);
      setNextCursor(page.pagination.nextCursor);
    } catch (loadError) {
      if (version === requestVersion.current) {
        setError(friendlyError(loadError));
      }
    } finally {
      if (version === requestVersion.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void loadLatest("initial");
  }, [loadLatest, user]);

  useEffect(() => {
    if (!scrollRef.current || loading) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [loading, messages.length]);

  useLiveUpdates(INQUIRY_PAGE_EVENTS, () => void loadLatest("refresh"), Boolean(user?.id));

  const loadOlder = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError("");
    try {
      const page = await listInquiryMessages({
        limit: INQUIRY_PAGE_SIZE,
        cursor: nextCursor,
      });
      setMessages((current) => {
        const existing = new Set(current.map((item) => item.id));
        const older = page.items.filter((item) => !existing.has(item.id));
        return [...older, ...current];
      });
      setHasMore(page.pagination.hasMore);
      setNextCursor(page.pagination.nextCursor);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoadingMore(false);
    }
  };

  const handleSend = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (sending) return;
    const text = draft.trim();
    if (!text && !attachment) {
      setError("Enter a message or attach an image.");
      return;
    }
    setSending(true);
    setError("");
    try {
      await sendInquiryMessage({
        text: text || undefined,
        image: attachment,
        idempotencyKey: sendKey,
      });
      setDraft("");
      setAttachment(null);
      setSendKey(crypto.randomUUID());
      await loadLatest("refresh");
    } catch (sendError) {
      setError(friendlyError(sendError));
    } finally {
      setSending(false);
    }
  };

  return (
    <AppShell
      title="Inquiry"
      description="Chat with the cashout Telegram group. Workflow messages sent through the Cashout panel stay hidden here."
    >
      <section className="flex min-h-[70vh] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-sm font-bold text-slate-900">Cashout group chat</p>
            <p className="text-xs text-slate-500">
              {refreshing ? "Refreshing…" : "Live updates enabled"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadLatest("refresh")}
            disabled={refreshing || loading}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        <div
          ref={scrollRef}
          className="flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 py-4 sm:px-5"
        >
          {hasMore ? (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={() => void loadOlder()}
                disabled={loadingMore}
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
              >
                {loadingMore ? "Loading older messages…" : "Load older messages"}
              </button>
            </div>
          ) : null}

          {loading ? (
            <p className="py-10 text-center text-sm font-semibold text-slate-500">
              Loading conversation…
            </p>
          ) : null}

          {!loading && messages.length === 0 ? (
            <p className="py-10 text-center text-sm font-semibold text-slate-500">
              No inquiry messages yet.
            </p>
          ) : null}

          {blocks.map((block) => (
            <article
              key={block.key}
              className={`max-w-full rounded-2xl border px-4 py-3 ${
                block.isOutbound
                  ? "ml-auto max-w-[92%] border-indigo-200 bg-indigo-50 sm:max-w-[80%]"
                  : "mr-auto max-w-[92%] border-slate-200 bg-slate-50 sm:max-w-[80%]"
              }`}
            >
              <div className="mb-3 flex items-start gap-3">
                <span
                  className={`grid h-10 w-10 shrink-0 place-items-center rounded-full text-sm font-black ${
                    block.isOutbound
                      ? "bg-indigo-600 text-white"
                      : "bg-slate-800 text-white"
                  }`}
                >
                  {initialsFor(block.senderName)}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-bold text-slate-900">
                    {block.senderName}
                  </p>
                  {block.senderUsername ? (
                    <p className="truncate text-xs text-slate-500">
                      @{block.senderUsername}
                    </p>
                  ) : null}
                  {block.sentByUsername ? (
                    <p className="truncate text-xs text-indigo-700">
                      Sent by {block.sentByUsername}
                    </p>
                  ) : null}
                  <p className="text-xs text-slate-500">{formatTime(block.latestAt)}</p>
                </div>
              </div>

              <div className="space-y-3">
                {block.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`space-y-2 ${message.is_deleted ? "opacity-60" : ""}`}
                  >
                    {message.forward_from_display_name ? (
                      <p className="text-xs font-semibold text-slate-500">
                        Forwarded from {message.forward_from_display_name}
                      </p>
                    ) : null}
                    {message.reply_to_telegram_message_id ? (
                      <p className="text-xs font-semibold text-slate-500">
                        Replying to message #{message.reply_to_telegram_message_id}
                      </p>
                    ) : null}
                    {message.is_deleted ? (
                      <p className="text-xs font-semibold text-red-600">
                        This message was deleted in Telegram.
                      </p>
                    ) : null}
                    {message.text ? (
                      <p className="whitespace-pre-wrap break-words text-sm text-slate-800">
                        {message.text}
                        {message.is_edited ? (
                          <span className="ml-2 text-xs font-semibold text-slate-500">
                            edited
                          </span>
                        ) : null}
                      </p>
                    ) : null}
                    {message.has_media ? <InquiryMediaPreview message={message} /> : null}
                    {message.telegram_grouped_id ? (
                      <p className="text-[11px] font-semibold text-slate-400">
                        Album item (group {message.telegram_grouped_id})
                      </p>
                    ) : null}
                    {message.caption ? (
                      <p className="whitespace-pre-wrap break-words text-sm text-slate-700">
                        {message.caption}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>

        <form
          onSubmit={handleSend}
          className="border-t border-slate-200 bg-slate-50 px-3 py-4 sm:px-5"
        >
          {error ? (
            <p className="mb-3 text-sm font-medium text-red-700">{error}</p>
          ) : null}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1 space-y-2">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={3}
                placeholder="Write a message to the cashout group…"
                className="w-full resize-y rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none ring-indigo-500 focus:ring-2"
              />
              <div className="flex flex-wrap items-center gap-2">
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    className="hidden"
                    onChange={(event) =>
                      setAttachment(event.target.files?.[0] ?? null)
                    }
                  />
                  Attach image
                </label>
                {attachment ? (
                  <>
                    <span className="max-w-full truncate text-xs font-semibold text-slate-600">
                      {attachment.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => setAttachment(null)}
                      className="text-xs font-bold text-red-700"
                    >
                      Remove
                    </button>
                  </>
                ) : null}
              </div>
            </div>
            <button
              type="submit"
              disabled={sending}
              className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-bold text-white disabled:opacity-50"
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </form>
      </section>
    </AppShell>
  );
}
