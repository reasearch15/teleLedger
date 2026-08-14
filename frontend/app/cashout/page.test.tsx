import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CashoutPage from "@/app/cashout/page";
import { createCashout, listCashouts } from "@/services/cashouts";
import type { Cashout, CashoutPage as CashoutPageResponse } from "@/types/api";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: 42,
      username: "sarah",
      role: "staff",
      is_active: true,
      staff_color: "#2563EB",
      coadmin_id: 10,
      coadmin_username: "coadmin",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
  }),
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
}));

vi.mock("@/services/cashouts", () => ({
  CASHOUT_PAGE_SIZE: 20,
  listCashouts: vi.fn(),
  createCashout: vi.fn(),
  updateCashoutNotes: vi.fn(),
  completeCashout: vi.fn(),
  cancelCashout: vi.fn(),
  retryCashoutTelegram: vi.fn(),
  listCashoutAudit: vi.fn(),
}));

const createdCashout: Cashout = {
  id: 1,
  request_number: "CR-000001",
  player_tag: "ABC12345",
  amount: "250.00",
  actual_paid_amount: null,
  completion_type: null,
  notes: "VIP Player",
  status: "pending",
  telegram_status: "pending",
  telegram_message_id: null,
  telegram_attempts: 0,
  telegram_sent_at: null,
  telegram_last_error: null,
  created_by_staff_id: 42,
  coadmin_id: 10,
  completed_by_staff_id: null,
  requested_by: {
    id: 42,
    username: "sarah",
    color: "#2563EB",
  },
  completed_by: null,
  created_at: "2026-07-06T20:35:00Z",
  updated_at: "2026-07-06T20:35:00Z",
  completed_at: null,
  cancelled_at: null,
};

const emptyPage: CashoutPageResponse = {
  items: [],
  limit: 20,
  offset: 0,
  has_more: false,
};

function cashoutFixture(overrides: Partial<Cashout>): Cashout {
  return {
    ...createdCashout,
    id: 100,
    request_number: "CR-000100",
    status: "completed",
    telegram_status: "sent",
    actual_paid_amount: "250.00",
    completion_type: "full",
    completed_by_staff_id: 42,
    completed_by: {
      id: 42,
      username: "sarah",
      color: "#2563EB",
    },
    completed_at: "2026-07-06T21:35:00Z",
    ...overrides,
  };
}

describe("CashoutPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.mocked(listCashouts).mockReset();
    vi.mocked(createCashout).mockReset();
    vi.mocked(listCashouts).mockResolvedValue(emptyPage);
  });

  it("validates, trims, and prevents duplicate submissions", async () => {
    let resolveCreate: (cashout: Cashout) => void = () => undefined;
    vi.mocked(createCashout).mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );

    render(<CashoutPage />);
    await waitFor(() => expect(listCashouts).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Player Tag"), {
      target: { value: "  ABC12345  " },
    });
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "250.00" },
    });
    fireEvent.change(screen.getByLabelText("Optional Notes"), {
      target: { value: "  VIP Player  " },
    });
    const submit = screen.getByRole("button", { name: "Submit Cashout" });
    fireEvent.click(submit);
    fireEvent.submit(submit.closest("form")!);

    expect(createCashout).toHaveBeenCalledTimes(1);
    expect(createCashout).toHaveBeenCalledWith(
      expect.objectContaining({
        playerTag: "ABC12345",
        amount: "250.00",
        notes: "VIP Player",
        idempotencyKey: expect.any(String),
      }),
    );
    expect(screen.getByRole("button", { name: "Submitting…" })).toBeDisabled();

    resolveCreate(createdCashout);
    expect(
      await screen.findByText(
        "CR-000001 was created and queued for delivery.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("ABC12345")).toBeInTheDocument();
  });

  it("renders completed full payments with requested, paid, and zero unpaid amounts", async () => {
    vi.mocked(listCashouts).mockResolvedValue({
      ...emptyPage,
      items: [cashoutFixture({ amount: "250.00", actual_paid_amount: "250.00" })],
    });

    render(<CashoutPage />);

    expect(await screen.findByText("Full Payment")).toBeInTheDocument();
    expect(screen.getByText("Requested: $250.00")).toBeInTheDocument();
    expect(screen.getAllByText("Actual paid: $250.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Unpaid difference: $0.00")).toBeInTheDocument();
    expect(screen.getByText("Completed by: sarah")).toBeInTheDocument();
  });

  it("renders completed partial payments without treating the unpaid difference as active", async () => {
    vi.mocked(listCashouts).mockResolvedValue({
      ...emptyPage,
      items: [
        cashoutFixture({
          amount: "100.00",
          actual_paid_amount: "60.00",
          completion_type: "partial",
        }),
      ],
    });

    render(<CashoutPage />);

    expect(await screen.findByText("Partial Payment")).toBeInTheDocument();
    expect(screen.getByText("Requested: $100.00")).toBeInTheDocument();
    expect(screen.getAllByText("Actual paid: $60.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Unpaid difference: $40.00")).toBeInTheDocument();
    expect(screen.getByText("Completion type: Partial Payment")).toBeInTheDocument();
  });

  it("renders legacy completed cashouts using safe full-payment fallbacks", async () => {
    vi.mocked(listCashouts).mockResolvedValue({
      ...emptyPage,
      items: [
        cashoutFixture({
          amount: "125.00",
          actual_paid_amount: null,
          completion_type: null,
          completed_by: null,
        }),
      ],
    });

    render(<CashoutPage />);

    expect(await screen.findByText("Full Payment")).toBeInTheDocument();
    expect(screen.getByText("Requested: $125.00")).toBeInTheDocument();
    expect(screen.getAllByText("Actual paid: $125.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Unpaid difference: $0.00")).toBeInTheDocument();
    expect(screen.getByText("Completed by: Telegram bot")).toBeInTheDocument();
  });
});
