import { apiRequest } from "@/lib/api-client";
import type {
  VenmoConfirmationDetail,
  VenmoConfirmationListResponse,
} from "@/types/api";

export function listVenmoConfirmations(): Promise<VenmoConfirmationListResponse> {
  return apiRequest<VenmoConfirmationListResponse>("/api/venmo-confirmations");
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
