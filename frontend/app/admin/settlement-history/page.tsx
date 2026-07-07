"use client";

import { AppShell } from "@/components/app-shell";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";
import { SettlementHistoryPanel } from "@/components/ledger/SettlementHistoryPanel";

export default function AdminSettlementHistoryPage() {
  return (
    <AppShell
      title="Settlement History"
      description="Withdrawal and settlement history with cursor pagination."
      requiredRole="admin"
    >
      <div className="space-y-10">
        <LedgerFilterPanel />
        <SettlementHistoryPanel />
      </div>
    </AppShell>
  );
}
