import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PaymentHistoryPage from "@/app/payment-history/page";
import { listPaymentHistory, reopenPayment } from "@/services/payments";
import type { Payment, PaymentPage } from "@/types/api";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      username: "admin",
      role: "admin",
      is_active: true,
      staff_color: "#7C3AED",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
  }),
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
}));

vi.mock("@/services/payments", () => ({
  PAYMENT_HISTORY_PAGE_SIZE: 20,
  listPaymentHistory: vi.fn(),
  listMyPaymentHistory: vi.fn(),
  reopenPayment: vi.fn(),
}));

const donePayment: Payment = {
  id: 12,
  telegram_message_id: 120,
  recipient_tag: "PLAYER-12",
  amount: "125.00",
  payment_sender_name: "Sender",
  payment_datetime: "2026-07-06T20:00:00Z",
  total_in: null,
  total_out: null,
  status: "done",
  claimed_by_staff_id: 42,
  claimed_at: "2026-07-06T20:01:00Z",
  completed_by_staff_id: 42,
  completed_at: "2026-07-06T20:02:00Z",
  claimed_by_staff: {
    id: 42,
    username: "sarah",
    color: "#EA580C",
  },
  completed_by_staff: {
    id: 42,
    username: "sarah",
    color: "#EA580C",
  },
  parser_confidence: 100,
  created_at: "2026-07-06T20:00:00Z",
  updated_at: "2026-07-06T20:02:00Z",
};

const historyPage: PaymentPage = {
  items: [donePayment],
  total: null,
  limit: 20,
  offset: 0,
  has_more: false,
};

describe("PaymentHistoryPage admin actions", () => {
  beforeEach(() => {
    vi.mocked(listPaymentHistory).mockReset();
    vi.mocked(reopenPayment).mockReset();
    vi.mocked(listPaymentHistory).mockResolvedValue(historyPage);
    vi.mocked(reopenPayment).mockResolvedValue({
      ...donePayment,
      status: "pending",
      claimed_by_staff_id: null,
      claimed_at: null,
      completed_by_staff_id: null,
      completed_at: null,
    });
  });

  it("reopens a done payment and removes it from history", async () => {
    render(<PaymentHistoryPage />);

    const reopen = await screen.findByRole("button", {
      name: "Put Back Pending",
    });
    fireEvent.click(reopen);

    await waitFor(() => expect(reopenPayment).toHaveBeenCalledWith(12));
    await waitFor(() =>
      expect(screen.queryByText("PLAYER-12")).not.toBeInTheDocument(),
    );
  });
});
