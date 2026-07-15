import { apiRequest } from "@/lib/api-client";
import type {
  LedgerCalculationMode,
  LedgerAdjustment,
  LedgerAdjustmentPage,
  LedgerDrilldownResponse,
  LedgerResponse,
  Settlement,
  SettlementPage,
  SettlementStatus,
} from "@/types/api";

export const SETTLEMENT_PAGE_SIZE = 30;
export const ADJUSTMENT_PAGE_SIZE = 30;

type DateFilters = {
  dateFrom?: string;
  dateTo?: string;
  calculationMode?: LedgerCalculationMode;
};

type SettlementFilters = DateFilters & {
  staffId?: number;
  coadminId?: number;
  status?: SettlementStatus | "";
  includeDeleted?: boolean;
  limit?: number;
  offset?: number;
  cursor?: string | null;
};

type AdjustmentFilters = DateFilters & {
  staffId?: number;
  coadminId?: number;
  includeDeleted?: boolean;
  limit?: number;
  offset?: number;
  cursor?: string | null;
};

function appendDateFilters(query: URLSearchParams, filters: DateFilters): void {
  if (filters.dateFrom) query.set("date_from", filters.dateFrom);
  if (filters.dateTo) query.set("date_to", filters.dateTo);
  if (filters.calculationMode) {
    query.set("calculation_mode", filters.calculationMode);
  }
}

function appendHistoryDateFilters(
  query: URLSearchParams,
  filters: DateFilters,
): void {
  if (filters.dateFrom) query.set("from", filters.dateFrom);
  if (filters.dateTo) query.set("to", filters.dateTo);
}

export function getLedger(filters: DateFilters = {}): Promise<LedgerResponse> {
  const query = new URLSearchParams();
  appendDateFilters(query, filters);
  return apiRequest<LedgerResponse>(`/api/admin/ledger?${query.toString()}`);
}

export function getLedgerDrilldown(
  filters: DateFilters & { staffId?: number } = {},
): Promise<LedgerDrilldownResponse> {
  const query = new URLSearchParams();
  appendDateFilters(query, filters);
  if (filters.staffId) query.set("staff_id", String(filters.staffId));
  return apiRequest<LedgerDrilldownResponse>(
    `/api/admin/ledger/drilldown?${query.toString()}`,
  );
}

export function createSettlement(
  staffId: number,
  filters: DateFilters = {},
  notes?: string,
): Promise<Settlement> {
  const query = new URLSearchParams();
  appendDateFilters(query, filters);
  return apiRequest<Settlement>(
    `/api/admin/ledger/staff/${staffId}/settlements?${query.toString()}`,
    {
      method: "POST",
      body: JSON.stringify({ notes: notes || null }),
    },
  );
}

export function createCoadminSettlement(
  coadminId: number,
  filters: DateFilters = {},
  notes?: string,
): Promise<Settlement> {
  const query = new URLSearchParams();
  appendDateFilters(query, filters);
  return apiRequest<Settlement>(
    `/api/admin/ledger/coadmins/${coadminId}/settlements?${query.toString()}`,
    {
      method: "POST",
      body: JSON.stringify({ notes: notes || null }),
    },
  );
}

export function createTotalInAdjustment(
  staffId: number,
  newTotalIn: string,
  reason: string,
): Promise<LedgerAdjustment> {
  return apiRequest<LedgerAdjustment>(
    `/api/admin/ledger/staff/${staffId}/adjustments`,
    {
      method: "POST",
      body: JSON.stringify({ new_total_in: newTotalIn, reason }),
    },
  );
}

export function listLedgerAdjustments(
  filters: AdjustmentFilters = {},
): Promise<LedgerAdjustmentPage> {
  const query = new URLSearchParams();
  appendHistoryDateFilters(query, filters);
  if (filters.staffId) query.set("staff_id", String(filters.staffId));
  if (filters.coadminId) query.set("coadminId", String(filters.coadminId));
  if (filters.includeDeleted) query.set("include_deleted", "true");
  query.set("limit", String(filters.limit ?? ADJUSTMENT_PAGE_SIZE));
  if (filters.cursor) query.set("cursor", filters.cursor);
  else query.set("offset", String(filters.offset ?? 0));
  return apiRequest<LedgerAdjustmentPage>(
    `/api/admin/ledger/adjustments?${query.toString()}`,
  );
}

export function listSettlements(
  filters: SettlementFilters = {},
): Promise<SettlementPage> {
  const query = new URLSearchParams();
  appendHistoryDateFilters(query, filters);
  if (filters.staffId) query.set("staff_id", String(filters.staffId));
  if (filters.coadminId) query.set("coadminId", String(filters.coadminId));
  if (filters.status) query.set("status", filters.status);
  if (filters.includeDeleted) query.set("include_deleted", "true");
  query.set("limit", String(filters.limit ?? SETTLEMENT_PAGE_SIZE));
  if (filters.cursor) query.set("cursor", filters.cursor);
  else query.set("offset", String(filters.offset ?? 0));
  return apiRequest<SettlementPage>(`/api/admin/settlements?${query.toString()}`);
}

export function claimSettlement(settlementId: number): Promise<Settlement> {
  return apiRequest<Settlement>(`/api/admin/settlements/${settlementId}/claim`, {
    method: "POST",
  });
}

export function completeSettlement(settlementId: number): Promise<Settlement> {
  return apiRequest<Settlement>(`/api/admin/settlements/${settlementId}/done`, {
    method: "POST",
  });
}

export function cancelSettlement(settlementId: number): Promise<Settlement> {
  return apiRequest<Settlement>(`/api/admin/settlements/${settlementId}/cancel`, {
    method: "POST",
  });
}
