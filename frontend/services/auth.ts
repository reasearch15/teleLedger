import { apiRequest } from "@/lib/api-client";
import type { User } from "@/types/api";

let pendingCurrentUserRequest: Promise<User> | null = null;

export function getCurrentUser(): Promise<User> {
  if (pendingCurrentUserRequest) return pendingCurrentUserRequest;

  const request = apiRequest<User>("/api/auth/me").finally(() => {
    if (pendingCurrentUserRequest === request) {
      pendingCurrentUserRequest = null;
    }
  });
  pendingCurrentUserRequest = request;
  return request;
}

export function login(username: string, password: string): Promise<User> {
  return apiRequest<User>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return apiRequest<void>("/api/auth/logout", { method: "POST" });
}
