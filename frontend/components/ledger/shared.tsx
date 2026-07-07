"use client";

import { useCallback, useEffect, useState } from "react";

export type LedgerFilters = {
  dateFrom?: string;
  dateTo?: string;
  coadminId?: number;
};

const FILTER_EVENT = "ledgerFiltersChanged";

export function readLedgerFilters(): LedgerFilters {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const coadminId = Number(params.get("coadminId") ?? "");
  return {
    dateFrom: params.get("dateFrom") || undefined,
    dateTo: params.get("dateTo") || undefined,
    coadminId: Number.isFinite(coadminId) && coadminId > 0 ? coadminId : undefined,
  };
}

export function writeLedgerFilters(filters: LedgerFilters): void {
  const params = new URLSearchParams(window.location.search);
  if (filters.dateFrom) params.set("dateFrom", filters.dateFrom);
  else params.delete("dateFrom");
  if (filters.dateTo) params.set("dateTo", filters.dateTo);
  else params.delete("dateTo");
  if (filters.coadminId) params.set("coadminId", String(filters.coadminId));
  else params.delete("coadminId");
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
  window.dispatchEvent(new Event(FILTER_EVENT));
}

export function useLedgerFilters(): LedgerFilters {
  const [filters, setFilters] = useState<LedgerFilters>(() => readLedgerFilters());

  useEffect(() => {
    const updateFilters = () => setFilters(readLedgerFilters());
    window.addEventListener(FILTER_EVENT, updateFilters);
    window.addEventListener("popstate", updateFilters);
    return () => {
      window.removeEventListener(FILTER_EVENT, updateFilters);
      window.removeEventListener("popstate", updateFilters);
    };
  }, []);

  return filters;
}

export function usePanelRefresh<T>(
  loader: () => Promise<T>,
  onLoaded: (value: T) => void,
) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      onLoaded(await loader());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Request failed.");
    } finally {
      setLoading(false);
    }
  }, [loader, onLoaded]);

  return { loading, error, refresh, setError };
}

export function formatMoney(value: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

export function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function netClass(value: string): string {
  const numeric = Number(value);
  if (numeric > 0) return "text-emerald-700";
  if (numeric < 0) return "text-red-700";
  return "text-slate-500";
}

export function formatIds(ids: number[]): string {
  return ids.length ? ids.join(", ") : "-";
}

export function pageRows<T>(page: { rows?: T[]; items: T[] }): T[] {
  return page.rows ?? page.items;
}

export function Panel({
  title,
  description,
  action,
  error,
  children,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="font-black text-slate-950">{title}</h2>
          <p className="mt-1 text-sm text-slate-500">{description}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {error ? (
        <div className="border-b border-red-100 bg-red-50 px-5 py-3 text-sm font-medium text-red-700">
          {error}
        </div>
      ) : null}
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

export function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto sm:-mx-5">
      <div className="inline-block min-w-full px-4 align-middle sm:px-5">
        {children}
      </div>
    </div>
  );
}

export function PanelLoading({ message }: { message: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{message}</div>;
}

export function EmptyTableRow({ colSpan, message }: { colSpan: number; message: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-5 py-8 text-center text-slate-500">
        {message}
      </td>
    </tr>
  );
}

export function LoadMoreButton({
  loading,
  onClick,
}: {
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <div className="mt-4 flex justify-center border-t border-slate-100 pt-4">
      <button
        type="button"
        disabled={loading}
        onClick={onClick}
        className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-bold text-indigo-700 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
      >
        {loading ? "Loading..." : "Load more"}
      </button>
    </div>
  );
}
