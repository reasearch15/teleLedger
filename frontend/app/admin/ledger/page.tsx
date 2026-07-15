"use client";

import { AppShell } from "@/components/app-shell";
import { LedgerDrilldownPanel } from "@/components/ledger/LedgerDrilldownPanel";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";
import { LedgerSummaryPanel } from "@/components/ledger/LedgerSummaryPanel";

export default function AdminLedgerPage() {
  return (
    <AppShell
      title="Ledger"
      description="Cashout belongs to the staff who created/requested it."
      requiredRole="admin"
    >
      <div className="space-y-10">
        <LedgerFilterPanel />
        <LedgerSummaryPanel />
        <LedgerDrilldownPanel />
      </div>
    </AppShell>
  );
}
