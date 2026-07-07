import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";

const mockState = vi.hoisted(() => ({
  role: "admin" as "admin" | "staff",
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
  useRouter: () => ({ replace: vi.fn() }),
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

describe("AppShell navigation", () => {
  afterEach(() => cleanup());

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
});
