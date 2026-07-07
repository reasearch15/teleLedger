import { environment } from "@/lib/env";

type ApiErrorBody = {
  detail?: string | Array<{ msg?: string }>;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function errorMessage(body: ApiErrorBody, fallback: string): string {
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((error) => error.msg)
      .filter(Boolean)
      .join(". ");
  }
  return fallback;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${environment.apiUrl}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // The fallback below handles non-JSON infrastructure responses.
    }
    throw new ApiError(
      errorMessage(body, `Request failed with status ${response.status}`),
      response.status,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error && error.message === "Failed to fetch") {
    return "Cannot reach the API. Make sure the backend is running.";
  }
  return "Something went wrong. Please try again.";
}

