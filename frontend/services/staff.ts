import { apiRequest } from "@/lib/api-client";
import type { User } from "@/types/api";

export function listStaff(): Promise<User[]> {
  return apiRequest<User[]>("/api/admin/staff");
}

export function listCoadmins(): Promise<User[]> {
  return apiRequest<User[]>("/api/admin/coadmins");
}

export function createCoadmin(
  username: string,
  password: string,
  isActive: boolean,
): Promise<User> {
  return apiRequest<User>("/api/admin/coadmins", {
    method: "POST",
    body: JSON.stringify({
      username,
      password,
      is_active: isActive,
    }),
  });
}

export function createStaff(
  username: string,
  password: string,
  coadminId: number,
): Promise<User> {
  return apiRequest<User>("/api/admin/staff", {
    method: "POST",
    body: JSON.stringify({ username, password, coadmin_id: coadminId }),
  });
}

export function assignStaffCoadmin(
  staffId: number,
  coadminId: number,
): Promise<User> {
  return apiRequest<User>(`/api/admin/staff/${staffId}/coadmin`, {
    method: "PATCH",
    body: JSON.stringify({ coadmin_id: coadminId }),
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

export function resetCoadminPassword(
  coadminId: number,
  password: string,
): Promise<User> {
  return apiRequest<User>(`/api/admin/coadmins/${coadminId}/reset-password`, {
    method: "PATCH",
    body: JSON.stringify({ password }),
  });
}

export function disableCoadmin(coadminId: number): Promise<User> {
  return apiRequest<User>(`/api/admin/coadmins/${coadminId}/disable`, {
    method: "PATCH",
  });
}

export function activateCoadmin(coadminId: number): Promise<User> {
  return apiRequest<User>(`/api/admin/coadmins/${coadminId}/activate`, {
    method: "PATCH",
  });
}

export function deleteCoadmin(coadminId: number): Promise<void> {
  return apiRequest<void>(`/api/admin/coadmins/${coadminId}`, {
    method: "DELETE",
  });
}
