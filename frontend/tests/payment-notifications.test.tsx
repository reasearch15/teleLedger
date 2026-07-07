import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgePendingPayment,
  resetPaymentNotificationsForTests,
  setPaymentNotificationSoundEnabled,
  setVisiblePendingPayments,
  usePaymentNotificationSound,
} from "@/lib/payment-notifications";

function AlarmHarness() {
  usePaymentNotificationSound();
  return null;
}

class MockAudioContext {
  state: AudioContextState = "suspended";
  currentTime = 0;

  async resume(): Promise<void> {
    this.state = "running";
  }

  createGain(): GainNode {
    return {
      gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
      connect: vi.fn(),
    } as unknown as GainNode;
  }

  createDynamicsCompressor(): DynamicsCompressorNode {
    const param = { setValueAtTime: vi.fn() };
    return {
      threshold: param,
      knee: param,
      ratio: param,
      attack: param,
      release: param,
      connect: vi.fn(),
    } as unknown as DynamicsCompressorNode;
  }

  createDelay(): DelayNode {
    return { connect: vi.fn(), delayTime: { setValueAtTime: vi.fn() } } as unknown as DelayNode;
  }

  createOscillator(): OscillatorNode {
    return {
      type: "triangle",
      frequency: { setValueAtTime: vi.fn() },
      connect: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
    } as unknown as OscillatorNode;
  }

  get destination(): AudioDestinationNode {
    return {} as AudioDestinationNode;
  }
}

describe("payment notifications alarm", () => {
  const getAlarmTimer = () =>
    (
      window as Window & {
        __ledgerPendingPaymentAlarmTimer?: ReturnType<typeof setInterval> | null;
      }
    ).__ledgerPendingPaymentAlarmTimer ?? null;

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    resetPaymentNotificationsForTests();
    vi.stubGlobal(
      "AudioContext",
      vi.fn(function AudioContext(this: MockAudioContext) {
        return new MockAudioContext();
      }),
    );
  });

  afterEach(() => {
    resetPaymentNotificationsForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not register visibilitychange listeners", () => {
    const addEventListenerSpy = vi.spyOn(document, "addEventListener");

    render(<AlarmHarness />);

    const visibilityCalls = addEventListenerSpy.mock.calls.filter(
      ([eventName]) => eventName === "visibilitychange",
    );
    expect(visibilityCalls).toHaveLength(0);
  });

  it("keeps the pending alarm timer running when the tab becomes hidden", () => {
    setVisiblePendingPayments([101]);
    window.dispatchEvent(new PointerEvent("pointerdown"));

    expect(getAlarmTimer()).not.toBeNull();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    expect(getAlarmTimer()).not.toBeNull();

    vi.advanceTimersByTime(3000);
    expect(getAlarmTimer()).not.toBeNull();
  });

  it("stops the alarm after a pending payment is acknowledged", () => {
    setVisiblePendingPayments([202]);
    window.dispatchEvent(new PointerEvent("pointerdown"));

    expect(getAlarmTimer()).not.toBeNull();

    acknowledgePendingPayment(202);

    expect(getAlarmTimer()).toBeNull();
  });

  it("stops the alarm when notification sound is disabled", () => {
    setVisiblePendingPayments([303]);
    window.dispatchEvent(new PointerEvent("pointerdown"));

    expect(getAlarmTimer()).not.toBeNull();

    setPaymentNotificationSoundEnabled(false);

    expect(getAlarmTimer()).toBeNull();
  });

  it("does not start the alarm before user interaction", () => {
    setVisiblePendingPayments([404]);

    expect(getAlarmTimer()).toBeNull();
  });

  it("falls back to HTML audio when AudioContext stays suspended", async () => {
    const audioPlay = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal(
      "Audio",
      vi.fn(function Audio(this: {
        play: typeof audioPlay;
        volume: number;
        src: string;
        preload: string;
      }) {
        this.play = audioPlay;
        this.volume = 1;
        this.src = "";
        this.preload = "auto";
        return this;
      }),
    );

    class SuspendedAudioContext extends MockAudioContext {
      override async resume(): Promise<void> {
        this.state = "suspended";
      }
    }

    vi.stubGlobal(
      "AudioContext",
      vi.fn(function AudioContext(this: SuspendedAudioContext) {
        return new SuspendedAudioContext();
      }),
    );

    render(<AlarmHarness />);
    setVisiblePendingPayments([505]);
    window.dispatchEvent(new PointerEvent("pointerdown"));

    await vi.advanceTimersByTimeAsync(3000);

    expect(audioPlay).toHaveBeenCalled();
  });
});
