"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

export const PAYMENT_NOTIFICATION_SOUND_KEY = "paymentNotificationSound";
export const PAYMENT_NOTIFICATION_VOLUME_KEY = "paymentNotificationVolume";

const PLAY_THROTTLE_MS = 500;
const PENDING_ALARM_INTERVAL_MS = 3000;
const DEFAULT_ENABLED = true;
const DEFAULT_VOLUME: PaymentNotificationVolume = "high";

export type PaymentNotificationVolume = "low" | "medium" | "high";
export type PaymentNotificationSound =
  | "newPayment"
  | "claimed"
  | "completed"
  | "error";

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
let soundPlayingUntil = 0;
let fallbackAlarmDataUri: string | null = null;
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

function getStoredVolume(): PaymentNotificationVolume {
  const browserWindow = getWindow();
  if (!browserWindow) return DEFAULT_VOLUME;

  const storedVolume = browserWindow.localStorage.getItem(
    PAYMENT_NOTIFICATION_VOLUME_KEY,
  );
  if (
    storedVolume === "low" ||
    storedVolume === "medium" ||
    storedVolume === "high"
  ) {
    return storedVolume;
  }
  return DEFAULT_VOLUME;
}

function getVolumeGain(): number {
  const volume = getStoredVolume();
  if (volume === "low") return 0.42;
  if (volume === "medium") return 0.68;
  return 0.95;
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

async function resumeAudioContextIfNeeded(
  context: AudioContext,
): Promise<boolean> {
  if (context.state === "closed") return false;
  if (context.state === "suspended") {
    try {
      await context.resume();
    } catch {
      return false;
    }
  }
  return context.state === "running";
}

function markUserInteraction(): void {
  hasUserInteraction = true;
  const context = createAudioContext();
  if (context) {
    void resumeAudioContextIfNeeded(context);
  }
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

function buildFallbackAlarmDataUri(): string {
  const sampleRate = 44100;
  const durationSeconds = 0.42;
  const numSamples = Math.floor(sampleRate * durationSeconds);
  const bytesPerSample = 2;
  const dataSize = numSamples * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, dataSize, true);

  const frequency = 880;
  for (let index = 0; index < numSamples; index += 1) {
    const time = index / sampleRate;
    const envelope = Math.min(1, time / 0.02) * Math.max(0, 1 - time / durationSeconds);
    const sample =
      Math.sin(2 * Math.PI * frequency * time) * envelope * 0.55 +
      Math.sin(2 * Math.PI * frequency * 2 * time) * envelope * 0.18;
    view.setInt16(44 + index * bytesPerSample, sample * 0x7fff, true);
  }

  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index] ?? 0);
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

function getFallbackAlarmDataUri(): string {
  fallbackAlarmDataUri = fallbackAlarmDataUri ?? buildFallbackAlarmDataUri();
  return fallbackAlarmDataUri;
}

function playFallbackAlarmSound(): void {
  if (!getStoredPreference() || !hasUserInteraction) return;

  const browserWindow = getWindow();
  if (!browserWindow) return;

  const now = Date.now();
  if (now - lastPlayedAt < PLAY_THROTTLE_MS) return;
  if (now < soundPlayingUntil) return;

  const audio = new Audio(getFallbackAlarmDataUri());
  audio.volume = getVolumeGain();
  lastPlayedAt = now;
  soundPlayingUntil = now + 900;
  void audio.play().catch(() => undefined);
}

async function playPendingPaymentAlarmTick(): Promise<void> {
  if (!hasUnacknowledgedPendingPayments()) {
    stopPendingPaymentAlarm();
    return;
  }

  const context = createAudioContext();
  if (context && (await resumeAudioContextIfNeeded(context))) {
    playNotificationSound("newPayment");
    return;
  }

  playFallbackAlarmSound();
}

function ensurePendingPaymentAlarm(): void {
  attachInteractionListeners();
  if (!getStoredPreference() || !hasUserInteraction) return;
  if (!hasUnacknowledgedPendingPayments()) {
    stopPendingPaymentAlarm();
    return;
  }

  void playPendingPaymentAlarmTick();
  const browserWindow = getWindow();
  if (!browserWindow || browserWindow.__ledgerPendingPaymentAlarmTimer) return;
  browserWindow.__ledgerPendingPaymentAlarmTimer = setInterval(() => {
    void playPendingPaymentAlarmTick();
  }, PENDING_ALARM_INTERVAL_MS);
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
    if (
      event.key === PAYMENT_NOTIFICATION_SOUND_KEY ||
      event.key === PAYMENT_NOTIFICATION_VOLUME_KEY
    ) {
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

export function getPaymentNotificationVolume(): PaymentNotificationVolume {
  return getStoredVolume();
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

export function setPaymentNotificationVolume(
  volume: PaymentNotificationVolume,
): void {
  const browserWindow = getWindow();
  if (!browserWindow) return;

  browserWindow.localStorage.setItem(PAYMENT_NOTIFICATION_VOLUME_KEY, volume);
  emitPreferenceChange();
}

function connectToneGraph(
  context: AudioContext,
  startedAt: number,
  duration: number,
): GainNode {
  const masterGain = context.createGain();
  const compressor = context.createDynamicsCompressor();
  const echoDelay = context.createDelay(0.35);
  const echoFeedback = context.createGain();
  const echoGain = context.createGain();

  compressor.threshold.setValueAtTime(-18, startedAt);
  compressor.knee.setValueAtTime(24, startedAt);
  compressor.ratio.setValueAtTime(5, startedAt);
  compressor.attack.setValueAtTime(0.004, startedAt);
  compressor.release.setValueAtTime(0.18, startedAt);

  masterGain.gain.setValueAtTime(0.0001, startedAt);
  masterGain.gain.exponentialRampToValueAtTime(
    getVolumeGain(),
    startedAt + 0.028,
  );
  masterGain.gain.exponentialRampToValueAtTime(0.0001, startedAt + duration);

  echoDelay.delayTime.setValueAtTime(0.105, startedAt);
  echoFeedback.gain.setValueAtTime(0.16, startedAt);
  echoGain.gain.setValueAtTime(0.18, startedAt);

  masterGain.connect(compressor);
  masterGain.connect(echoDelay);
  echoDelay.connect(echoFeedback);
  echoFeedback.connect(echoDelay);
  echoDelay.connect(echoGain);
  echoGain.connect(compressor);
  compressor.connect(context.destination);

  return masterGain;
}

function scheduleRichTone({
  context,
  destination,
  frequency,
  startTime,
  duration,
  gain,
  type = "triangle",
}: {
  context: AudioContext;
  destination: AudioNode;
  frequency: number;
  startTime: number;
  duration: number;
  gain: number;
  type?: OscillatorType;
}): void {
  const partials = [
    { ratio: 1, gain: 1 },
    { ratio: 2, gain: 0.28 },
    { ratio: 3, gain: 0.12 },
  ];

  for (const partial of partials) {
    const oscillator = context.createOscillator();
    const toneGain = context.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency * partial.ratio, startTime);
    toneGain.gain.setValueAtTime(0.0001, startTime);
    toneGain.gain.exponentialRampToValueAtTime(
      gain * partial.gain,
      startTime + 0.025,
    );
    toneGain.gain.exponentialRampToValueAtTime(
      Math.max(0.0001, gain * partial.gain * 0.48),
      startTime + duration * 0.58,
    );
    toneGain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
    oscillator.connect(toneGain);
    toneGain.connect(destination);
    oscillator.start(startTime);
    oscillator.stop(startTime + duration + 0.02);
  }
}

function playNotificationSound(sound: PaymentNotificationSound): void {
  if (!getStoredPreference()) return;
  attachInteractionListeners();

  const now = Date.now();
  if (now - lastPlayedAt < PLAY_THROTTLE_MS) return;
  if (now < soundPlayingUntil) return;
  if (!hasUserInteraction) return;

  const context = createAudioContext();
  if (!context) return;

  void resumeAudioContextIfNeeded(context).then((running) => {
    if (!running) return;

    const playbackStartedAt = Date.now();
    if (playbackStartedAt - lastPlayedAt < PLAY_THROTTLE_MS) return;
    if (playbackStartedAt < soundPlayingUntil) return;

    lastPlayedAt = playbackStartedAt;
    const startedAt = context.currentTime;
    const durationBySound: Record<PaymentNotificationSound, number> = {
      newPayment: 0.9,
      claimed: 0.26,
      completed: 0.62,
      error: 0.54,
    };
    const duration = durationBySound[sound];
    const masterGain = connectToneGraph(context, startedAt, duration);

    if (sound === "newPayment") {
      [
        { frequency: 523.25, offset: 0, gain: 0.2, duration: 0.28 },
        { frequency: 659.25, offset: 0.18, gain: 0.23, duration: 0.32 },
        { frequency: 880, offset: 0.39, gain: 0.27, duration: 0.38 },
      ].forEach((note) => {
        scheduleRichTone({
          context,
          destination: masterGain,
          frequency: note.frequency,
          startTime: startedAt + note.offset,
          duration: note.duration,
          gain: note.gain,
        });
      });
    } else if (sound === "claimed") {
      scheduleRichTone({
        context,
        destination: masterGain,
        frequency: 880,
        startTime: startedAt,
        duration: 0.18,
        gain: 0.18,
        type: "sine",
      });
      scheduleRichTone({
        context,
        destination: masterGain,
        frequency: 1174.66,
        startTime: startedAt + 0.07,
        duration: 0.16,
        gain: 0.14,
        type: "sine",
      });
    } else if (sound === "completed") {
      [
        { frequency: 659.25, offset: 0, gain: 0.17, duration: 0.24 },
        { frequency: 880, offset: 0.16, gain: 0.18, duration: 0.25 },
        { frequency: 1318.51, offset: 0.34, gain: 0.16, duration: 0.22 },
      ].forEach((note) => {
        scheduleRichTone({
          context,
          destination: masterGain,
          frequency: note.frequency,
          startTime: startedAt + note.offset,
          duration: note.duration,
          gain: note.gain,
          type: "sine",
        });
      });
    } else {
      [
        { frequency: 392, offset: 0, gain: 0.18, duration: 0.28 },
        { frequency: 349.23, offset: 0.2, gain: 0.14, duration: 0.28 },
      ].forEach((note) => {
        scheduleRichTone({
          context,
          destination: masterGain,
          frequency: note.frequency,
          startTime: startedAt + note.offset,
          duration: note.duration,
          gain: note.gain,
          type: "sine",
        });
      });
    }

    soundPlayingUntil = Date.now() + duration * 1000 + 80;
  });
}

export function playNewPaymentNotification(): void {
  playNotificationSound("newPayment");
}

export function playPaymentClaimedNotification(): void {
  playNotificationSound("claimed");
}

export function playPaymentCompletedNotification(): void {
  playNotificationSound("completed");
}

export function playPaymentErrorNotification(): void {
  playNotificationSound("error");
}

export function usePaymentNotificationPreference(): {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
  volume: PaymentNotificationVolume;
  setVolume: (volume: PaymentNotificationVolume) => void;
} {
  const enabled = useSyncExternalStore(
    subscribeToPreferenceChanges,
    getStoredPreference,
    () => DEFAULT_ENABLED,
  );
  const volume = useSyncExternalStore(
    subscribeToPreferenceChanges,
    getStoredVolume,
    () => DEFAULT_VOLUME,
  );

  const setEnabled = useCallback((nextEnabled: boolean) => {
    setPaymentNotificationSoundEnabled(nextEnabled);
  }, []);
  const setVolume = useCallback((nextVolume: PaymentNotificationVolume) => {
    setPaymentNotificationVolume(nextVolume);
  }, []);

  return { enabled, setEnabled, volume, setVolume };
}

export function usePaymentNotificationSound(): {
  notifyNewPayment: () => void;
  notifyPaymentClaimed: () => void;
  notifyPaymentCompleted: () => void;
  notifyPaymentError: () => void;
  acknowledgePayment: (paymentId: number) => void;
  setVisiblePendingPayments: (paymentIds: number[]) => void;
} {
  useEffect(() => {
    attachInteractionListeners();
    return () => {
      clearPendingPaymentAlarmState();
    };
  }, []);

  return {
    notifyNewPayment: playNewPaymentNotification,
    notifyPaymentClaimed: playPaymentClaimedNotification,
    notifyPaymentCompleted: playPaymentCompletedNotification,
    notifyPaymentError: playPaymentErrorNotification,
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

export function resetPaymentNotificationsForTests(): void {
  hasUserInteraction = false;
  interactionListenersAttached = false;
  lastPlayedAt = 0;
  soundPlayingUntil = 0;
  fallbackAlarmDataUri = null;
  clearPendingPaymentAlarmState();
  const browserWindow = getWindow();
  if (browserWindow) {
    delete browserWindow.__ledgerPaymentAudioContext;
  }
}
