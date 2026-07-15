"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import {
  INQUIRY_LIVE_EVENT,
  useLiveConnectionStatus,
  useLiveUpdates,
} from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { INQUIRY_PAGE_EVENTS } from "@/lib/live-events";
import {
  fetchInquiryMediaBlob,
  INQUIRY_CHAT_PAGE_SIZE,
  listInquiryMessages,
  sendInquiryMessage,
} from "@/services/inquiries";
import type { InquiryMessage } from "@/types/api";

type SenderBlock = {
  key: string;
  senderName: string;
  isOutbound: boolean;
  sentByName: string | null;
  latestAt: string;
  messages: InquiryMessage[];
};

const FALLBACK_REFRESH_MS = 12_000;

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
  isOutbound: boolean;
  sentByName: string | null;
} {
  if (message.message_source === "inquiry") {
    return {
      name: "TeleLedger",
      isOutbound: true,
      sentByName: message.sent_by_name ?? "Staff",
    };
  }
  return {
    name: message.sender_alias ?? "Customer",
    isOutbound: message.direction === "outbound",
    sentByName: null,
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
        `${sender.isOutbound ? "outbound" : "inbound"}:${sender.name}`
    ) {
      blocks.push({
        key: `${message.id}`,
        senderName: sender.name,
        isOutbound: sender.isOutbound,
        sentByName: sender.sentByName,
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

function compareMessages(a: InquiryMessage, b: InquiryMessage): number {
  const dateDelta = new Date(a.message_date).getTime() - new Date(b.message_date).getTime();
  if (dateDelta !== 0) return dateDelta;
  return a.id - b.id;
}

function mergeMessages(
  current: InquiryMessage[],
  incoming: InquiryMessage[],
): InquiryMessage[] {
  const byId = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) {
    byId.set(message.id, message);
  }
  return Array.from(byId.values()).sort(compareMessages);
}

function isNearBottom(element: HTMLDivElement | null): boolean {
  if (!element) return true;
  return element.scrollHeight - element.scrollTop - element.clientHeight < 96;
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
  const liveConnectionStatus = useLiveConnectionStatus();
  const [messages, setMessages] = useState<InquiryMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [showNewMessages, setShowNewMessages] = useState(false);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sendKey, setSendKey] = useState(() => crypto.randomUUID());
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const requestVersion = useRef(0);
  const messagesRef = useRef<InquiryMessage[]>([]);
  const shouldScrollToBottom = useRef(false);
  const prependMetrics = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const wasHidden = useRef(false);
  const liveRefreshTimer = useRef<number | null>(null);
  const fallbackRefreshActive =
    Boolean(user?.id) &&
    (liveConnectionStatus === "reconnecting" || liveConnectionStatus === "offline");

  const blocks = useMemo(() => buildSenderBlocks(messages), [messages]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const loadLatest = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    const version = ++requestVersion.current;
    const wasNearBottom = isNearBottom(scrollRef.current);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    setError("");
    try {
      const page = await listInquiryMessages({ limit: INQUIRY_CHAT_PAGE_SIZE });
      if (version !== requestVersion.current) return;
      const loadedItems = [...page.items].sort(compareMessages);
      if (mode === "initial") {
        setMessages(loadedItems);
        setHasMore(page.has_more ?? page.pagination.hasMore);
        setNextCursor(page.next_cursor ?? page.pagination.nextCursor);
        shouldScrollToBottom.current = true;
      } else {
        const currentIds = new Set(messagesRef.current.map((message) => message.id));
        const addedNewerMessage = loadedItems.some((message) => !currentIds.has(message.id));
        setMessages((current) => {
          const merged = mergeMessages(current, loadedItems);
          console.info("inquiry_state_updated", {
            loaded_count: loadedItems.length,
            previous_count: current.length,
            next_count: merged.length,
            added_newer_message: addedNewerMessage,
          });
          return merged;
        });
        if (addedNewerMessage && wasNearBottom) {
          shouldScrollToBottom.current = true;
        }
        if (addedNewerMessage && !wasNearBottom) {
          setShowNewMessages(true);
        }
      }
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

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || loading) return;
    if (prependMetrics.current) {
      const metrics = prependMetrics.current;
      prependMetrics.current = null;
      element.scrollTop = metrics.scrollTop + (element.scrollHeight - metrics.scrollHeight);
      return;
    }
    if (shouldScrollToBottom.current) {
      shouldScrollToBottom.current = false;
      element.scrollTop = element.scrollHeight;
    }
  }, [loading, messages]);

  const handleScroll = () => {
    if (isNearBottom(scrollRef.current)) {
      setShowNewMessages(false);
    }
  };

  const scheduleLiveRefresh = useCallback(
    (events: unknown[]) => {
      console.info("inquiry_live_update_refresh", {
        events: events.map((event) =>
          typeof event === "object" && event !== null && "event" in event
            ? (event as { event: unknown }).event
            : "unknown",
        ),
      });
      if (liveRefreshTimer.current) {
        window.clearTimeout(liveRefreshTimer.current);
      }
      liveRefreshTimer.current = window.setTimeout(() => {
        liveRefreshTimer.current = null;
        void loadLatest("refresh");
      }, 100);
    },
    [loadLatest],
  );

  useLiveUpdates(INQUIRY_PAGE_EVENTS, scheduleLiveRefresh, Boolean(user?.id));

  useEffect(() => {
    if (!user?.id) return;
    const handleInquiryLiveEvent = (event: Event) => {
      scheduleLiveRefresh([
        event instanceof CustomEvent ? event.detail : { event: "inquiry_event" },
      ]);
    };
    window.addEventListener(INQUIRY_LIVE_EVENT, handleInquiryLiveEvent);
    return () => {
      window.removeEventListener(INQUIRY_LIVE_EVENT, handleInquiryLiveEvent);
      if (liveRefreshTimer.current) {
        window.clearTimeout(liveRefreshTimer.current);
        liveRefreshTimer.current = null;
      }
    };
  }, [scheduleLiveRefresh, user?.id]);

  useEffect(() => {
    if (!user?.id) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        wasHidden.current = true;
        return;
      }
      if (wasHidden.current) {
        wasHidden.current = false;
        void loadLatest("refresh");
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [loadLatest, user?.id]);

  useEffect(() => {
    if (!fallbackRefreshActive) return;
    void loadLatest("refresh");
    const timer = window.setInterval(() => {
      void loadLatest("refresh");
    }, FALLBACK_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [fallbackRefreshActive, loadLatest]);

  const loadOlder = async () => {
    if ((!nextCursor && messages.length === 0) || loadingMore || !hasMore) return;
    const element = scrollRef.current;
    if (element) {
      prependMetrics.current = {
        scrollHeight: element.scrollHeight,
        scrollTop: element.scrollTop,
      };
    }
    setLoadingMore(true);
    setError("");
    try {
      const oldestLoadedId = messages[0]?.id;
      const page = await listInquiryMessages({
        limit: INQUIRY_CHAT_PAGE_SIZE,
        beforeMessageId: oldestLoadedId,
        cursor: oldestLoadedId ? null : nextCursor,
      });
      setMessages((current) => {
        const existing = new Set(current.map((item) => item.id));
        const older = [...page.items].sort(compareMessages).filter((item) => !existing.has(item.id));
        return [...older, ...current];
      });
      setHasMore(page.has_more ?? page.pagination.hasMore);
      setNextCursor(page.next_cursor ?? page.pagination.nextCursor);
    } catch (loadError) {
      prependMetrics.current = null;
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

  const liveStatusText =
    liveConnectionStatus === "connected"
      ? "Live updates connected"
      : liveConnectionStatus === "offline"
        ? "Offline — fallback refresh active"
        : "Reconnecting…";

  return (
    <AppShell
      title="Inquiry"
      description="Manage customer inquiry conversations. Workflow messages sent through the Cashout panel stay hidden here."
    >
      <section className="flex h-[clamp(28rem,calc(100dvh-15rem),52rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-sm font-bold text-slate-900">Inquiry conversation</p>
            <p className="text-xs text-slate-500">
              {refreshing ? "Refreshing…" : liveStatusText}
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
          onScroll={handleScroll}
          className="relative flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 py-4 sm:px-5"
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
              className={`min-w-0 max-w-full rounded-2xl border px-4 py-3 ${
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
                  {block.sentByName ? (
                    <p className="truncate text-xs text-indigo-700">
                      Sent by {block.sentByName}
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
                    {message.is_reply ? (
                      <p className="text-xs font-semibold text-slate-500">
                        Replying to an earlier message
                      </p>
                    ) : null}
                    {message.is_deleted ? (
                      <p className="text-xs font-semibold text-red-600">
                        This message was deleted.
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
                    {message.has_album ? (
                      <p className="text-[11px] font-semibold text-slate-400">
                        Album item
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

        {showNewMessages ? (
          <div className="pointer-events-none -mt-14 flex justify-center px-4 pb-3">
            <button
              type="button"
              onClick={() => {
                shouldScrollToBottom.current = true;
                setShowNewMessages(false);
                scrollRef.current?.scrollTo({
                  top: scrollRef.current.scrollHeight,
                  behavior: "smooth",
                });
              }}
              className="pointer-events-auto rounded-full bg-slate-900 px-4 py-2 text-xs font-bold text-white shadow-lg"
            >
              New messages
            </button>
          </div>
        ) : null}

        <form
          onSubmit={handleSend}
          className="shrink-0 border-t border-slate-200 bg-slate-50 px-3 py-4 sm:px-5"
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
                placeholder="Write an inquiry message..."
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
              {sending ? "Sending..." : "Send Inquiry"}
            </button>
          </div>
        </form>
      </section>
    </AppShell>
  );
}
