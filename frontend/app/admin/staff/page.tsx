"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { STAFF_PAGE_EVENTS } from "@/lib/live-events";
import {
  activateCoadmin,
  assignStaffCoadmin,
  createCoadmin,
  createStaff,
  deleteCoadmin,
  deleteStaff,
  disableCoadmin,
  disableStaff,
  listCoadmins,
  listStaff,
  resetCoadminPassword,
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
  const [coadmins, setCoadmins] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [coadminId, setCoadminId] = useState("");
  const [coadminUsername, setCoadminUsername] = useState("");
  const [coadminPassword, setCoadminPassword] = useState("");
  const [coadminActive, setCoadminActive] = useState(true);
  const [resetId, setResetId] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [coadminResetId, setCoadminResetId] = useState<number | null>(null);
  const [coadminResetPassword, setCoadminResetPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [busyCoadminId, setBusyCoadminId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingCoadmin, setCreatingCoadmin] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [coadminDeleteTarget, setCoadminDeleteTarget] = useState<User | null>(
    null,
  );

  const staffCountByCoadmin = staff.reduce<Record<number, number>>(
    (counts, staffUser) => {
      if (staffUser.coadmin_id != null) {
        counts[staffUser.coadmin_id] = (counts[staffUser.coadmin_id] ?? 0) + 1;
      }
      return counts;
    },
    {},
  );

  const loadStaff = useCallback(async () => {
    if (user?.role !== "admin") return;
    setLoading(true);
    setError("");
    try {
      const [staffResult, coadminResult] = await Promise.all([
        listStaff(),
        listCoadmins(),
      ]);
      setStaff(staffResult);
      setCoadmins(coadminResult);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (user?.role !== "admin") return;
    let active = true;
    Promise.all([listStaff(), listCoadmins()])
      .then(([staffResult, coadminResult]) => {
        if (active) {
          setStaff(staffResult);
          setCoadmins(coadminResult);
        }
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
    if (!coadminId) {
      setError("Choose a coadmin for this staff account.");
      return;
    }

    setCreating(true);
    try {
      const created = await createStaff(username, password, Number(coadminId));
      setUsername("");
      setPassword("");
      setCoadminId("");
      setMessage(`Staff account “${created.username}” was created.`);
      await loadStaff();
    } catch (createError) {
      setError(friendlyError(createError));
    } finally {
      setCreating(false);
    }
  };

  const handleCreateCoadmin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setMessage("");

    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(coadminUsername.trim())) {
      setError(
        "Username must be 3-64 characters using letters, numbers, dots, underscores, or hyphens.",
      );
      return;
    }
    const passwordError = validatePassword(coadminPassword);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    setCreatingCoadmin(true);
    try {
      const created = await createCoadmin(
        coadminUsername,
        coadminPassword,
        coadminActive,
      );
      setCoadminUsername("");
      setCoadminPassword("");
      setCoadminActive(true);
      setMessage(`Coadmin account "${created.username}" was created.`);
      await loadStaff();
    } catch (createError) {
      setError(friendlyError(createError));
    } finally {
      setCreatingCoadmin(false);
    }
  };

  const handleAssignCoadmin = async (staffUser: User, nextCoadminId: string) => {
    if (!nextCoadminId) return;
    setBusyId(staffUser.id);
    setError("");
    setMessage("");
    try {
      await assignStaffCoadmin(staffUser.id, Number(nextCoadminId));
      setMessage(`Coadmin updated for "${staffUser.username}".`);
      await loadStaff();
    } catch (assignError) {
      setError(friendlyError(assignError));
    } finally {
      setBusyId(null);
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

  const handleCoadminReset = async (
    event: FormEvent<HTMLFormElement>,
    coadminUser: User,
  ) => {
    event.preventDefault();
    setError("");
    setMessage("");
    const passwordError = validatePassword(coadminResetPassword);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    setBusyCoadminId(coadminUser.id);
    try {
      await resetCoadminPassword(coadminUser.id, coadminResetPassword);
      setCoadminResetId(null);
      setCoadminResetPassword("");
      setMessage(`Password reset for “${coadminUser.username}”.`);
    } catch (resetError) {
      setError(friendlyError(resetError));
    } finally {
      setBusyCoadminId(null);
    }
  };

  const handleCoadminDisable = async (coadminUser: User) => {
    if (
      !window.confirm(
        `Disable ${coadminUser.username}? They will lose access immediately.`,
      )
    ) {
      return;
    }
    setBusyCoadminId(coadminUser.id);
    setError("");
    setMessage("");
    try {
      await disableCoadmin(coadminUser.id);
      setMessage(`“${coadminUser.username}” has been disabled.`);
      await loadStaff();
    } catch (disableError) {
      setError(friendlyError(disableError));
    } finally {
      setBusyCoadminId(null);
    }
  };

  const handleCoadminActivate = async (coadminUser: User) => {
    setBusyCoadminId(coadminUser.id);
    setError("");
    setMessage("");
    try {
      await activateCoadmin(coadminUser.id);
      setMessage(`“${coadminUser.username}” has been reactivated.`);
      await loadStaff();
    } catch (activateError) {
      setError(friendlyError(activateError));
    } finally {
      setBusyCoadminId(null);
    }
  };

  const handleCoadminDelete = async () => {
    if (!coadminDeleteTarget) return;
    setBusyCoadminId(coadminDeleteTarget.id);
    setError("");
    setMessage("");
    try {
      await deleteCoadmin(coadminDeleteTarget.id);
      setMessage(`“${coadminDeleteTarget.username}” was permanently deleted.`);
      setCoadminDeleteTarget(null);
      await loadStaff();
    } catch (deleteError) {
      setError(friendlyError(deleteError));
    } finally {
      setBusyCoadminId(null);
    }
  };

  return (
    <AppShell
      title="Staff management"
      description="Create staff accounts and control access to the payment ledger."
      requiredRole="admin"
    >
      <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
        <div className="grid h-fit gap-4">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <h2 className="text-lg font-bold text-slate-950">Create coadmin</h2>
          <form onSubmit={handleCreateCoadmin} className="mt-5 space-y-4" noValidate>
            <label className="block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-700">
                Username
              </span>
              <input
                value={coadminUsername}
                onChange={(event) => setCoadminUsername(event.target.value)}
                autoComplete="off"
                placeholder="coadmin.username"
                disabled={creatingCoadmin}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-700">
                Temporary password
              </span>
              <input
                type="password"
                value={coadminPassword}
                onChange={(event) => setCoadminPassword(event.target.value)}
                autoComplete="new-password"
                placeholder="At least 12 characters"
                disabled={creatingCoadmin}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
              />
            </label>
            <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
              <input
                type="checkbox"
                checked={coadminActive}
                onChange={(event) => setCoadminActive(event.target.checked)}
                disabled={creatingCoadmin}
              />
              Active
            </label>
            <button
              type="submit"
              disabled={creatingCoadmin}
              className="w-full rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {creatingCoadmin ? "Creating..." : "Create coadmin"}
            </button>
          </form>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
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
            <label className="block">
              <span className="mb-1.5 block text-sm font-semibold text-slate-700">
                Coadmin
              </span>
              <select
                value={coadminId}
                onChange={(event) => setCoadminId(event.target.value)}
                disabled={creating}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus:border-indigo-500"
              >
                <option value="">Choose coadmin</option>
                {coadmins
                  .filter((coadmin) => coadmin.is_active)
                  .map((coadmin) => (
                    <option key={coadmin.id} value={coadmin.id}>
                      {coadmin.username}
                    </option>
                  ))}
              </select>
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
        </div>

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
            <h2 className="font-bold text-slate-950">Coadmin accounts</h2>
          </div>

          {!loading && coadmins.length === 0 ? (
            <div className="mb-8 rounded-2xl border border-dashed border-slate-300 bg-white py-10 text-center">
              <p className="font-bold text-slate-800">No coadmin accounts yet</p>
              <p className="mt-2 text-sm text-slate-500">
                Create a coadmin using the form on the left.
              </p>
            </div>
          ) : (
            <div className="mb-8 grid gap-3">
              {coadmins.map((coadminUser) => {
                const assignedStaff = staffCountByCoadmin[coadminUser.id] ?? 0;
                return (
                  <article
                    key={coadminUser.id}
                    className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                  >
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-bold text-slate-950">
                            {coadminUser.username}
                          </h3>
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                              coadminUser.is_active
                                ? "bg-emerald-50 text-emerald-700"
                                : "bg-slate-100 text-slate-500"
                            }`}
                          >
                            {coadminUser.is_active ? "Active" : "Disabled"}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-slate-500">
                          Last login: {formatDate(coadminUser.last_login_at)}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          Assigned staff: {assignedStaff}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            setCoadminResetId(
                              coadminResetId === coadminUser.id
                                ? null
                                : coadminUser.id,
                            );
                            setCoadminResetPassword("");
                          }}
                          className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                        >
                          Reset password
                        </button>
                        {coadminUser.is_active ? (
                          <button
                            type="button"
                            disabled={busyCoadminId === coadminUser.id}
                            onClick={() => void handleCoadminDisable(coadminUser)}
                            className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-100 disabled:opacity-50"
                          >
                            Disable
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busyCoadminId === coadminUser.id}
                            onClick={() => void handleCoadminActivate(coadminUser)}
                            className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
                          >
                            Activate
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busyCoadminId === coadminUser.id}
                          onClick={() => setCoadminDeleteTarget(coadminUser)}
                          className="rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-50 disabled:opacity-50"
                        >
                          Delete
                        </button>
                      </div>
                    </div>

                    {coadminResetId === coadminUser.id ? (
                      <form
                        onSubmit={(event) =>
                          void handleCoadminReset(event, coadminUser)
                        }
                        className="mt-4 flex flex-col gap-2 border-t border-slate-100 pt-4 sm:flex-row"
                      >
                        <label className="flex-1">
                          <span className="sr-only">New password</span>
                          <input
                            type="password"
                            value={coadminResetPassword}
                            onChange={(event) =>
                              setCoadminResetPassword(event.target.value)
                            }
                            autoComplete="new-password"
                            placeholder="New password, at least 12 characters"
                            className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm focus:border-indigo-500"
                          />
                        </label>
                        <button
                          type="submit"
                          disabled={busyCoadminId === coadminUser.id}
                          className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white hover:bg-slate-700 disabled:opacity-50"
                        >
                          Save password
                        </button>
                      </form>
                    ) : null}
                  </article>
                );
              })}
            </div>
          )}

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
                      <label className="mt-3 block max-w-xs">
                        <span className="mb-1 block text-xs font-bold text-slate-500">
                          Coadmin
                        </span>
                        <select
                          value={staffUser.coadmin_id ?? ""}
                          onChange={(event) =>
                            void handleAssignCoadmin(staffUser, event.target.value)
                          }
                          disabled={busyId === staffUser.id || coadmins.length === 0}
                          className="w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm font-semibold text-slate-800"
                        >
                          {coadmins
                            .filter(
                              (coadmin) =>
                                coadmin.is_active ||
                                coadmin.id === staffUser.coadmin_id,
                            )
                            .map((coadmin) => (
                              <option key={coadmin.id} value={coadmin.id}>
                                {coadmin.username}
                              </option>
                            ))}
                        </select>
                      </label>
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

      {coadminDeleteTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
          role="presentation"
          onClick={() => setCoadminDeleteTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-coadmin-title"
            className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h2
              id="delete-coadmin-title"
              className="text-lg font-bold text-slate-950"
            >
              Delete coadmin permanently?
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              Delete {coadminDeleteTarget.username} permanently? This removes
              login access. Payments, ledger records, settlements, and audit
              history are preserved.
            </p>
            {(staffCountByCoadmin[coadminDeleteTarget.id] ?? 0) > 0 ? (
              <p className="mt-3 text-sm font-semibold text-red-700">
                This coadmin still has assigned staff. Reassign or delete staff
                first.
              </p>
            ) : null}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setCoadminDeleteTarget(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busyCoadminId === coadminDeleteTarget.id}
                onClick={() => void handleCoadminDelete()}
                className="rounded-lg bg-red-700 px-4 py-2 text-sm font-bold text-white hover:bg-red-800 disabled:opacity-50"
              >
                {busyCoadminId === coadminDeleteTarget.id
                  ? "Deleting…"
                  : "Delete permanently"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

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
