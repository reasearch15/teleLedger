export function LoadingScreen({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="flex items-center gap-3 text-sm font-medium text-slate-600">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
        {label}
      </div>
    </div>
  );
}

