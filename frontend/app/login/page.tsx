"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { LoadingScreen } from "@/components/loading-screen";
import { friendlyError } from "@/lib/api-client";

export default function LoginPage() {
  const { user, loading, login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard");
    }
  }, [loading, router, user]);

  if (loading || user) {
    return <LoadingScreen label="Checking your session…" />;
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");

    if (username.trim().length < 3) {
      setError("Enter your username.");
      return;
    }
    if (!password) {
      setError("Enter your password.");
      return;
    }

    setSubmitting(true);
    try {
      await login(username, password);
      router.replace("/dashboard");
    } catch (submitError) {
      setError(friendlyError(submitError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="grid min-h-screen bg-slate-950 lg:grid-cols-[1.15fr_0.85fr]">
      <section className="relative hidden overflow-hidden p-12 lg:flex lg:flex-col lg:justify-between">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(99,102,241,0.34),_transparent_42%),radial-gradient(circle_at_bottom_right,_rgba(14,165,233,0.2),_transparent_38%)]" />
        <div className="relative flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-indigo-500 font-black text-white">
            L
          </span>
          <span className="font-bold text-white">Ledger</span>
        </div>
        <div className="relative max-w-xl">
          <p className="mb-4 text-sm font-bold uppercase tracking-[0.2em] text-indigo-300">
            Payment operations
          </p>
          <h1 className="text-5xl font-bold leading-[1.08] tracking-tight text-white">
            Keep every payment moving, without losing the thread.
          </h1>
          <p className="mt-6 max-w-lg text-lg leading-8 text-slate-300">
            A focused workspace for reviewing, claiming, and completing payment
            notifications.
          </p>
        </div>
        <p className="relative text-sm text-slate-500">
          Local access · Secure cookie session
        </p>
      </section>

      <section className="flex items-center justify-center bg-slate-50 px-5 py-12 sm:px-10">
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden">
            <span className="grid h-11 w-11 place-items-center rounded-2xl bg-indigo-600 font-black text-white">
              L
            </span>
          </div>
          <p className="text-sm font-bold uppercase tracking-[0.18em] text-indigo-600">
            Welcome back
          </p>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-slate-950">
            Sign in to your workspace
          </h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Use the account provided by your Ledger administrator.
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-5" noValidate>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">
                Username
              </span>
              <input
                type="text"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm transition placeholder:text-slate-400 focus:border-indigo-500"
                placeholder="your.username"
                disabled={submitting}
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">
                Password
              </span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-950 shadow-sm transition placeholder:text-slate-400 focus:border-indigo-500"
                placeholder="Enter your password"
                disabled={submitting}
              />
            </label>

            {error ? (
              <div
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
              >
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-xl bg-indigo-600 px-4 py-3 font-bold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}
