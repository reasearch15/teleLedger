import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VenmoConfirmationDetailPage from "@/app/venmo-confirmations/[requestId]/page";
import {
  confirmVenmoAttempt,
  dismissVenmoInquiry,
  getVenmoConfirmation,
  markVenmoAttemptNotReceived,
  resendVenmoConfirmation,
} from "@/services/venmo-confirmations";
import type { VenmoConfirmationDetail } from "@/types/api";

const mockState = vi.hoisted(() => ({
  role: "staff" as "admin" | "staff",
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: mockState.role === "admin" ? 1 : 42,
      username: mockState.role,
      role: mockState.role,
      is_active: true,
      staff_color: "#2563EB",
      coadmin_id: mockState.role === "staff" ? 10 : null,
      coadmin_username: mockState.role === "staff" ? "coadmin" : null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
  }),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ requestId: "77" }),
}));

vi.mock("@/services/venmo-confirmations", () => ({
  getVenmoConfirmation: vi.fn(),
  confirmVenmoAttempt: vi.fn(),
  markVenmoAttemptNotReceived: vi.fn(),
  dismissVenmoInquiry: vi.fn(),
  resendVenmoConfirmation: vi.fn(),
}));

const detail: VenmoConfirmationDetail = {
  id: 77,
  coadmin_id: 10,
  requested_by_staff_id: 42,
  requested_by_username: "sarah",
  coadmin_username: "coadmin",
  screenshot_media_asset_id: 5,
  status: "pending",
  payment_note: "Reference ABC",
  metadata: null,
  confirmed_at: null,
  confirmed_by_display_name: null,
  created_at: "2026-07-15T15:45:00Z",
  updated_at: "2026-07-15T15:45:00Z",
  media: {
    id: 5,
    original_filename: "venmo.png",
    mime_type: "image/png",
    size_bytes: 2048,
    created_at: "2026-07-15T15:45:00Z",
    preview_url: "/api/venmo-confirmations/media/5",
  },
  attempts: [
    {
      id: 11,
      request_id: 77,
      attempt_number: 1,
      telegram_chat_id: 123,
      telegram_message_id: 456,
      status: "posted",
      created_at: "2026-07-15T15:45:00Z",
      posted_at: "2026-07-15T15:46:00Z",
      resolved_at: null,
      last_error: null,
    },
  ],
  inquiries: [
    {
      id: 12,
      request_id: 77,
      source_attempt_id: 11,
      resulting_attempt_id: null,
      status: "open",
      created_at: "2026-07-15T15:50:00Z",
      dismissed_at: null,
      dismissed_by_staff_id: null,
      resent_at: null,
      resent_by_staff_id: null,
    },
  ],
  events: [
    {
      id: 13,
      request_id: 77,
      attempt_id: 11,
      inquiry_id: null,
      event_type: "attempt_posted",
      actor_user_id: null,
      actor_username: null,
      actor_source: "telegram_bot",
      actor_identifier: null,
      payload: null,
      created_at: "2026-07-15T15:46:00Z",
    },
  ],
};

describe("VenmoConfirmationDetailPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    mockState.role = "staff";
    vi.mocked(getVenmoConfirmation).mockReset();
    vi.mocked(confirmVenmoAttempt).mockReset();
    vi.mocked(markVenmoAttemptNotReceived).mockReset();
    vi.mocked(dismissVenmoInquiry).mockReset();
    vi.mocked(resendVenmoConfirmation).mockReset();
    vi.mocked(getVenmoConfirmation).mockResolvedValue(detail);
    vi.mocked(confirmVenmoAttempt).mockResolvedValue({
      ...detail,
      status: "confirmed",
      confirmed_at: "2026-07-15T15:55:00Z",
      confirmed_by_display_name: "sarah",
      attempts: [{ ...detail.attempts[0], status: "confirmed" }],
    });
  });

  it("renders media evidence, attempts, inquiries, and event history", async () => {
    render(<VenmoConfirmationDetailPage />);

    expect(await screen.findByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("venmo.png")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open image" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:8000/api/venmo-confirmations/media/5",
    );
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText(/Inquiry #12/)).toBeInTheDocument();
    expect(screen.getByText("attempt posted")).toBeInTheDocument();
  });

  it("keeps Venmo mutation buttons safe during duplicate clicks", async () => {
    let resolveConfirm: (value: VenmoConfirmationDetail) => void = () => undefined;
    vi.mocked(confirmVenmoAttempt).mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );
    render(<VenmoConfirmationDetailPage />);

    const confirm = await screen.findByRole("button", { name: "Confirm" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(confirmVenmoAttempt).toHaveBeenCalledTimes(1));
    expect(confirm).toBeDisabled();

    resolveConfirm({
      ...detail,
      status: "confirmed",
      attempts: [{ ...detail.attempts[0], status: "confirmed" }],
    });
    expect((await screen.findAllByText("Confirmed")).length).toBeGreaterThan(0);
  });

  it("does not expose backend-rejected action buttons to admins", async () => {
    mockState.role = "admin";

    render(<VenmoConfirmationDetailPage />);

    await screen.findByText("Pending");
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request resend" })).toBeDisabled();
  });
});
