import { apiRequest } from "@/lib/api-client";
import type { User } from "@/types/api";

export function listStaff(): Promise<User[]> {
  return apiRequest<User[]>("/api/admin/staff");
}

export function createStaff(username: string, password: string): Promise<User> {
  return apiRequest<User>("/api/admin/staff", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function disableStaff(staffId: number): Promise<User> {
  return apiRequest<User>(`/api/admin/staff/${staffId}/disable`, {
    method: "PATCH",
  });
}

export function resetStaffPassword(
  staffId: number,
  password: string,
): Promise<User> {
  return apiRequest<User>(`/api/admin/staff/${staffId}/reset-password`, {
    method: "PATCH",
    body: JSON.stringify({ password }),
  });
}

export function deleteStaff(staffId: number): Promise<void> {
  return apiRequest<void>(`/api/admin/staff/${staffId}`, {
    method: "DELETE",
  });
}

