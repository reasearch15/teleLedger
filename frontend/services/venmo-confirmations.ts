import { apiRequest } from "@/lib/api-client";
import type {
  VenmoConfirmationDetail,
  VenmoConfirmationListResponse,
} from "@/types/api";

type ListVenmoConfirmationOptions = {
  limit?: number;
  cursor?: string | null;
};

export function listVenmoConfirmations(
  options: ListVenmoConfirmationOptions = {},
): Promise<VenmoConfirmationListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 30));
  if (options.cursor) {
    params.set("cursor", options.cursor);
  }
  return apiRequest<VenmoConfirmationListResponse>(
    `/api/venmo-confirmations?${params.toString()}`,
  );
}

export function createVenmoConfirmation(
  file: File,
  paymentNote?: string,
): Promise<VenmoConfirmationDetail> {
  const form = new FormData();
  form.append("file", file);
  if (paymentNote?.trim()) {
    form.append("payment_note", paymentNote.trim());
  }
  return apiRequest<VenmoConfirmationDetail>("/api/venmo-confirmations", {
    method: "POST",
    body: form,
  });
}

export function getVenmoConfirmation(
  requestId: number,
): Promise<VenmoConfirmationDetail> {
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/${requestId}`,
  );
}

export function confirmVenmoAttempt(
  attemptId: number,
): Promise<VenmoConfirmationDetail> {
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/attempts/${attemptId}/confirm`,
    { method: "POST" },
  );
}

export function markVenmoAttemptNotReceived(
  attemptId: number,
): Promise<VenmoConfirmationDetail> {
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/attempts/${attemptId}/not-received`,
    { method: "POST" },
  );
}

export function dismissVenmoInquiry(
  inquiryId: number,
): Promise<VenmoConfirmationDetail> {
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/inquiries/${inquiryId}/dismiss`,
    { method: "POST" },
  );
}

export function resendVenmoConfirmation(
  requestId: number,
): Promise<VenmoConfirmationDetail> {
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/${requestId}/resend`,
    { method: "POST" },
  );
}

export function uploadVenmoPaymentScreenshot(
  requestId: number,
  file: File,
): Promise<VenmoConfirmationDetail> {
  const form = new FormData();
  form.append("file", file);
  return apiRequest<VenmoConfirmationDetail>(
    `/api/venmo-confirmations/${requestId}/payment-screenshot`,
    { method: "POST", body: form },
  );
}

export function deleteVenmoConfirmation(requestId: number): Promise<void> {
  return apiRequest<void>(`/api/venmo-confirmations/${requestId}`, {
    method: "DELETE",
  });
}
