import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PaymentsPage from "@/app/payments/page";
import {
  claimPayment,
  dismissDeclinedPaymentReview,
  dismissPaymentNotOurs,
  listPayments,
} from "@/services/payments";
import type { Payment, PaymentPage } from "@/types/api";

const authState = {
  user: {
    id: 1,
    username: "staff",
    role: "staff" as const,
    is_active: true,
    staff_color: "#2563EB",
    coadmin_id: 10,
    coadmin_username: "owner",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    last_login_at: null,
  },
};

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => authState,
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
}));

vi.mock("@/services/payments", () => ({
  PAYMENT_PAGE_SIZE: 7,
  listPayments: vi.fn(),
  claimPayment: vi.fn(),
  dismissPaymentNotOurs: vi.fn(),
  dismissDeclinedPaymentReview: vi.fn(),
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
    coadmin_dismissals: [],
    all_coadmins_declined_at: null,
    declined_review_dismissed_at: null,
    can_dismiss: false,
    eligible_coadmin_count: 0,
    declined_coadmin_count: 0,
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
    vi.mocked(claimPayment).mockReset();
    vi.mocked(dismissPaymentNotOurs).mockReset();
    vi.mocked(dismissDeclinedPaymentReview).mockReset();
    authState.user = {
      id: 1,
      username: "staff",
      role: "staff",
      is_active: true,
      staff_color: "#2563EB",
      coadmin_id: 10,
      coadmin_username: "owner",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    };
    window.localStorage.clear();
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
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

  it("dismisses Not Ours through the backend and refreshes the active list", async () => {
    vi.mocked(listPayments)
      .mockResolvedValueOnce(
        page([payment(42)], {
          total: 1,
          offset: 0,
          hasMore: false,
        }),
      )
      .mockResolvedValueOnce(
        page([], {
          total: 0,
          offset: 0,
          hasMore: false,
        }),
      );
    vi.mocked(dismissPaymentNotOurs).mockResolvedValueOnce(undefined);

    render(<PaymentsPage />);

    expect(await screen.findByText("Krista R")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Not Ours" }));

    await waitFor(() => expect(dismissPaymentNotOurs).toHaveBeenCalledWith(42));
    await waitFor(() => {
      expect(screen.queryByText("Krista R")).not.toBeInTheDocument();
    });
    expect(window.confirm).toHaveBeenCalledWith(
      "Mark this payment as Not Ours for your coadmin team?",
    );
    expect(claimPayment).not.toHaveBeenCalled();
  });

  it("shows Dismiss for admin when can_dismiss is true and refreshes after dismiss", async () => {
    authState.user = {
      ...authState.user,
      id: 99,
      username: "admin",
      role: "admin",
      coadmin_id: null,
      coadmin_username: null,
    };

    vi.mocked(listPayments)
      .mockResolvedValueOnce(
        page(
          [
            {
              ...payment(42),
              can_dismiss: true,
              eligible_coadmin_count: 1,
              declined_coadmin_count: 1,
              coadmin_dismissals: [
                {
                  coadmin_id: 10,
                  coadmin_username: "charlie",
                  dismissed_by_staff_id: 1,
                  dismissed_by_staff_username: "bella",
                  created_at: "2026-06-29T15:08:00Z",
                },
              ],
            },
          ],
          {
            total: 1,
            offset: 0,
            hasMore: false,
          },
        ),
      )
      .mockResolvedValueOnce(
        page([], {
          total: 0,
          offset: 0,
          hasMore: false,
        }),
      );
    vi.mocked(dismissDeclinedPaymentReview).mockResolvedValueOnce(undefined);

    render(<PaymentsPage />);

    expect(await screen.findByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    await waitFor(() =>
      expect(dismissDeclinedPaymentReview).toHaveBeenCalledWith(42),
    );
    await waitFor(() => expect(listPayments).toHaveBeenCalledTimes(2));
  });

  it("does not show Dismiss for admin when can_dismiss is false", async () => {
    authState.user = {
      ...authState.user,
      id: 99,
      username: "admin",
      role: "admin",
      coadmin_id: null,
      coadmin_username: null,
    };

    vi.mocked(listPayments).mockResolvedValueOnce(
      page([payment(42)], {
        total: 1,
        offset: 0,
        hasMore: false,
      }),
    );

    render(<PaymentsPage />);

    expect(await screen.findByText("Krista R")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });
});
