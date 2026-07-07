import { apiRequest } from "@/lib/api-client";
import type {
  Cashout,
  CashoutAudit,
  CashoutFilters,
  CashoutPage,
} from "@/types/api";

export const CASHOUT_PAGE_SIZE = 20;

type CashoutPagination = {
  limit?: number;
  offset?: number;
};

export function listCashouts(
  filters: CashoutFilters = {},
  pagination: CashoutPagination = {},
): Promise<CashoutPage> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.telegramStatus) {
    query.set("telegram_status", filters.telegramStatus);
  }
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  query.set("limit", String(pagination.limit ?? CASHOUT_PAGE_SIZE));
  query.set("offset", String(pagination.offset ?? 0));
  return apiRequest<CashoutPage>(`/api/cashouts?${query.toString()}`);
}

export function createCashout(input: {
  playerTag: string;
  amount: string;
  notes?: string;
  idempotencyKey: string;
}): Promise<Cashout> {
  return apiRequest<Cashout>("/api/cashouts", {
    method: "POST",
    body: JSON.stringify({
      player_tag: input.playerTag,
      amount: input.amount,
      notes: input.notes || null,
      idempotency_key: input.idempotencyKey,
    }),
  });
}

export function updateCashoutNotes(
  cashoutId: number,
  notes: string,
): Promise<Cashout> {
  return apiRequest<Cashout>(`/api/cashouts/${cashoutId}/notes`, {
    method: "PATCH",
    body: JSON.stringify({ notes }),
  });
}

export function completeCashout(cashoutId: number): Promise<Cashout> {
  return apiRequest<Cashout>(`/api/cashouts/${cashoutId}/complete`, {
    method: "POST",
  });
}

export function cancelCashout(cashoutId: number): Promise<Cashout> {
  return apiRequest<Cashout>(`/api/cashouts/${cashoutId}/cancel`, {
    method: "POST",
  });
}

export function retryCashoutTelegram(cashoutId: number): Promise<Cashout> {
  return apiRequest<Cashout>(`/api/cashouts/${cashoutId}/retry-telegram`, {
    method: "POST",
  });
}

export function listCashoutAudit(cashoutId: number): Promise<CashoutAudit[]> {
  return apiRequest<CashoutAudit[]>(`/api/cashouts/${cashoutId}/audit`);
}
