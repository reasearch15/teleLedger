"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useAuth } from "@/components/auth-provider";
import { environment } from "@/lib/env";
import {
  matchesLiveEvent,
  parseLiveEvent,
  type LiveEventType,
} from "@/lib/live-events";

export type LiveConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting";

type Subscription = {
  id: number;
  events: readonly LiveEventType[];
  callback: () => void;
  debouncedCallback: () => void;
};

type LiveUpdatesContextValue = {
  connectionStatus: LiveConnectionStatus;
  subscribe: (
    events: readonly LiveEventType[],
    callback: () => void,
  ) => () => void;
};

const LiveUpdatesContext = createContext<LiveUpdatesContextValue | null>(null);

const REFETCH_DEBOUNCE_MS = 300;
const MAX_RECONNECT_DELAY_MS = 30_000;

function createDebouncedCallback(callback: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return () => {
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = null;
      callback();
    }, REFETCH_DEBOUNCE_MS);
  };
}

export function LiveUpdatesProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user } = useAuth();
  const [connectionStatus, setConnectionStatus] =
    useState<LiveConnectionStatus>("idle");
  const subscriptionsRef = useRef<Map<number, Subscription>>(new Map());
  const nextSubscriptionId = useRef(0);
  const enabled = Boolean(user) && typeof EventSource !== "undefined";

  const subscribe = useCallback(
    (events: readonly LiveEventType[], callback: () => void) => {
      const id = nextSubscriptionId.current++;
      const subscription: Subscription = {
        id,
        events,
        callback,
        debouncedCallback: createDebouncedCallback(callback),
      };
      subscriptionsRef.current.set(id, subscription);
      return () => {
        subscriptionsRef.current.delete(id);
      };
    },
    [],
  );

  const dispatchEvent = useCallback((eventName: LiveEventType) => {
    for (const subscription of subscriptionsRef.current.values()) {
      if (subscription.events.includes(eventName)) {
        subscription.debouncedCallback();
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      setConnectionStatus(reconnectAttempt === 0 ? "connecting" : "reconnecting");
      source = new EventSource(`${environment.apiUrl}/api/events`, {
        withCredentials: true,
      });

      source.onopen = () => {
        reconnectAttempt = 0;
        setConnectionStatus("connected");
      };

      source.onmessage = (message) => {
        const event = parseLiveEvent(message.data);
        if (event) {
          dispatchEvent(event.event);
        }
      };

      source.onerror = () => {
        source?.close();
        source = null;
        if (closed) return;
        setConnectionStatus("reconnecting");
        const delay = Math.min(
          MAX_RECONNECT_DELAY_MS,
          1000 * 2 ** reconnectAttempt,
        );
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closed = true;
      source?.close();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      setConnectionStatus("idle");
    };
  }, [dispatchEvent, enabled]);

  const value = useMemo(
    () => ({ connectionStatus, subscribe }),
    [connectionStatus, subscribe],
  );

  return (
    <LiveUpdatesContext.Provider value={value}>
      {children}
    </LiveUpdatesContext.Provider>
  );
}

export function useLiveUpdatesContext(): LiveUpdatesContextValue {
  const context = useContext(LiveUpdatesContext);
  if (!context) {
    throw new Error("useLiveUpdatesContext must be used within LiveUpdatesProvider");
  }
  return context;
}

export function useLiveUpdates(
  events: readonly LiveEventType[],
  onUpdate: () => void,
  enabled = true,
): void {
  const { subscribe } = useLiveUpdatesContext();
  const onUpdateRef = useRef(onUpdate);
  const eventsKey = events.join("|");

  useEffect(() => {
    onUpdateRef.current = onUpdate;
  }, [onUpdate]);

  useEffect(() => {
    if (!enabled) return;
    return subscribe(events, () => {
      onUpdateRef.current();
    });
  }, [enabled, eventsKey, subscribe, events]);
}

export function useLiveConnectionStatus(): LiveConnectionStatus {
  return useLiveUpdatesContext().connectionStatus;
}

// Re-export for tests that need to inspect event matching without the provider.
export { matchesLiveEvent, parseLiveEvent };
