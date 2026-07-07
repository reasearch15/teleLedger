"use client";

import { AppShell } from "@/components/app-shell";
import { AdjustmentHistoryPanel } from "@/components/ledger/AdjustmentHistoryPanel";
import { CoadminSummaryPanel } from "@/components/ledger/CoadminSummaryPanel";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";
import { LedgerSummaryPanel } from "@/components/ledger/LedgerSummaryPanel";
import { SettlementHistoryPanel } from "@/components/ledger/SettlementHistoryPanel";
import { StaffBalancesPanel } from "@/components/ledger/StaffBalancesPanel";

export default function AdminLedgerPage() {
  return (
    <AppShell
      title="Ledger"
      description="Cashout belongs to the staff who created/requested it."
      requiredRole="admin"
    >
      <div className="-mx-4 space-y-10 bg-slate-100 px-4 py-6 sm:-mx-6 sm:px-6 lg:rounded-3xl">
        <LedgerFilterPanel />
        <LedgerSummaryPanel />
        <CoadminSummaryPanel />
        <StaffBalancesPanel />
        <AdjustmentHistoryPanel />
        <SettlementHistoryPanel />
      </div>
    </AppShell>
  );
}
