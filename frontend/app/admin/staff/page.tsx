"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { STAFF_PAGE_EVENTS } from "@/lib/live-events";
import {
  createStaff,
  deleteStaff,
  disableStaff,
  listStaff,
  resetStaffPassword,
} from "@/services/staff";
import type { User } from "@/types/api";

function formatDate(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function validatePassword(password: string): string | null {
  if (password.length < 12 || password.length > 128) {
    return "Password must be between 12 and 128 characters.";
  }
  return null;
}

export default function StaffPage() {
  const { user } = useAuth();
  const [staff, setStaff] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetId, setResetId] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);

  const loadStaff = useCallback(async () => {
    if (user?.role !== "admin") return;
    setLoading(true);
    setError("");
    try {
      setStaff(await listStaff());
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (user?.role !== "admin") return;
    let active = true;
    listStaff()
      .then((result) => {
        if (active) setStaff(result);
      })
      .catch((loadError: unknown) => {
        if (active) setError(friendlyError(loadError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

  useLiveUpdates(STAFF_PAGE_EVENTS, loadStaff, user?.role === "admin");

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setMessage("");

    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(username.trim())) {
      setError(
        "Username must be 3-64 characters using letters, numbers, dots, underscores, or hyphens.",
      );
      return;
    }
    const passwordError = validatePassword(password);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    setCreating(true);
    try {
      const created = await createStaff(username, password);
      setUsername("");
      setPassword("");
      setMessage(`Staff account “${created.username}” was created.`);
      await loadStaff();
    } catch (createError) {
      setError(friendlyError(createError));
    } finally {
      setCreating(false);
    }
  };

  const handleDisable = async (staffUser: User) => {
    if (
      !window.confirm(
        `Disable ${staffUser.username}? They will lose access immediately.`,
      )
    ) {
      return;
    }
    setBusyId(staffUser.id);
    setError("");
    setMessage("");
    try {
      await disableStaff(staffUser.id);
      setMessage(`“${staffUser.username}” has been disabled.`);
      await loadStaff();
    } catch (disableError) {
      setError(friendlyError(disableError));
    } finally {
      setBusyId(null);
    }
  };

  const handleReset = async (event: FormEvent<HTMLFormElement>, staffUser: User) => {
    event.preventDefault();
    setError("");
    setMessage("");
    const passwordError = validatePassword(resetPassword);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    setBusyId(staffUser.id);
    try {
      await resetStaffPassword(staffUser.id, resetPassword);
      setResetId(null);
      setResetPassword("");
      setMessage(`Password reset for “${staffUser.username}”.`);
    } catch (resetError) {
      setError(friendlyError(resetError));
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setBusyId(deleteTarget.id);
    setError("");
    setMessage("");
    try {
      await deleteStaff(deleteTarget.id);
      setMessage(`“${deleteTarget.username}” was permanently deleted.`);
      setDeleteTarget(null);
      await loadStaff();
    } catch (deleteError) {
      setError(friendlyError(deleteError));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell
      title="Staff management"
      description="Create staff accounts and control access to the payment ledger."
      requiredRole="admin"
    >
      <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
        <section className="h-fit rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <h2 className="text-lg font-bold text-slate-950">Create staff account</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Staff can access and process payments but cannot manage other accounts.
          </p>
          <form onSubmit={handleCreate} className="mt-5 space-y-4" noValidate>
            <label className="block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-700">
                Username
              </span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="off"
                placeholder="staff.username"
                disabled={creating}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-700">
                Temporary password
              </span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="new-password"
                placeholder="At least 12 characters"
                disabled={creating}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
              />
            </label>
            <button
              type="submit"
              disabled={creating}
              className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {creating ? "Creating…" : "Create staff"}
            </button>
          </form>
        </section>

        <section>
          {error ? (
            <div
              role="alert"
              className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
            >
              {error}
            </div>
          ) : null}
          {message ? (
            <div
              role="status"
              className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700"
            >
              {message}
            </div>
          ) : null}

          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-bold text-slate-950">Staff accounts</h2>
            <button
              type="button"
              onClick={() => void loadStaff()}
              disabled={loading}
              className="text-sm font-bold text-indigo-600 hover:text-indigo-700 disabled:opacity-50"
            >
              Refresh
            </button>
          </div>

          {!loading && staff.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white py-14 text-center">
              <p className="font-bold text-slate-800">No staff accounts yet</p>
              <p className="mt-2 text-sm text-slate-500">
                Create the first staff account using the form.
              </p>
            </div>
          ) : (
            <div className="grid gap-3">
              {staff.map((staffUser) => (
                <article
                  key={staffUser.id}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className="grid size-8 place-items-center rounded-full text-xs font-black text-white"
                          style={{ backgroundColor: staffUser.staff_color }}
                        >
                          {staffUser.username.slice(0, 1).toUpperCase()}
                        </span>
                        <h3 className="font-bold text-slate-950">
                          {staffUser.username}
                        </h3>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                            staffUser.is_active
                              ? "bg-emerald-50 text-emerald-700"
                              : "bg-slate-100 text-slate-500"
                          }`}
                        >
                          {staffUser.is_active ? "Active" : "Disabled"}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Last login: {formatDate(staffUser.last_login_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setResetId(
                            resetId === staffUser.id ? null : staffUser.id,
                          );
                          setResetPassword("");
                        }}
                        className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        Reset password
                      </button>
                      {staffUser.is_active ? (
                        <button
                          type="button"
                          disabled={busyId === staffUser.id}
                          onClick={() => void handleDisable(staffUser)}
                          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-100 disabled:opacity-50"
                        >
                          Disable
                        </button>
                      ) : null}
                      <button
                        type="button"
                        disabled={busyId === staffUser.id}
                        onClick={() => setDeleteTarget(staffUser)}
                        className="rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-50 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {resetId === staffUser.id ? (
                    <form
                      onSubmit={(event) => void handleReset(event, staffUser)}
                      className="mt-4 flex flex-col gap-2 border-t border-slate-100 pt-4 sm:flex-row"
                    >
                      <label className="flex-1">
                        <span className="sr-only">New password</span>
                        <input
                          type="password"
                          value={resetPassword}
                          onChange={(event) => setResetPassword(event.target.value)}
                          autoComplete="new-password"
                          placeholder="New password, at least 12 characters"
                          className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
                        />
                      </label>
                      <button
                        type="submit"
                        disabled={busyId === staffUser.id}
                        className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white hover:bg-slate-700 disabled:opacity-50"
                      >
                        Save password
                      </button>
                    </form>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      {deleteTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
          role="presentation"
          onClick={() => setDeleteTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-staff-title"
            className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h2
              id="delete-staff-title"
              className="text-lg font-bold text-slate-950"
            >
              Delete staff permanently?
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              Delete {deleteTarget.username} permanently? This will remove login
              access and detach staff history. This cannot be undone.
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busyId === deleteTarget.id}
                onClick={() => void handleDelete()}
                className="rounded-lg bg-red-700 px-4 py-2 text-sm font-bold text-white hover:bg-red-800 disabled:opacity-50"
              >
                {busyId === deleteTarget.id ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
