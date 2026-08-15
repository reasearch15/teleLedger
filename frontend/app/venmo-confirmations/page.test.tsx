import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VenmoConfirmationsPage from "@/app/venmo-confirmations/page";
import { useLiveUpdates } from "@/components/live-updates-provider";
import {
  createVenmoConfirmation,
  deleteVenmoConfirmation,
  listVenmoConfirmations,
} from "@/services/venmo-confirmations";
import type { VenmoConfirmationListResponse } from "@/types/api";

const mockAuthState = vi.hoisted(() => ({
  role: "staff" as "admin" | "staff",
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: mockAuthState.role === "admin" ? 1 : 42,
      username: mockAuthState.role,
      role: mockAuthState.role,
    },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
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
  deleteVenmoConfirmation: vi.fn(),
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
  has_more: false,
  next_cursor: null,
};

describe("VenmoConfirmationsPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    mockAuthState.role = "staff";
    global.URL.createObjectURL = vi.fn(() => "blob:preview");
    global.URL.revokeObjectURL = vi.fn();
    vi.mocked(useLiveUpdates).mockReset();
    vi.mocked(createVenmoConfirmation).mockReset();
    vi.mocked(deleteVenmoConfirmation).mockReset();
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
    expect(useLiveUpdates).toHaveBeenCalledWith(
      ["venmo_confirmation_updated", "venmo_confirmation_deleted"],
      expect.any(Function),
      true,
    );
  });

  it("renders confirmed and not-received cards with distinct status styling", async () => {
    vi.mocked(listVenmoConfirmations).mockResolvedValueOnce({
      items: [
        {
          ...listResponse.items[0],
          id: 78,
          status: "confirmed",
          confirmed_at: "2026-07-15T16:00:00Z",
        },
        {
          ...listResponse.items[0],
          id: 79,
          status: "not_received",
          confirmed_at: null,
        },
      ],
      has_more: false,
      next_cursor: null,
    });

    render(<VenmoConfirmationsPage />);

    const confirmedLink = await screen.findByRole("link", { name: /Request #78/ });
    const notReceivedLink = await screen.findByRole("link", { name: /Request #79/ });
    expect(confirmedLink.closest("article")).toHaveClass("bg-emerald-50");
    expect(confirmedLink).toHaveTextContent("✅ Confirmed");
    expect(confirmedLink.closest("article")).toHaveTextContent("✓ Confirmed");
    expect(notReceivedLink.closest("article")).toHaveClass("bg-amber-50");
    expect(notReceivedLink).toHaveTextContent("Not received");
  });

  it("shows loading and error states safely", async () => {
    vi.mocked(listVenmoConfirmations).mockRejectedValueOnce(new Error("No access"));
    render(<VenmoConfirmationsPage />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );

    vi.mocked(listVenmoConfirmations).mockResolvedValueOnce({
      items: [],
      has_more: false,
      next_cursor: null,
    });
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
    vi.mocked(listVenmoConfirmations).mockResolvedValueOnce({
      items: [],
      has_more: false,
      next_cursor: null,
    });
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

  it("loads additional confirmation requests in cursor batches", async () => {
    vi.mocked(listVenmoConfirmations)
      .mockResolvedValueOnce({
        items: [listResponse.items[0]],
        has_more: true,
        next_cursor: "2026-07-15T15:45:00Z|77",
      })
      .mockResolvedValueOnce({
        items: [
          {
            ...listResponse.items[0],
            id: 76,
            payment_note: "Older reference",
          },
          {
            ...listResponse.items[0],
            id: 77,
            payment_note: "Duplicate skipped",
          },
        ],
        has_more: false,
        next_cursor: null,
      });

    render(<VenmoConfirmationsPage />);

    expect(await screen.findByText("Request #77")).toBeInTheDocument();
    const loadMore = await screen.findByRole("button", { name: "Load More" });
    fireEvent.click(loadMore);

    expect(await screen.findByText("Request #76")).toBeInTheDocument();
    expect(screen.getAllByText("Request #77")).toHaveLength(1);
    expect(listVenmoConfirmations).toHaveBeenNthCalledWith(1, { limit: 30 });
    expect(listVenmoConfirmations).toHaveBeenNthCalledWith(2, {
      limit: 30,
      cursor: "2026-07-15T15:45:00Z|77",
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Load More" })).not.toBeInTheDocument(),
    );
  });

  it("shows admin delete only for pending requests and removes card after confirm", async () => {
    mockAuthState.role = "admin";
    vi.mocked(deleteVenmoConfirmation).mockResolvedValue(undefined);

    render(<VenmoConfirmationsPage />);

    expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete Request #77?");
    expect(dialog).toHaveTextContent(
      "This removes the request from TeleLedger only. It will not delete any Telegram message.",
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteVenmoConfirmation).toHaveBeenCalledWith(77));
    expect(await screen.findByText("Request #77 deleted.")).toBeInTheDocument();
    expect(screen.queryByText("Request #77")).not.toBeInTheDocument();
    expect(screen.getByText("0 requests")).toBeInTheDocument();
  });

  it("does not show delete for staff users", async () => {
    mockAuthState.role = "staff";
    render(<VenmoConfirmationsPage />);

    expect(await screen.findByText("Request #77")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });
});
