import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminLedgerPage from "@/app/admin/ledger/page";
import { getLedger, getLedgerDrilldown } from "@/services/ledger";
import type { LedgerDrilldownResponse, LedgerResponse } from "@/types/api";

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
  getLedgerDrilldown: vi.fn(),
  listSettlements: vi.fn(),
  listLedgerAdjustments: vi.fn(),
  createCoadminSettlement: vi.fn(),
  createSettlement: vi.fn(),
  createTotalInAdjustment: vi.fn(),
  claimSettlement: vi.fn(),
  completeSettlement: vi.fn(),
  cancelSettlement: vi.fn(),
}));

vi.mock("@/services/staff", () => ({
  listCoadmins: vi.fn().mockResolvedValue([]),
}));

const ledgerBefore: LedgerResponse = {
  items: [
    {
      staff_id: 42,
      staff_username: "Sarah",
      staff_color: "#2563EB",
      coadmin_id: 10,
      coadmin_username: "default_coadmin",
      payment_total: "1000.00",
      adjustment_total: "0.00",
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
      payment_total: "100.00",
      adjustment_total: "0.00",
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
      payment_total: "1000.00",
      adjustment_total: "0.00",
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
      payment_total: "100.00",
      adjustment_total: "0.00",
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
    payment_total: "1100.00",
    adjustment_total: "0.00",
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
  rolling_hours: null,
  generated_at: null,
};

const partialCashoutDrilldown: LedgerDrilldownResponse = {
  payments: [],
  cashouts: [
    {
      id: 7,
      staff_id: 42,
      staff_username: "Sarah",
      amount: "100.00",
      requested_amount: "100.00",
      actual_paid_amount: "60.00",
      unpaid_difference: "40.00",
      completion_type: "partial",
      status: "completed",
      created_at: "2026-07-15T14:00:00Z",
      completed_at: "2026-07-15T15:00:00Z",
      settlement_id: null,
      player_tag: "PLAYER1",
      request_number: "CR-000007",
    },
  ],
  adjustments: [],
  calculation_type: "rolling_activity",
  timezone: "Asia/Kathmandu",
  period_start: "2026-07-15T09:30:00+05:45",
  period_end: "2026-07-15T21:30:00+05:45",
  includes_settled: true,
  rolling_hours: 12,
  generated_at: "2026-07-15T15:45:00Z",
};

describe("AdminLedgerPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/ledger");
    vi.mocked(getLedger).mockReset();
    vi.mocked(getLedgerDrilldown).mockReset();
    vi.mocked(getLedger).mockResolvedValue(ledgerBefore);
    vi.mocked(getLedgerDrilldown).mockResolvedValue({
      payments: [],
      cashouts: [],
      adjustments: [],
      calculation_type: "rolling_activity",
      timezone: "Asia/Kathmandu",
      period_start: "2026-07-15T09:30:00+05:45",
      period_end: "2026-07-15T21:30:00+05:45",
      includes_settled: true,
      rolling_hours: 12,
      generated_at: "2026-07-15T15:45:00Z",
    });
  });

  it("renders ledger summary totals", async () => {
    render(<AdminLedgerPage />);

    expect((await screen.findAllByText("Current Open Balance")).length).toBeGreaterThan(
      0,
    );
    expect((await screen.findAllByText("$1,100.00")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("$400.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$700.00").length).toBeGreaterThan(0);
    expect(
      await screen.findByText("Uses actual paid cashout amounts."),
    ).toBeInTheDocument();
  });

  it("shows partial cashout actual paid amount as Total Out in drilldown", async () => {
    vi.mocked(getLedgerDrilldown).mockResolvedValue(partialCashoutDrilldown);
    render(<AdminLedgerPage />);

    fireEvent.click(screen.getByRole("button", { name: "Last 12 Hours" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    const cashoutPanel = await screen.findByText("Cashouts");
    const table = cashoutPanel.closest("section");
    expect(table).not.toBeNull();
    expect(within(table as HTMLElement).getByText("Total Out")).toBeInTheDocument();
    expect(within(table as HTMLElement).getByText("$60.00")).toBeInTheDocument();
    expect(within(table as HTMLElement).getByText("$100.00")).toBeInTheDocument();
    expect(within(table as HTMLElement).getByText("$40.00")).toBeInTheDocument();
    expect(within(table as HTMLElement).getByText("Partial Payment")).toBeInTheDocument();
  });
});
