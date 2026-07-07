"use client";

import { memo, useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import { ADJUSTMENT_PAGE_SIZE, listLedgerAdjustments } from "@/services/ledger";
import type { LedgerAdjustment } from "@/types/api";

import {
  EmptyTableRow,
  formatDate,
  formatMoney,
  LoadMoreButton,
  netClass,
  pageRows,
  Panel,
  TableShell,
  useLedgerFilters,
} from "./shared";

export function AdjustmentHistoryPanel() {
  const filters = useLedgerFilters();
  const [items, setItems] = useState<LedgerAdjustment[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(
    async (reset = true, nextCursor: string | null = null) => {
      if (reset) setLoading(true);
      else setLoadingMore(true);
      setError("");
      try {
        const page = await listLedgerAdjustments({
          ...filters,
          limit: ADJUSTMENT_PAGE_SIZE,
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

  return (
    <Panel
      title="Adjustment History"
      description={`Latest ${ADJUSTMENT_PAGE_SIZE} manual total-in adjustments.`}
      error={error}
    >
      <TableShell>
        <table className="w-full min-w-[960px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-5 py-3">Staff</th>
              <th className="px-5 py-3">Previous Total In</th>
              <th className="px-5 py-3">New Total In</th>
              <th className="px-5 py-3">Delta</th>
              <th className="px-5 py-3">Reason</th>
              <th className="px-5 py-3">Admin</th>
              <th className="px-5 py-3">Created at</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <EmptyTableRow colSpan={7} message="Loading adjustments..." />
            ) : null}
            {!loading && items.length === 0 ? (
              <EmptyTableRow colSpan={7} message="No records found." />
            ) : null}
            {items.map((adjustment) => (
              <AdjustmentRow key={adjustment.id} adjustment={adjustment} />
            ))}
          </tbody>
        </table>
      </TableShell>
      {hasMore ? (
        <LoadMoreButton
          loading={loadingMore}
          onClick={() => void load(false, cursor)}
        />
      ) : null}
    </Panel>
  );
}

const AdjustmentRow = memo(function AdjustmentRow({
  adjustment,
}: {
  adjustment: LedgerAdjustment;
}) {
  return (
    <tr>
      <td className="px-5 py-3 font-semibold">
        <span
          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: adjustment.staff_color || "#64748B" }}
        />
        {adjustment.staff_username || "Deleted Staff"}
      </td>
      <td className="px-5 py-3">{formatMoney(adjustment.previous_total_in)}</td>
      <td className="px-5 py-3">{formatMoney(adjustment.new_total_in)}</td>
      <td className={`px-5 py-3 font-bold ${netClass(adjustment.amount_delta)}`}>
        {formatMoney(adjustment.amount_delta)}
      </td>
      <td className="px-5 py-3">{adjustment.reason}</td>
      <td className="px-5 py-3">{adjustment.created_by_admin_username ?? "-"}</td>
      <td className="px-5 py-3">{formatDate(adjustment.created_at)}</td>
    </tr>
  );
});
