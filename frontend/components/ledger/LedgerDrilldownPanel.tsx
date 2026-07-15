"use client";

import { useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import { getLedgerDrilldown } from "@/services/ledger";
import type { LedgerDrilldownResponse } from "@/types/api";

import {
  EmptyTableRow,
  formatDate,
  formatMoney,
  Panel,
  PanelLoading,
  TableShell,
  useLedgerFilters,
} from "./shared";

export function LedgerDrilldownPanel() {
  const filters = useLedgerFilters();
  const [drilldown, setDrilldown] = useState<LedgerDrilldownResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const isHistorical = filters.calculationMode !== "open_balance";

  const refresh = useCallback(async () => {
    if (!isHistorical) {
      setDrilldown(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      setDrilldown(await getLedgerDrilldown(filters));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [filters, isHistorical]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, refresh, isHistorical);

  if (!isHistorical) return null;

  return (
    <Panel
      title="Transaction Drilldown"
      description="Read-only included payments, cashouts, and adjustments for the selected activity window."
      error={error}
    >
      {loading ? <PanelLoading message="Loading transaction drilldown..." /> : null}
      {!loading && drilldown ? (
        <div className="grid gap-6">
          <DrilldownTable
            title="Payments"
            headers={["Staff", "Amount", "Status", "Completed", "Settlement", "Tag"]}
            rows={drilldown.payments.map((payment) => [
              payment.staff_username,
              formatMoney(payment.amount),
              payment.status,
              formatDate(payment.completed_at),
              payment.settlement_id ?? "-",
              payment.recipient_tag,
            ])}
          />
          <DrilldownTable
            title="Cashouts"
            headers={["Staff", "Amount", "Status", "Completed", "Settlement", "Tag"]}
            rows={drilldown.cashouts.map((cashout) => [
              cashout.staff_username,
              formatMoney(cashout.amount),
              cashout.status,
              formatDate(cashout.completed_at),
              cashout.settlement_id ?? "-",
              cashout.player_tag,
            ])}
          />
          <DrilldownTable
            title="Adjustments"
            headers={["Staff", "Amount", "Created", "Settlement", "Reason"]}
            rows={drilldown.adjustments.map((adjustment) => [
              adjustment.staff_username,
              formatMoney(adjustment.amount_delta),
              formatDate(adjustment.created_at),
              adjustment.settlement_id ?? "-",
              adjustment.reason,
            ])}
          />
        </div>
      ) : null}
    </Panel>
  );
}

function DrilldownTable({
  title,
  headers,
  rows,
}: {
  title: string;
  headers: string[];
  rows: Array<Array<string | number>>;
}) {
  return (
    <section>
      <h3 className="mb-3 text-sm font-black uppercase text-slate-500">{title}</h3>
      <TableShell>
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              {headers.map((header) => (
                <th key={header} className="px-4 py-3">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.length === 0 ? (
              <EmptyTableRow colSpan={headers.length} message="No records found." />
            ) : null}
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={`${rowIndex}-${cellIndex}`} className="px-4 py-3">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableShell>
    </section>
  );
}
