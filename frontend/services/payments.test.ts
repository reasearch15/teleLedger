import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/lib/api-client";
import { listPayments } from "@/services/payments";
import type { PaymentPage } from "@/types/api";

vi.mock("@/lib/api-client", () => ({
  apiRequest: vi.fn(),
}));

const page: PaymentPage = {
  items: [],
  total: 0,
  limit: 7,
  offset: 0,
  has_more: false,
};

describe("listPayments", () => {
  beforeEach(() => {
    vi.mocked(apiRequest).mockReset();
  });

  it("coalesces repeated in-flight requests from a single page load", async () => {
    let resolveRequest: (value: PaymentPage) => void = () => undefined;
    const pendingRequest = new Promise<PaymentPage>((resolve) => {
      resolveRequest = resolve;
    });
    vi.mocked(apiRequest).mockReturnValue(pendingRequest);

    const firstRequest = listPayments();
    const repeatedRequest = listPayments();

    expect(apiRequest).toHaveBeenCalledTimes(1);
    resolveRequest(page);
    await expect(Promise.all([firstRequest, repeatedRequest])).resolves.toEqual([
      page,
      page,
    ]);
  });

  it("sends filters with the requested page offset", async () => {
    vi.mocked(apiRequest).mockResolvedValue(page);

    await listPayments(
      {
        search: "Krista",
        status: "pending",
        dateFrom: "2026-06-01",
        dateTo: "2026-06-30",
      },
      { limit: 7, offset: 14 },
    );

    expect(apiRequest).toHaveBeenCalledWith(
      "/api/payments?status=pending&search=Krista&date_from=2026-06-01&date_to=2026-06-30&limit=7&offset=14",
    );
  });
});
