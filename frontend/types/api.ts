export type UserRole = "admin" | "coadmin" | "staff";

export type User = {
  id: number;
  username: string;
  role: UserRole;
  is_active: boolean;
  staff_color: string;
  coadmin_id: number | null;
  coadmin_username: string | null;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
};

export type PaymentStatus = "pending" | "in_progress" | "done";

export type StaffIdentity = {
  id: number;
  username: string;
  color: string;
};

export type Payment = {
  id: number;
  telegram_message_id: number;
  recipient_tag: string;
  amount: string;
  payment_sender_name: string;
  payment_datetime: string | null;
  total_in: string | null;
  total_out: string | null;
  status: PaymentStatus;
  claimed_by_staff_id: number | null;
  claimed_at: string | null;
  completed_by_staff_id: number | null;
  completed_at: string | null;
  claimed_by_staff: StaffIdentity | null;
  completed_by_staff: StaffIdentity | null;
  coadmin_dismissals: Array<{
    coadmin_id: number;
    coadmin_username: string | null;
    dismissed_by_staff_id: number | null;
    dismissed_by_staff_username: string | null;
    created_at: string;
  }>;
  all_coadmins_declined_at: string | null;
  declined_review_dismissed_at: string | null;
  can_dismiss: boolean;
  eligible_coadmin_count: number;
  declined_coadmin_count: number;
  parser_confidence: number;
  created_at: string;
  updated_at: string;
};

export type PaymentAuditAction =
  | "created"
  | "claimed"
  | "unclaimed"
  | "done"
  | "reopened"
  | "reassigned";

export type PaymentAudit = {
  id: number;
  payment_event_id: number;
  actor_user_id: number | null;
  actor_username: string | null;
  subject_staff_id: number | null;
  subject_username: string | null;
  action: PaymentAuditAction;
  from_status: PaymentStatus | null;
  to_status: PaymentStatus;
  created_at: string;
};

export type PaymentPage = {
  items: Payment[];
  total: number | null;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type PaymentFilters = {
  status?: PaymentStatus | "";
  search?: string;
  dateFrom?: string;
  dateTo?: string;
  activeOnly?: boolean;
};

export type CashoutStatus =
  | "pending"
  | "sent"
  | "completed"
  | "cancelled"
  | "failed_to_send";

export type CashoutTelegramStatus = "pending" | "sent" | "failed_to_send";

export type InquiryMessage = {
  id: number;
  sender_alias: string | null;
  text: string | null;
  caption: string | null;
  message_date: string;
  edited_at: string | null;
  direction: "inbound" | "outbound";
  message_source: "telegram_external" | "inquiry" | "cashout_panel";
  media_type: "none" | "photo" | "document";
  media_mime_type: string | null;
  media_filename: string | null;
  media_size_bytes: number | null;
  media_download_status:
    | "not_applicable"
    | "pending"
    | "ready"
    | "failed";
  media_error: string | null;
  has_media: boolean;
  has_album: boolean;
  is_reply: boolean;
  is_deleted: boolean;
  sent_by_name: string | null;
  starts_new_sender_block: boolean;
  is_edited: boolean;
};

export type InquiryMessagePage = {
  items: InquiryMessage[];
  has_more?: boolean;
  next_cursor?: string | null;
  pagination: {
    hasMore: boolean;
    nextCursor: string | null;
  };
};

export type SendInquiryResult = {
  message: InquiryMessage;
};

export type CashoutStaff = {
  id: number;
  username: string;
  color: string;
};

export type Cashout = {
  id: number;
  request_number: string;
  player_tag: string;
  amount: string;
  notes: string | null;
  status: CashoutStatus;
  telegram_status: CashoutTelegramStatus;
  telegram_message_id: number | null;
  telegram_chat_id?: number | null;
  telegram_attempts: number;
  telegram_sent_at: string | null;
  telegram_last_error: string | null;
  created_by_staff_id: number;
  completed_by_staff_id: number | null;
  requested_by: CashoutStaff | null;
  completed_by: CashoutStaff | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  cancelled_at: string | null;
};

export type CashoutPage = {
  items: Cashout[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type CashoutAuditAction =
  | "created"
  | "telegram_sent"
  | "telegram_retry"
  | "telegram_reaction_completed"
  | "completed"
  | "cancelled"
  | "edited_notes";

export type CashoutAudit = {
  id: number;
  cashout_request_id: number;
  action: CashoutAuditAction;
  actor_user_id: number | null;
  actor_username: string | null;
  previous_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  created_at: string;
};

export type CashoutFilters = {
  status?: CashoutStatus | "";
  telegramStatus?: CashoutTelegramStatus | "";
  search?: string;
};

export type LedgerItem = {
  staff_id: number;
  staff_username: string;
  staff_color: string;
  coadmin_id: number | null;
  coadmin_username: string;
  payment_total: string;
  adjustment_total: string;
  total_in: string;
  total_out: string;
  settled_amount: string;
  net: string;
  payments_count: number;
  cashouts_count: number;
  settlements_count: number;
};

export type CoadminLedgerSummary = {
  coadmin_id: number | null;
  coadmin_username: string;
  payment_total: string;
  adjustment_total: string;
  total_in: string;
  total_out: string;
  settled_amount: string;
  net: string;
  staff_count: number;
  payments_count: number;
  cashouts_count: number;
  settlements_count: number;
};

export type LedgerSummary = {
  payment_total: string;
  adjustment_total: string;
  total_in: string;
  total_out: string;
  settled_amount: string;
  net: string;
};

export type LedgerResponse = {
  items: LedgerItem[];
  coadmin_summaries: CoadminLedgerSummary[];
  summary: LedgerSummary;
  calculation_type: "open_balance" | "custom_range" | "rolling_activity";
  timezone: string;
  period_start: string | null;
  period_end: string | null;
  includes_settled: boolean;
  rolling_hours: number | null;
  generated_at: string | null;
};

export type LedgerCalculationMode =
  | "open_balance"
  | "last_12_hours"
  | "custom_range";

export type LedgerPaymentDrilldown = {
  id: number;
  staff_id: number;
  staff_username: string;
  amount: string;
  status: string;
  completed_at: string | null;
  settlement_id: number | null;
  recipient_tag: string;
  payment_sender_name: string;
};

export type LedgerCashoutDrilldown = {
  id: number;
  staff_id: number;
  staff_username: string;
  amount: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  settlement_id: number | null;
  player_tag: string;
  request_number: string | null;
};

export type LedgerAdjustmentDrilldown = {
  id: number;
  staff_id: number;
  staff_username: string;
  amount_delta: string;
  created_at: string;
  settlement_id: number | null;
  reason: string;
};

export type LedgerDrilldownResponse = {
  payments: LedgerPaymentDrilldown[];
  cashouts: LedgerCashoutDrilldown[];
  adjustments: LedgerAdjustmentDrilldown[];
  calculation_type: "custom_range" | "rolling_activity";
  timezone: string;
  period_start: string | null;
  period_end: string | null;
  includes_settled: boolean;
  rolling_hours: number | null;
  generated_at: string | null;
};

export type LedgerAdjustment = {
  id: number;
  staff_id: number | null;
  staff_username: string;
  staff_color: string;
  type: "total_in_adjustment";
  amount_delta: string;
  previous_total_in: string;
  new_total_in: string;
  reason: string;
  created_by_admin_id: number | null;
  created_by_admin_username: string | null;
  settlement_id: number | null;
  created_at: string;
};

export type LedgerAdjustmentPage = {
  items: LedgerAdjustment[];
  rows: LedgerAdjustment[];
  limit: number;
  offset: number;
  has_more: boolean;
  hasMore: boolean;
  nextCursor: string | null;
};

export type SettlementStatus = "pending" | "claimed" | "done" | "cancelled";

export type Settlement = {
  id: number;
  staff_id: number | null;
  staff_username: string;
  staff_color: string;
  coadmin_id: number | null;
  coadmin_username: string | null;
  scope: "staff" | "coadmin";
  amount: string;
  status: SettlementStatus;
  claimed_by_admin_id: number | null;
  claimed_by_admin_username: string | null;
  claimed_at: string | null;
  completed_by_admin_id: number | null;
  completed_by_admin_username: string | null;
  completed_at: string | null;
  created_by_admin_id: number;
  created_by_admin_username: string;
  created_at: string;
  updated_at: string;
  notes: string | null;
  payment_ids: number[];
  cashout_ids: number[];
  adjustment_ids: number[];
};

export type SettlementPage = {
  items: Settlement[];
  rows: Settlement[];
  limit: number;
  offset: number;
  has_more: boolean;
  hasMore: boolean;
  nextCursor: string | null;
};
