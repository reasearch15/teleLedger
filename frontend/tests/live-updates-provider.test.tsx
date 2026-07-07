import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  LiveUpdatesProvider,
  useLiveUpdates,
} from "@/components/live-updates-provider";
import { LIVE_EVENTS } from "@/lib/live-events";

const mockUseAuth = vi.fn();

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => mockUseAuth(),
}));

type EventSourceInstance = {
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: (() => void) | null;
  close: ReturnType<typeof vi.fn>;
  url: string;
};

const eventSources: EventSourceInstance[] = [];

class TestEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(public readonly url: string) {
    eventSources.push(this);
    queueMicrotask(() => this.onopen?.());
  }
}

describe("LiveUpdatesProvider", () => {
  beforeEach(() => {
    eventSources.length = 0;
    vi.stubGlobal("EventSource", TestEventSource);
    mockUseAuth.mockReturnValue({
      user: {
        id: 1,
        username: "staff",
        role: "staff",
      },
    });
  });

  it("opens one SSE connection after login", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <LiveUpdatesProvider>{children}</LiveUpdatesProvider>
    );

    const { unmount } = renderHook(
      () => useLiveUpdates([LIVE_EVENTS.PAYMENT_CLAIMED], vi.fn()),
      { wrapper },
    );

    await waitFor(() => {
      expect(eventSources).toHaveLength(1);
      expect(eventSources[0]?.url).toContain("/api/events");
    });

    unmount();
    expect(eventSources[0]?.close).toHaveBeenCalled();
  });

  it("calls the matching page refetch when an event arrives", async () => {
    const onPaymentsUpdate = vi.fn();
    const onCashoutUpdate = vi.fn();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <LiveUpdatesProvider>{children}</LiveUpdatesProvider>
    );

    renderHook(
      () => {
        useLiveUpdates([LIVE_EVENTS.PAYMENT_CLAIMED], onPaymentsUpdate);
        useLiveUpdates([LIVE_EVENTS.CASHOUT_COMPLETED], onCashoutUpdate);
      },
      { wrapper },
    );

    await waitFor(() => expect(eventSources).toHaveLength(1));

    act(() => {
      eventSources[0]?.onmessage?.({
        data: JSON.stringify({
          event: LIVE_EVENTS.PAYMENT_CLAIMED,
          payment_id: 12,
        }),
      } as MessageEvent<string>);
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });

    expect(onPaymentsUpdate).toHaveBeenCalledTimes(1);
    expect(onCashoutUpdate).not.toHaveBeenCalled();
  });

  it("does not open duplicate SSE connections when navigating between subscribers", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <LiveUpdatesProvider>{children}</LiveUpdatesProvider>
    );

    const { unmount } = renderHook(
      () => {
        useLiveUpdates([LIVE_EVENTS.PAYMENT_CLAIMED], vi.fn());
        useLiveUpdates([LIVE_EVENTS.CASHOUT_CREATED], vi.fn());
      },
      { wrapper },
    );

    await waitFor(() => expect(eventSources).toHaveLength(1));

    unmount();
    expect(eventSources[0]?.close).toHaveBeenCalledTimes(1);
  });
});
