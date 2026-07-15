"use client";

import { useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import { getLedger } from "@/services/ledger";
import type { LedgerResponse, LedgerSummary } from "@/types/api";

import { formatMoney, netClass, Panel, PanelLoading, useLedgerFilters } from "./shared";

export function LedgerSummaryPanel() {
  const filters = useLedgerFilters();
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [ledgerMeta, setLedgerMeta] = useState<Pick<
    LedgerResponse,
    | "calculation_type"
    | "timezone"
    | "period_start"
    | "period_end"
    | "includes_settled"
    | "rolling_hours"
  > | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ledger = await getLedger(filters);
      setSummary(ledger.summary);
      setLedgerMeta({
        calculation_type: ledger.calculation_type,
        timezone: ledger.timezone,
        period_start: ledger.period_start,
        period_end: ledger.period_end,
        includes_settled: ledger.includes_settled,
        rolling_hours: ledger.rolling_hours,
      });
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, refresh, true);

  const isRollingActivity = ledgerMeta?.calculation_type === "rolling_activity";
  const isHistoricalActivity =
    ledgerMeta?.calculation_type === "custom_range" || isRollingActivity;
  const rangeLabel =
    ledgerMeta?.period_start && ledgerMeta.period_end
      ? formatNepalRange(ledgerMeta.period_start, ledgerMeta.period_end)
      : null;

  return (
    <Panel
      title={
        isRollingActivity
          ? "Rolling 12-Hour Activity"
          : isHistoricalActivity
            ? "Custom Date Range Activity"
            : "Current Open Balance"
      }
      description={
        isHistoricalActivity
          ? `${rangeLabel ?? "Selected historical range"} Nepal Time. Settled transactions are included.`
          : "Unsettled completed payments, adjustments, and cashouts."
      }
      error={error}
    >
      {loading ? (
        <PanelLoading message="Loading summary..." />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ["Payments", summary?.payment_total ?? "0.00"],
            ["Cashouts", summary?.total_out ?? "0.00"],
            ["Adjustments", summary?.adjustment_total ?? "0.00"],
            ["Net", summary?.net ?? "0.00"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-slate-200 p-4">
              <p className="text-sm font-semibold text-slate-500">{label}</p>
              <p
                className={`mt-2 text-2xl font-black ${
                  label === "Net" ? netClass(value) : "text-slate-950"
                }`}
              >
                {formatMoney(value)}
              </p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function formatNepalRange(start: string, end: string): string {
  const formatter = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Kathmandu",
  });
  return `${formatter.format(new Date(start))} - ${formatter.format(new Date(end))}`;
}
