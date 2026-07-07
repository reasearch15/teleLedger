"use client";

import { AppShell } from "@/components/app-shell";
import { AdjustmentHistoryPanel } from "@/components/ledger/AdjustmentHistoryPanel";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";

export default function AdminAdjustmentHistoryPage() {
  return (
    <AppShell
      title="Adjustment History"
      description="Manual total-in adjustment history with cursor pagination."
      requiredRole="admin"
    >
      <div className="space-y-10">
        <LedgerFilterPanel />
        <AdjustmentHistoryPanel />
      </div>
    </AppShell>
  );
}
