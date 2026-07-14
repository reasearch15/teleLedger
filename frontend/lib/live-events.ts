export const LIVE_EVENTS = {
  PAYMENT_CREATED: "payment_created",
  PAYMENT_CLAIMED: "payment_claimed",
  PAYMENT_UNCLAIMED: "payment_unclaimed",
  PAYMENT_DONE: "payment_done",
  PAYMENT_REOPENED: "payment_reopened",
  PAYMENT_DISMISSED: "payment_dismissed",
  PAYMENT_ALL_COADMINS_DECLINED: "payment_all_coadmins_declined",
  PAYMENT_DECLINED_REVIEW_DISMISSED: "payment_declined_review_dismissed",
  PAYMENT_DELETED: "payment_deleted",
  CASHOUT_CREATED: "cashout_created",
  CASHOUT_SENT: "cashout_sent",
  CASHOUT_COMPLETED: "cashout_completed",
  CASHOUT_CANCELLED: "cashout_cancelled",
  CASHOUT_NOTES_UPDATED: "cashout_notes_updated",
  INQUIRY_MESSAGE_CREATED: "inquiry_message_created",
  INQUIRY_MESSAGE_UPDATED: "inquiry_message_updated",
  INQUIRY_MEDIA_READY: "inquiry_media_ready",
  SETTLEMENT_CREATED: "settlement_created",
  SETTLEMENT_DONE: "settlement_done",
  LEDGER_CHANGED: "ledger_changed",
  STAFF_CHANGED: "staff_changed",
} as const;

export type LiveEventType = (typeof LIVE_EVENTS)[keyof typeof LIVE_EVENTS];

export type LiveEvent = {
  event: LiveEventType;
  payment_id?: number;
  cashout_id?: number;
  inquiry_message_id?: number;
  settlement_id?: number;
  user_id?: number;
  coadmin_id?: number;
};

export const PAYMENT_PAGE_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.PAYMENT_CREATED,
  LIVE_EVENTS.PAYMENT_CLAIMED,
  LIVE_EVENTS.PAYMENT_UNCLAIMED,
  LIVE_EVENTS.PAYMENT_DONE,
  LIVE_EVENTS.PAYMENT_REOPENED,
  LIVE_EVENTS.PAYMENT_DISMISSED,
  LIVE_EVENTS.PAYMENT_ALL_COADMINS_DECLINED,
  LIVE_EVENTS.PAYMENT_DECLINED_REVIEW_DISMISSED,
  LIVE_EVENTS.PAYMENT_DELETED,
];

export const DECLINED_PAYMENTS_PAGE_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.PAYMENT_ALL_COADMINS_DECLINED,
  LIVE_EVENTS.PAYMENT_DECLINED_REVIEW_DISMISSED,
  LIVE_EVENTS.PAYMENT_DELETED,
  LIVE_EVENTS.PAYMENT_DISMISSED,
];

export const PAYMENT_HISTORY_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.PAYMENT_CLAIMED,
  LIVE_EVENTS.PAYMENT_UNCLAIMED,
  LIVE_EVENTS.PAYMENT_DONE,
  LIVE_EVENTS.PAYMENT_REOPENED,
];

export const CASHOUT_PAGE_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.CASHOUT_CREATED,
  LIVE_EVENTS.CASHOUT_SENT,
  LIVE_EVENTS.CASHOUT_COMPLETED,
  LIVE_EVENTS.CASHOUT_CANCELLED,
  LIVE_EVENTS.CASHOUT_NOTES_UPDATED,
];

export const INQUIRY_PAGE_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.INQUIRY_MESSAGE_CREATED,
  LIVE_EVENTS.INQUIRY_MESSAGE_UPDATED,
  LIVE_EVENTS.INQUIRY_MEDIA_READY,
];

export const LEDGER_PAGE_EVENTS: LiveEventType[] = [
  LIVE_EVENTS.PAYMENT_DONE,
  LIVE_EVENTS.CASHOUT_COMPLETED,
  LIVE_EVENTS.SETTLEMENT_CREATED,
  LIVE_EVENTS.SETTLEMENT_DONE,
  LIVE_EVENTS.LEDGER_CHANGED,
];

export const STAFF_PAGE_EVENTS: LiveEventType[] = [LIVE_EVENTS.STAFF_CHANGED];

export function parseLiveEvent(data: string): LiveEvent | null {
  try {
    const parsed = JSON.parse(data) as Partial<LiveEvent>;
    if (typeof parsed.event !== "string") {
      return null;
    }
    return parsed as LiveEvent;
  } catch {
    return null;
  }
}

export function matchesLiveEvent(
  event: LiveEvent,
  subscribedEvents: readonly LiveEventType[],
): boolean {
  return subscribedEvents.includes(event.event);
}
