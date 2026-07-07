"use client";

import { memo, useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import {
  SETTLEMENT_PAGE_SIZE,
  cancelSettlement,
  claimSettlement,
  completeSettlement,
  listSettlements,
} from "@/services/ledger";
import type { Settlement } from "@/types/api";

import {
  EmptyTableRow,
  formatDate,
  formatIds,
  formatMoney,
  LoadMoreButton,
  pageRows,
  Panel,
  TableShell,
  useLedgerFilters,
} from "./shared";

const settlementColors = {
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  claimed: "border-blue-300 bg-blue-50 text-blue-900",
  done: "border-emerald-300 bg-emerald-50 text-emerald-900",
  cancelled: "border-red-300 bg-red-50 text-red-900",
};

export function SettlementHistoryPanel() {
  const filters = useLedgerFilters();
  const [items, setItems] = useState<Settlement[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(
    async (reset = true, nextCursor: string | null = null) => {
      if (reset) setLoading(true);
      else setLoadingMore(true);
      setError("");
      try {
        const page = await listSettlements({
          ...filters,
          limit: SETTLEMENT_PAGE_SIZE,
          cursor: reset ? null : nextCursor,
        });
        const rows = pageRows(page);
        setItems((current) => (reset ? rows : [...current, ...rows]));
        setCursor(page.nextCursor ?? null);
        setHasMore(page.hasMore ?? page.has_more);
      } catch (loadError) {
        setError(friendlyError(loadError));
      } finally {
        if (reset) setLoading(false);
        else setLoadingMore(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    void load(true);
  }, [load]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, () => load(true), true);

  const runSettlementAction = async (
    settlementId: number,
    action: (id: number) => Promise<Settlement>,
  ) => {
    setActionId(settlementId);
    setError("");
    try {
      await action(settlementId);
      await load(true);
    } catch (actionError) {
      setError(friendlyError(actionError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <Panel
      title="Withdrawal / Settlement History"
      description={`Latest ${SETTLEMENT_PAGE_SIZE} settlement and withdrawal records.`}
      error={error}
    >
      <TableShell>
        <table className="w-full min-w-[1400px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-5 py-3">Staff</th>
              <th className="px-5 py-3">Scope</th>
              <th className="px-5 py-3">Coadmin</th>
              <th className="px-5 py-3">Amount</th>
              <th className="px-5 py-3">Status</th>
              <th className="px-5 py-3">Created by</th>
              <th className="px-5 py-3">Completed by</th>
              <th className="px-5 py-3">Created at</th>
              <th className="px-5 py-3">Completed at</th>
              <th className="px-5 py-3">Payments</th>
              <th className="px-5 py-3">Cashouts</th>
              <th className="px-5 py-3">Adjustments</th>
              <th className="px-5 py-3">Notes</th>
              <th className="px-5 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <EmptyTableRow colSpan={14} message="Loading settlements..." />
            ) : null}
            {!loading && items.length === 0 ? (
              <EmptyTableRow colSpan={14} message="No records found." />
            ) : null}
            {items.map((settlement) => (
              <SettlementRow
                key={settlement.id}
                settlement={settlement}
                busy={actionId === settlement.id}
                onAction={runSettlementAction}
              />
            ))}
          </tbody>
        </table>
      </TableShell>
      {hasMore ? (
        <LoadMoreButton loading={loadingMore} onClick={() => void load(false, cursor)} />
      ) : null}
    </Panel>
  );
}

const SettlementRow = memo(function SettlementRow({
  settlement,
  busy,
  onAction,
}: {
  settlement: Settlement;
  busy: boolean;
  onAction: (
    settlementId: number,
    action: (id: number) => Promise<Settlement>,
  ) => Promise<void>;
}) {
  return (
    <tr>
      <td className="px-5 py-3 font-semibold">
        <span
          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: settlement.staff_color || "#64748B" }}
        />
        {settlement.scope === "coadmin"
          ? settlement.coadmin_username ?? "Coadmin"
          : settlement.staff_username || "Deleted Staff"}
      </td>
      <td className="px-5 py-3 capitalize">{settlement.scope}</td>
      <td className="px-5 py-3">{settlement.coadmin_username ?? "-"}</td>
      <td className="px-5 py-3 font-bold">{formatMoney(settlement.amount)}</td>
      <td className="px-5 py-3">
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-bold capitalize ${
            settlementColors[settlement.status]
          }`}
        >
          {settlement.status}
        </span>
      </td>
      <td className="px-5 py-3">{settlement.created_by_admin_username}</td>
      <td className="px-5 py-3">{settlement.completed_by_admin_username ?? "-"}</td>
      <td className="px-5 py-3">{formatDate(settlement.created_at)}</td>
      <td className="px-5 py-3">{formatDate(settlement.completed_at)}</td>
      <td className="px-5 py-3">{formatIds(settlement.payment_ids)}</td>
      <td className="px-5 py-3">{formatIds(settlement.cashout_ids)}</td>
      <td className="px-5 py-3">{formatIds(settlement.adjustment_ids)}</td>
      <td className="px-5 py-3">{settlement.notes ?? "-"}</td>
      <td className="px-5 py-3">
        {settlement.status === "pending" || settlement.status === "claimed" ? (
          <div className="flex justify-end gap-2">
            {settlement.status === "pending" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(settlement.id, claimSettlement)}
                className="rounded-lg border border-blue-300 px-2.5 py-1.5 text-xs font-bold text-blue-700"
              >
                Claim
              </button>
            ) : null}
            <button
              type="button"
              disabled={busy}
              onClick={() => void onAction(settlement.id, completeSettlement)}
              className="rounded-lg border border-emerald-300 px-2.5 py-1.5 text-xs font-bold text-emerald-700"
            >
              Done
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void onAction(settlement.id, cancelSettlement)}
              className="rounded-lg border border-red-300 px-2.5 py-1.5 text-xs font-bold text-red-700"
            >
              Cancel
            </button>
          </div>
        ) : (
          <span className="block text-right">-</span>
        )}
      </td>
    </tr>
  );
});
