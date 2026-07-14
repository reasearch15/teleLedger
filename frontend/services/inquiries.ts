import { environment } from "@/lib/env";
import type { InquiryMessage, InquiryMessagePage, SendInquiryResult } from "@/types/api";

export const INQUIRY_PAGE_SIZE = 40;

export async function listInquiryMessages(options?: {
  limit?: number;
  cursor?: string | null;
}): Promise<InquiryMessagePage> {
  const params = new URLSearchParams();
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.cursor) params.set("cursor", options.cursor);
  const query = params.toString();
  const response = await fetch(
    `${environment.apiUrl}/api/inquiries/messages${query ? `?${query}` : ""}`,
    { credentials: "include", headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    throw new Error(`Failed to load inquiry messages (${response.status})`);
  }
  return (await response.json()) as InquiryMessagePage;
}

export async function sendInquiryMessage(input: {
  text?: string;
  image?: File | null;
  idempotencyKey: string;
}): Promise<SendInquiryResult> {
  const form = new FormData();
  form.set("idempotency_key", input.idempotencyKey);
  if (input.text?.trim()) form.set("text", input.text.trim());
  if (input.image) form.append("image", input.image);

  const response = await fetch(`${environment.apiUrl}/api/inquiries/send`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (!response.ok) {
    let detail = `Failed to send inquiry message (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep fallback detail.
    }
    throw new Error(detail);
  }
  return (await response.json()) as SendInquiryResult;
}

export function inquiryMediaUrl(messageId: number): string {
  return `${environment.apiUrl}/api/inquiries/messages/${messageId}/media`;
}

export async function fetchInquiryMediaBlob(messageId: number): Promise<string> {
  const response = await fetch(inquiryMediaUrl(messageId), { credentials: "include" });
  if (!response.ok) {
    throw new Error("Media unavailable");
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}
