import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VenmoConfirmationsPage from "@/app/venmo-confirmations/page";
import { listVenmoConfirmations } from "@/services/venmo-confirmations";
import type { VenmoConfirmationListResponse } from "@/types/api";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    className,
  }: {
    children: React.ReactNode;
    href: string;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

vi.mock("@/services/venmo-confirmations", () => ({
  listVenmoConfirmations: vi.fn(),
}));

const listResponse: VenmoConfirmationListResponse = {
  items: [
    {
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
      media: null,
    },
  ],
};

describe("VenmoConfirmationsPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.mocked(listVenmoConfirmations).mockReset();
    vi.mocked(listVenmoConfirmations).mockResolvedValue(listResponse);
  });

  it("renders request cards with links to detail pages", async () => {
    render(<VenmoConfirmationsPage />);

    expect(await screen.findByText("Request #77")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Request #77/ })).toHaveAttribute(
      "href",
      "/venmo-confirmations/77",
    );
    expect(screen.getByText(/Staff: sarah/)).toBeInTheDocument();
    expect(screen.getByText("Reference ABC")).toBeInTheDocument();
  });

  it("shows loading and error states safely", async () => {
    vi.mocked(listVenmoConfirmations).mockRejectedValueOnce(new Error("No access"));
    render(<VenmoConfirmationsPage />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );

    vi.mocked(listVenmoConfirmations).mockResolvedValueOnce({ items: [] });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() =>
      expect(screen.getByText("No Venmo confirmation requests found.")).toBeInTheDocument(),
    );
  });
});
