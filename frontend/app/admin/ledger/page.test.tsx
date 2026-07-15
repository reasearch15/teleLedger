import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminLedgerPage from "@/app/admin/ledger/page";
import {
  createCoadminSettlement,
  createSettlement,
  createTotalInAdjustment,
  getLedger,
  listLedgerAdjustments,
  listSettlements,
} from "@/services/ledger";
import type {
  LedgerAdjustmentPage,
  LedgerResponse,
  SettlementPage,
} from "@/types/api";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
}));

vi.mock("@/services/ledger", () => ({
  ADJUSTMENT_PAGE_SIZE: 30,
  SETTLEMENT_PAGE_SIZE: 30,
  getLedger: vi.fn(),
  listSettlements: vi.fn(),
  listLedgerAdjustments: vi.fn(),
  createCoadminSettlement: vi.fn(),
  createSettlement: vi.fn(),
  createTotalInAdjustment: vi.fn(),
  claimSettlement: vi.fn(),
  completeSettlement: vi.fn(),
  cancelSettlement: vi.fn(),
}));

const ledgerBefore: LedgerResponse = {
  items: [
    {
      staff_id: 42,
      staff_username: "Sarah",
      staff_color: "#2563EB",
      coadmin_id: 10,
      coadmin_username: "default_coadmin",
      total_in: "1000.00",
      total_out: "300.00",
      settled_amount: "0.00",
      net: "700.00",
      payments_count: 12,
      cashouts_count: 4,
      settlements_count: 0,
    },
    {
      staff_id: 84,
      staff_username: "Alex",
      staff_color: "#16A34A",
      coadmin_id: 11,
      coadmin_username: "coadmin_two",
      total_in: "100.00",
      total_out: "100.00",
      settled_amount: "0.00",
      net: "0.00",
      payments_count: 1,
      cashouts_count: 1,
      settlements_count: 0,
    },
  ],
  coadmin_summaries: [
    {
      coadmin_id: 10,
      coadmin_username: "default_coadmin",
      total_in: "1000.00",
      total_out: "300.00",
      settled_amount: "0.00",
      net: "700.00",
      staff_count: 1,
      payments_count: 12,
      cashouts_count: 4,
      settlements_count: 0,
    },
    {
      coadmin_id: 11,
      coadmin_username: "coadmin_two",
      total_in: "100.00",
      total_out: "100.00",
      settled_amount: "0.00",
      net: "0.00",
      staff_count: 1,
      payments_count: 1,
      cashouts_count: 1,
      settlements_count: 0,
    },
  ],
  summary: {
    total_in: "1100.00",
    total_out: "400.00",
    settled_amount: "0.00",
    net: "700.00",
  },
  calculation_type: "open_balance",
  timezone: "Asia/Kathmandu",
  period_start: null,
  period_end: null,
  includes_settled: false,
};

const ledgerAfter: LedgerResponse = {
  ...ledgerBefore,
  items: [
    {
      ...ledgerBefore.items[0],
      total_in: "0.00",
      total_out: "0.00",
      net: "0.00",
      settlements_count: 1,
    },
    ledgerBefore.items[1],
  ],
  summary: {
    total_in: "100.00",
    total_out: "100.00",
    settled_amount: "0.00",
    net: "0.00",
  },
  coadmin_summaries: [
    {
      ...ledgerBefore.coadmin_summaries[0],
      total_in: "0.00",
      total_out: "0.00",
      net: "0.00",
      settlements_count: 1,
    },
    ledgerBefore.coadmin_summaries[1],
  ],
};

const history: SettlementPage = {
  items: [
    {
      id: 1,
      staff_id: 42,
      staff_username: "Sarah",
      staff_color: "#2563EB",
      coadmin_id: 10,
      coadmin_username: "default_coadmin",
      scope: "staff",
      amount: "700.00",
      status: "done",
      claimed_by_admin_id: null,
      claimed_by_admin_username: null,
      claimed_at: null,
      completed_by_admin_id: 1,
      completed_by_admin_username: "admin",
      completed_at: "2026-07-07T12:00:00Z",
      created_by_admin_id: 1,
      created_by_admin_username: "admin",
      created_at: "2026-07-07T12:00:00Z",
      updated_at: "2026-07-07T12:00:00Z",
      notes: "Weekly",
      payment_ids: [1, 2],
      cashout_ids: [3],
      adjustment_ids: [1],
    },
  ],
  rows: [],
  limit: 30,
  offset: 0,
  has_more: false,
  hasMore: false,
  nextCursor: null,
};
history.rows = history.items;

const adjustments: LedgerAdjustmentPage = {
  items: [
    {
      id: 1,
      staff_id: 42,
      staff_username: "Sarah",
      staff_color: "#2563EB",
      type: "total_in_adjustment",
      amount_delta: "-28.75",
      previous_total_in: "128.75",
      new_total_in: "100.00",
      reason: "Correction",
      created_by_admin_id: 1,
      created_by_admin_username: "admin",
      settlement_id: null,
      created_at: "2026-07-07T12:00:00Z",
    },
  ],
  rows: [],
  limit: 30,
  offset: 0,
  has_more: false,
  hasMore: false,
  nextCursor: null,
};
adjustments.rows = adjustments.items;

describe("AdminLedgerPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.mocked(getLedger).mockReset();
    vi.mocked(listSettlements).mockReset();
    vi.mocked(listLedgerAdjustments).mockReset();
    vi.mocked(createCoadminSettlement).mockReset();
    vi.mocked(createSettlement).mockReset();
    vi.mocked(createTotalInAdjustment).mockReset();
    vi.mocked(getLedger).mockResolvedValue(ledgerBefore);
    vi.mocked(listSettlements).mockResolvedValue(history);
    vi.mocked(listLedgerAdjustments).mockResolvedValue(adjustments);
    vi.mocked(createCoadminSettlement).mockResolvedValue(history.items[0]);
    vi.mocked(createSettlement).mockResolvedValue(history.items[0]);
    vi.mocked(createTotalInAdjustment).mockResolvedValue(adjustments.items[0]);
  });

  it("renders ledger table and settlement history", async () => {
    render(<AdminLedgerPage />);

    expect((await screen.findAllByText("Sarah")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("$1,000.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$300.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$700.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Weekly").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Correction").length).toBeGreaterThan(0);
  });

  it("disables settlement when net is zero", async () => {
    render(<AdminLedgerPage />);

    await screen.findAllByText("Alex");
    const staffPanel = screen.getByText("Staff Current Open Balance").closest("section");
    expect(staffPanel).not.toBeNull();
    const buttons = within(staffPanel as HTMLElement).getAllByRole("button", {
      name: "Settle / Withdraw",
    });

    expect(buttons.some((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("confirms settlement and refreshes ledger to zero", async () => {
    vi.mocked(getLedger)
      .mockResolvedValueOnce(ledgerBefore)
      .mockResolvedValueOnce(ledgerAfter);
    render(<AdminLedgerPage />);

    await screen.findAllByText("Sarah");
    const staffPanel = screen.getByText("Staff Current Open Balance").closest("section");
    expect(staffPanel).not.toBeNull();
    fireEvent.click(
      within(staffPanel as HTMLElement).getAllByRole("button", {
        name: "Settle / Withdraw",
      })[0],
    );
    expect(screen.getByText("Confirm settlement")).toBeInTheDocument();
    expect(screen.getAllByText("$700.00").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(createSettlement).toHaveBeenCalledWith(42, {}));
    await waitFor(() => expect(screen.getAllByText("$0.00").length).toBeGreaterThan(0));
  });

  it("confirms coadmin settlement from the summary", async () => {
    render(<AdminLedgerPage />);

    await screen.findAllByText("default_coadmin");
    const coadminPanel = screen
      .getByText("Coadmin Current Open Balance")
      .closest("section");
    expect(coadminPanel).not.toBeNull();
    fireEvent.click(
      within(coadminPanel as HTMLElement).getAllByRole("button", {
        name: "Settle / Withdraw",
      })[0],
    );
    expect(screen.getByText("Confirm settlement")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(createCoadminSettlement).toHaveBeenCalledWith(10, {}),
    );
  });

  it("opens total in adjustment modal and saves reason", async () => {
    render(<AdminLedgerPage />);

    await screen.findAllByText("Sarah");
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    expect(screen.getAllByText("Edit Total In").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("New Total In"), {
      target: { value: "150.00" },
    });
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "Manual correction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createTotalInAdjustment).toHaveBeenCalledWith(
        42,
        "150.00",
        "Manual correction",
      ),
    );
  });
});
