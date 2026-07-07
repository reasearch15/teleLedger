"use client";

import { AppShell } from "@/components/app-shell";
import { LedgerFilterPanel } from "@/components/ledger/LedgerFilterPanel";
import { StaffBalancesPanel } from "@/components/ledger/StaffBalancesPanel";

export default function AdminStaffBalancesPage() {
  return (
    <AppShell
      title="Staff Balances"
      description="Open ledger balances by staff account."
      requiredRole="admin"
    >
      <div className="space-y-10">
        <LedgerFilterPanel />
        <StaffBalancesPanel />
      </div>
    </AppShell>
  );
}
