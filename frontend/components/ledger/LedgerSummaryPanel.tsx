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
    "calculation_type" | "timezone" | "period_start" | "period_end" | "includes_settled"
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

  const isShiftActivity = ledgerMeta?.calculation_type === "shift_activity";

  return (
    <Panel
      title={isShiftActivity ? "Daily Activity" : "Current Open Balance"}
      description={
        isShiftActivity
          ? `Completed payments, adjustments, and cashouts in Nepal Time (${ledgerMeta?.timezone ?? "Asia/Kathmandu"}). Settled transactions are included.`
          : "Unsettled completed payments, adjustments, and cashouts."
      }
      error={error}
    >
      {loading ? (
        <PanelLoading message="Loading summary..." />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ["Total In", summary?.total_in ?? "0.00"],
            ["Total Out", summary?.total_out ?? "0.00"],
            ["Settled", summary?.settled_amount ?? "0.00"],
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
