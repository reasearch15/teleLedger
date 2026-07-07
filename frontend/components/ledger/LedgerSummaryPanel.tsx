"use client";

import { useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import { getLedger } from "@/services/ledger";
import type { LedgerSummary } from "@/types/api";

import { formatMoney, netClass, Panel, PanelLoading, useLedgerFilters } from "./shared";

export function LedgerSummaryPanel() {
  const filters = useLedgerFilters();
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ledger = await getLedger(filters);
      setSummary(ledger.summary);
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

  return (
    <Panel
      title="Summary Totals"
      description="Current open ledger totals for the selected date range."
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
