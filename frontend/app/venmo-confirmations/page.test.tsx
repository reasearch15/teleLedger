import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VenmoConfirmationsPage from "@/app/venmo-confirmations/page";
import {
  createVenmoConfirmation,
  listVenmoConfirmations,
} from "@/services/venmo-confirmations";
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
  createVenmoConfirmation: vi.fn(),
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
    global.URL.createObjectURL = vi.fn(() => "blob:preview");
    global.URL.revokeObjectURL = vi.fn();
    vi.mocked(createVenmoConfirmation).mockReset();
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
    expect(screen.getByRole("link", { name: "New Confirmation" })).toHaveAttribute(
      "href",
      "#new-confirmation",
    );
  });

  it("shows New Confirmation on an empty list and submits image evidence", async () => {
    vi.mocked(listVenmoConfirmations).mockResolvedValueOnce({ items: [] });
    vi.mocked(createVenmoConfirmation).mockResolvedValue({
      ...listResponse.items[0],
      id: 88,
      payment_note: "Any provider evidence",
      media: {
        id: 9,
        original_filename: "dog.png",
        mime_type: "image/png",
        size_bytes: 16,
        created_at: "2026-07-15T15:45:00Z",
        preview_url: "/api/venmo-confirmations/media/9",
      },
      attempts: [],
      inquiries: [],
      events: [],
    });

    render(<VenmoConfirmationsPage />);

    expect(await screen.findByText("No Venmo confirmation requests found.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New Confirmation" })).toBeInTheDocument();

    const file = new File(["png"], "dog.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Evidence image"), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByLabelText("Note / context"), {
      target: { value: "Any provider evidence" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit & Send" }));

    await waitFor(() => expect(createVenmoConfirmation).toHaveBeenCalledWith(
      file,
      "Any provider evidence",
    ));
    expect(await screen.findByText("Confirmation request #88 created.")).toBeInTheDocument();
    expect(await screen.findByText("Request #88")).toBeInTheDocument();
  });
});
