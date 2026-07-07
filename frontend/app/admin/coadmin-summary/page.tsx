"use client";

import { AppShell } from "@/components/app-shell";
import { CoadminSummaryPanel } from "@/components/ledger/CoadminSummaryPanel";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";

export default function AdminCoadminSummaryPage() {
  return (
    <AppShell
      title="Coadmin Summary"
      description="Open ledger balances grouped by coadmin team."
      requiredRole="admin"
    >
      <div className="space-y-10">
        <LedgerFilterPanel />
        <CoadminSummaryPanel />
      </div>
    </AppShell>
  );
}
