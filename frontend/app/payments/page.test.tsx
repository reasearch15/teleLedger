import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PaymentsPage from "@/app/payments/page";
import { listPayments } from "@/services/payments";
import type { Payment, PaymentPage } from "@/types/api";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      username: "staff",
      role: "staff",
      is_active: true,
      staff_color: "#2563EB",
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
  PAYMENT_PAGE_SIZE: 7,
  listPayments: vi.fn(),
  claimPayment: vi.fn(),
  unclaimPayment: vi.fn(),
  markPaymentDone: vi.fn(),
  forceUnclaimPayment: vi.fn(),
  reopenPayment: vi.fn(),
  assignPayment: vi.fn(),
  listPaymentAudit: vi.fn(),
}));

function payment(id: number): Payment {
  return {
    id,
    telegram_message_id: id,
    recipient_tag: "Stephen_Mckinney_21",
    amount: "36.28",
    payment_sender_name: "Krista R",
    payment_datetime: "2026-06-29T15:08:00",
    total_in: "5709.59",
    total_out: "1881.66",
    status: "pending",
    claimed_by_staff_id: null,
    claimed_at: null,
    completed_by_staff_id: null,
    completed_at: null,
    claimed_by_staff: null,
    completed_by_staff: null,
    parser_confidence: 100,
    created_at: "2026-06-29T15:08:00Z",
    updated_at: "2026-06-29T15:08:00Z",
  };
}

function page(
  items: Payment[],
  options: {
    total: number;
    offset: number;
    hasMore: boolean;
  },
): PaymentPage {
  return {
    items,
    total: options.total,
    limit: 7,
    offset: options.offset,
    has_more: options.hasMore,
  };
}

describe("PaymentsPage pagination", () => {
  beforeEach(() => {
    vi.mocked(listPayments).mockReset();
  });

  it("fetches once initially, loads the next page, and resets filters to offset zero", async () => {
    vi.mocked(listPayments)
      .mockResolvedValueOnce(
        page(
          [
            payment(8),
            payment(7),
            payment(6),
            payment(5),
            payment(4),
            payment(3),
            payment(2),
          ],
          {
            total: 8,
            offset: 0,
            hasMore: true,
          },
        ),
      )
      .mockResolvedValueOnce(
        page([payment(1)], {
          total: 8,
          offset: 7,
          hasMore: false,
        }),
      )
      .mockResolvedValueOnce(
        page([payment(8)], {
          total: 1,
          offset: 0,
          hasMore: false,
        }),
      );

    render(<PaymentsPage />);

    await waitFor(() => expect(listPayments).toHaveBeenCalledTimes(1));
    expect(listPayments).toHaveBeenNthCalledWith(
      1,
      { activeOnly: true },
      { limit: 7, offset: 0 },
    );

    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(listPayments).toHaveBeenCalledTimes(2));
    expect(listPayments).toHaveBeenNthCalledWith(
      2,
      { activeOnly: true },
      { limit: 7, offset: 7 },
    );

    fireEvent.change(screen.getByPlaceholderText("Recipient, sender, message"), {
      target: { value: "Krista" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));

    await waitFor(() => expect(listPayments).toHaveBeenCalledTimes(3));
    expect(listPayments).toHaveBeenNthCalledWith(
      3,
      { search: "Krista", activeOnly: true },
      { limit: 7, offset: 0 },
    );
  });
});
