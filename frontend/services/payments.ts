import { apiRequest } from "@/lib/api-client";
import type {
  Payment,
  PaymentAudit,
  PaymentFilters,
  PaymentPage,
} from "@/types/api";

export const PAYMENT_PAGE_SIZE = 7;
export const PAYMENT_HISTORY_PAGE_SIZE = 20;

type PaymentPagination = {
  limit?: number;
  offset?: number;
};

const pendingPageRequests = new Map<string, Promise<PaymentPage>>();

export function listPayments(
  filters: PaymentFilters = {},
  pagination: PaymentPagination = {},
): Promise<PaymentPage> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.dateFrom) query.set("date_from", filters.dateFrom);
  if (filters.dateTo) query.set("date_to", filters.dateTo);
  if (filters.activeOnly) query.set("active_only", "true");
  query.set("limit", String(pagination.limit ?? PAYMENT_PAGE_SIZE));
  query.set("offset", String(pagination.offset ?? 0));
  const path = `/api/payments?${query.toString()}`;

  const pendingRequest = pendingPageRequests.get(path);
  if (pendingRequest) return pendingRequest;

  const startedAt = performance.now();
  const request = apiRequest<PaymentPage>(path).finally(() => {
    if (process.env.NODE_ENV === "development") {
      console.debug(
        `[payments] fetch ${path} completed in ${(performance.now() - startedAt).toFixed(1)}ms`,
      );
    }
    if (pendingPageRequests.get(path) === request) {
      pendingPageRequests.delete(path);
    }
  });
  pendingPageRequests.set(path, request);
  return request;
}

export function listMyPaymentHistory(
  pagination: PaymentPagination = {},
): Promise<PaymentPage> {
  const query = new URLSearchParams();
  query.set("limit", String(pagination.limit ?? PAYMENT_HISTORY_PAGE_SIZE));
  query.set("offset", String(pagination.offset ?? 0));
  return apiRequest<PaymentPage>(
    `/api/payments/my-history?${query.toString()}`,
  );
}

export function listPaymentHistory(
  pagination: PaymentPagination = {},
): Promise<PaymentPage> {
  const query = new URLSearchParams();
  query.set("limit", String(pagination.limit ?? PAYMENT_HISTORY_PAGE_SIZE));
  query.set("offset", String(pagination.offset ?? 0));
  return apiRequest<PaymentPage>(`/api/payments/history?${query.toString()}`);
}

export function claimPayment(paymentId: number): Promise<Payment> {
  return apiRequest<Payment>(`/api/payments/${paymentId}/claim`, {
    method: "POST",
  });
}

export function dismissPaymentNotOurs(paymentId: number): Promise<void> {
  return apiRequest<void>(`/api/payments/${paymentId}/not-ours`, {
    method: "POST",
  });
}

export function unclaimPayment(paymentId: number): Promise<Payment> {
  return apiRequest<Payment>(`/api/payments/${paymentId}/unclaim`, {
    method: "POST",
  });
}

export function markPaymentDone(paymentId: number): Promise<Payment> {
  return apiRequest<Payment>(`/api/payments/${paymentId}/done`, {
    method: "POST",
  });
}

export function forceUnclaimPayment(paymentId: number): Promise<Payment> {
  return apiRequest<Payment>(
    `/api/payments/admin/${paymentId}/force-unclaim`,
    { method: "POST" },
  );
}

export function reopenPayment(paymentId: number): Promise<Payment> {
  return apiRequest<Payment>(`/api/payments/admin/${paymentId}/reopen`, {
    method: "POST",
  });
}

export function assignPayment(
  paymentId: number,
  staffId: number,
): Promise<Payment> {
  return apiRequest<Payment>(`/api/payments/admin/${paymentId}/assign`, {
    method: "POST",
    body: JSON.stringify({ staff_id: staffId }),
  });
}

export function listPaymentAudit(paymentId: number): Promise<PaymentAudit[]> {
  return apiRequest<PaymentAudit[]>(
    `/api/payments/admin/${paymentId}/audit`,
  );
}
