"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

export const PAYMENT_NOTIFICATION_SOUND_KEY = "paymentNotificationSound";

const PLAY_THROTTLE_MS = 500;
const PENDING_ALARM_INTERVAL_MS = 3000;
const DEFAULT_ENABLED = true;

type AudioContextConstructor = typeof AudioContext;
type BrowserWindow = Window &
  typeof globalThis & {
    __ledgerPaymentAudioContext?: AudioContext;
    __ledgerPendingPaymentAlarmTimer?: ReturnType<typeof setInterval> | null;
    webkitAudioContext?: AudioContextConstructor;
  };

let hasUserInteraction = false;
let interactionListenersAttached = false;
let lastPlayedAt = 0;
let visiblePendingPaymentIds = new Set<number>();
let acknowledgedPendingPaymentIds = new Set<number>();
const preferenceListeners = new Set<() => void>();

function getWindow(): BrowserWindow | null {
  if (typeof window === "undefined") return null;
  return window;
}

function getStoredPreference(): boolean {
  const browserWindow = getWindow();
  if (!browserWindow) return DEFAULT_ENABLED;
  return (
    browserWindow.localStorage.getItem(PAYMENT_NOTIFICATION_SOUND_KEY) !== "off"
  );
}

function createAudioContext(): AudioContext | null {
  const browserWindow = getWindow();
  if (!browserWindow) return null;

  const AudioContextClass = (browserWindow.AudioContext ??
    browserWindow.webkitAudioContext) as AudioContextConstructor | undefined;
  if (!AudioContextClass) return null;

  browserWindow.__ledgerPaymentAudioContext =
    browserWindow.__ledgerPaymentAudioContext ?? new AudioContextClass();
  return browserWindow.__ledgerPaymentAudioContext;
}

function markUserInteraction(): void {
  hasUserInteraction = true;
  void createAudioContext()?.resume();
  reconcilePendingPaymentAlarm();
}

function attachInteractionListeners(): void {
  const browserWindow = getWindow();
  if (!browserWindow || interactionListenersAttached) return;

  interactionListenersAttached = true;
  const options: AddEventListenerOptions = { passive: true };
  browserWindow.addEventListener("pointerdown", markUserInteraction, options);
  browserWindow.addEventListener("keydown", markUserInteraction);
  browserWindow.addEventListener("touchstart", markUserInteraction, options);
}

function hasUnacknowledgedPendingPayments(): boolean {
  for (const paymentId of visiblePendingPaymentIds) {
    if (!acknowledgedPendingPaymentIds.has(paymentId)) {
      return true;
    }
  }
  return false;
}

function stopPendingPaymentAlarm(): void {
  const browserWindow = getWindow();
  const pendingAlarmTimer = browserWindow?.__ledgerPendingPaymentAlarmTimer;
  if (!pendingAlarmTimer) return;
  clearInterval(pendingAlarmTimer);
  if (browserWindow) {
    browserWindow.__ledgerPendingPaymentAlarmTimer = null;
  }
}

function shouldPauseForHiddenTab(): boolean {
  const browserWindow = getWindow();
  return browserWindow?.document.visibilityState === "hidden";
}

function playPendingPaymentAlarmTick(): void {
  if (shouldPauseForHiddenTab()) return;
  if (!hasUnacknowledgedPendingPayments()) {
    stopPendingPaymentAlarm();
    return;
  }
  playNewPaymentNotification();
}

function ensurePendingPaymentAlarm(): void {
  attachInteractionListeners();
  if (!getStoredPreference() || shouldPauseForHiddenTab()) return;
  if (!hasUnacknowledgedPendingPayments()) {
    stopPendingPaymentAlarm();
    return;
  }

  playPendingPaymentAlarmTick();
  const browserWindow = getWindow();
  if (!browserWindow || browserWindow.__ledgerPendingPaymentAlarmTimer) return;
  browserWindow.__ledgerPendingPaymentAlarmTimer = setInterval(
    playPendingPaymentAlarmTick,
    PENDING_ALARM_INTERVAL_MS,
  );
}

function reconcilePendingPaymentAlarm(): void {
  if (hasUnacknowledgedPendingPayments()) {
    ensurePendingPaymentAlarm();
  } else {
    stopPendingPaymentAlarm();
  }
}

function clearPendingPaymentAlarmState(): void {
  visiblePendingPaymentIds = new Set();
  acknowledgedPendingPaymentIds = new Set();
  stopPendingPaymentAlarm();
}

function handleVisibilityChange(): void {
  if (shouldPauseForHiddenTab()) {
    stopPendingPaymentAlarm();
    return;
  }
  reconcilePendingPaymentAlarm();
}

function attachVisibilityListener(): () => void {
  const browserWindow = getWindow();
  if (!browserWindow) return () => undefined;

  browserWindow.document.addEventListener(
    "visibilitychange",
    handleVisibilityChange,
  );

  return () => {
    browserWindow.document.removeEventListener(
      "visibilitychange",
      handleVisibilityChange,
    );
  };
}

function emitPreferenceChange(): void {
  for (const listener of preferenceListeners) {
    listener();
  }
}

function subscribeToPreferenceChanges(listener: () => void): () => void {
  attachInteractionListeners();
  preferenceListeners.add(listener);

  const browserWindow = getWindow();
  const handleStorage = (event: StorageEvent) => {
    if (event.key === PAYMENT_NOTIFICATION_SOUND_KEY) {
      listener();
    }
  };
  browserWindow?.addEventListener("storage", handleStorage);

  return () => {
    preferenceListeners.delete(listener);
    browserWindow?.removeEventListener("storage", handleStorage);
  };
}

export function isPaymentNotificationSoundEnabled(): boolean {
  return getStoredPreference();
}

export function setPaymentNotificationSoundEnabled(enabled: boolean): void {
  const browserWindow = getWindow();
  if (!browserWindow) return;

  if (enabled) {
    browserWindow.localStorage.removeItem(PAYMENT_NOTIFICATION_SOUND_KEY);
    attachInteractionListeners();
  } else {
    browserWindow.localStorage.setItem(PAYMENT_NOTIFICATION_SOUND_KEY, "off");
    stopPendingPaymentAlarm();
  }
  emitPreferenceChange();
  if (enabled) {
    reconcilePendingPaymentAlarm();
  }
}

export function playNewPaymentNotification(): void {
  if (!getStoredPreference()) return;
  attachInteractionListeners();

  const now = Date.now();
  if (now - lastPlayedAt < PLAY_THROTTLE_MS) return;
  if (!hasUserInteraction) return;

  const context = createAudioContext();
  if (!context) return;

  lastPlayedAt = now;
  const startedAt = context.currentTime;
  const duration = 0.42;
  const masterGain = context.createGain();
  masterGain.gain.setValueAtTime(0.0001, startedAt);
  masterGain.gain.exponentialRampToValueAtTime(0.075, startedAt + 0.035);
  masterGain.gain.exponentialRampToValueAtTime(0.055, startedAt + 0.18);
  masterGain.gain.exponentialRampToValueAtTime(0.0001, startedAt + duration);
  masterGain.connect(context.destination);

  const firstTone = context.createOscillator();
  const secondTone = context.createOscillator();
  firstTone.type = "sine";
  secondTone.type = "sine";
  firstTone.frequency.setValueAtTime(587.33, startedAt);
  secondTone.frequency.setValueAtTime(783.99, startedAt + 0.11);

  const secondGain = context.createGain();
  secondGain.gain.setValueAtTime(0.0001, startedAt);
  secondGain.gain.exponentialRampToValueAtTime(0.7, startedAt + 0.14);
  secondGain.gain.exponentialRampToValueAtTime(0.0001, startedAt + duration);

  firstTone.connect(masterGain);
  secondTone.connect(secondGain);
  secondGain.connect(masterGain);

  firstTone.start(startedAt);
  firstTone.stop(startedAt + duration);
  secondTone.start(startedAt + 0.1);
  secondTone.stop(startedAt + duration);
}

export function usePaymentNotificationPreference(): {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
} {
  const enabled = useSyncExternalStore(
    subscribeToPreferenceChanges,
    getStoredPreference,
    () => DEFAULT_ENABLED,
  );

  const setEnabled = useCallback((nextEnabled: boolean) => {
    setPaymentNotificationSoundEnabled(nextEnabled);
  }, []);

  return { enabled, setEnabled };
}

export function usePaymentNotificationSound(): {
  notifyNewPayment: () => void;
  acknowledgePayment: (paymentId: number) => void;
  setVisiblePendingPayments: (paymentIds: number[]) => void;
} {
  useEffect(() => {
    attachInteractionListeners();
    const detachVisibilityListener = attachVisibilityListener();
    return () => {
      detachVisibilityListener();
      clearPendingPaymentAlarmState();
    };
  }, []);

  return {
    notifyNewPayment: playNewPaymentNotification,
    acknowledgePayment: acknowledgePendingPayment,
    setVisiblePendingPayments,
  };
}

export function setVisiblePendingPayments(paymentIds: number[]): void {
  const nextVisiblePaymentIds = new Set(paymentIds);

  for (const paymentId of acknowledgedPendingPaymentIds) {
    if (!nextVisiblePaymentIds.has(paymentId)) {
      acknowledgedPendingPaymentIds.delete(paymentId);
    }
  }

  visiblePendingPaymentIds = nextVisiblePaymentIds;
  reconcilePendingPaymentAlarm();
}

export function acknowledgePendingPayment(paymentId: number): void {
  if (!visiblePendingPaymentIds.has(paymentId)) return;
  acknowledgedPendingPaymentIds.add(paymentId);
  reconcilePendingPaymentAlarm();
}
