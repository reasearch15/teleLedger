"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

export const PAYMENT_NOTIFICATION_SOUND_KEY = "paymentNotificationSound";

const PLAY_THROTTLE_MS = 500;
const DEFAULT_ENABLED = true;

type AudioContextConstructor = typeof AudioContext;
type BrowserWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: AudioContextConstructor;
  };

let audioContext: AudioContext | null = null;
let hasUserInteraction = false;
let interactionListenersAttached = false;
let lastPlayedAt = 0;
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

  audioContext = audioContext ?? new AudioContextClass();
  return audioContext;
}

function markUserInteraction(): void {
  hasUserInteraction = true;
  void createAudioContext()?.resume();
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
  }
  emitPreferenceChange();
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
} {
  useEffect(() => {
    attachInteractionListeners();
  }, []);

  return {
    notifyNewPayment: playNewPaymentNotification,
  };
}
