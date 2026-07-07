"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { friendlyError } from "@/lib/api-client";
import { listCoadmins } from "@/services/staff";
import type { User } from "@/types/api";

import {
  Panel,
  PanelLoading,
  readLedgerFilters,
  writeLedgerFilters,
} from "./shared";

export function LedgerFilterPanel() {
  const initialFilters = readLedgerFilters();
  const [dateFrom, setDateFrom] = useState(initialFilters.dateFrom ?? "");
  const [dateTo, setDateTo] = useState(initialFilters.dateTo ?? "");
  const [coadminId, setCoadminId] = useState(
    initialFilters.coadminId ? String(initialFilters.coadminId) : "all",
  );
  const [coadmins, setCoadmins] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadCoadmins = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCoadmins(await listCoadmins());
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCoadmins();
  }, [loadCoadmins]);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    writeLedgerFilters({
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      coadminId: coadminId === "all" ? undefined : Number(coadminId),
    });
  };

  return (
    <Panel
      title="Filter Panel"
      description="Set the reporting window and narrow staff/history views by coadmin."
      error={error}
    >
      {loading ? <PanelLoading message="Loading filters..." /> : null}
      <form
        onSubmit={applyFilters}
        className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]"
      >
        <label className="grid gap-1 text-sm font-semibold text-slate-700">
          From
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <label className="grid gap-1 text-sm font-semibold text-slate-700">
          To
          <input
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <label className="grid gap-1 text-sm font-semibold text-slate-700">
          Coadmin
          <select
            value={coadminId}
            onChange={(event) => setCoadminId(event.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2"
          >
            <option value="all">All coadmins</option>
            {coadmins.map((coadmin) => (
              <option
                key={coadmin.id}
                value={String(coadmin.id)}
              >
                {coadmin.username}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="self-end rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white"
        >
          Apply
        </button>
      </form>
    </Panel>
  );
}
