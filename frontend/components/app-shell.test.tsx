import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";
import { listNotifications, markNotificationRead } from "@/services/notifications";

const mockState = vi.hoisted(() => ({
  role: "admin" as "admin" | "staff",
  push: vi.fn(),
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

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ replace: vi.fn(), push: mockState.push }),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    loading: false,
    logout: vi.fn(),
    user: {
      id: mockState.role === "admin" ? 1 : 42,
      username: mockState.role,
      role: mockState.role,
      is_active: true,
      staff_color: "#2563EB",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
  }),
}));

vi.mock("@/components/live-updates-provider", () => ({
  useLiveUpdates: vi.fn(),
}));

vi.mock("@/services/notifications", () => ({
  listNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
}));

describe("AppShell navigation", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    mockState.push.mockReset();
    vi.mocked(listNotifications).mockReset();
    vi.mocked(markNotificationRead).mockReset();
    vi.mocked(listNotifications).mockResolvedValue({
      unread_count: 0,
      items: [],
    });
    vi.mocked(markNotificationRead).mockResolvedValue({
      id: 99,
      recipient_user_id: 42,
      coadmin_id: 10,
      type: "venmo_confirmation_confirmed",
      related_entity_type: "venmo_confirmation_request",
      related_entity_id: 77,
      title: "Venmo confirmed",
      body: "Reference ABC",
      payload: null,
      created_at: "2026-07-15T15:45:00Z",
      read_at: "2026-07-15T15:46:00Z",
      navigation_href: "/venmo-confirmations/77",
    });
  });

  it("shows Ledger navigation for admins", () => {
    mockState.role = "admin";

    render(<AppShell title="Dashboard">content</AppShell>);

    expect(screen.getByRole("link", { name: "Ledger" })).toHaveAttribute(
      "href",
      "/admin/ledger",
    );
  });

  it("hides Ledger navigation for staff", () => {
    mockState.role = "staff";

    render(<AppShell title="Dashboard">content</AppShell>);

    expect(screen.queryByRole("link", { name: "Ledger" })).not.toBeInTheDocument();
  });

  it("uses two-row mobile header with logout in the top row", () => {
    mockState.role = "staff";

    const { container } = render(<AppShell title="Dashboard">content</AppShell>);
    const shell = container.firstElementChild;
    expect(shell).toHaveClass("min-w-0");
    expect(shell?.querySelector(".grid.grid-cols-2")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: "Logout" })).toHaveLength(1);
    expect(screen.getByText("Alerts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Menu/ })).toBeInTheDocument();
  });

  it("keeps unread notifications visible and opens linked records", async () => {
    mockState.role = "staff";
    vi.mocked(listNotifications).mockResolvedValue({
      unread_count: 1,
      items: [
        {
          id: 99,
          recipient_user_id: 42,
          coadmin_id: 10,
          type: "venmo_confirmation_confirmed",
          related_entity_type: "venmo_confirmation_request",
          related_entity_id: 77,
          title: "Venmo confirmed",
          body: "Reference ABC",
          payload: null,
          created_at: "2026-07-15T15:45:00Z",
          read_at: null,
          navigation_href: "/venmo-confirmations/77",
        },
      ],
    });

    render(<AppShell title="Dashboard">content</AppShell>);

    const button = await screen.findByRole("button", {
      name: /Notifications, 1 unread/,
    });
    fireEvent.click(button);

    const panel = screen.getByRole("dialog", { name: "Notifications" });
    expect(panel.className).toMatch(/max-lg:inset-x-4/);
    fireEvent.click(screen.getByText("Venmo confirmed"));

    await waitFor(() => expect(markNotificationRead).toHaveBeenCalledWith(99));
    expect(mockState.push).toHaveBeenCalledWith("/venmo-confirmations/77");
  });
});
