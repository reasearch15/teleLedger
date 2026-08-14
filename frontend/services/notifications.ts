import { apiRequest } from "@/lib/api-client";
import type {
  NotificationCountResponse,
  NotificationListResponse,
  PersistentNotification,
} from "@/types/api";

export function listNotifications(options: {
  unreadOnly?: boolean;
  limit?: number;
} = {}): Promise<NotificationListResponse> {
  const query = new URLSearchParams();
  if (options.unreadOnly) query.set("unread_only", "true");
  query.set("limit", String(options.limit ?? 30));
  return apiRequest<NotificationListResponse>(
    `/api/notifications?${query.toString()}`,
  );
}

export function getUnreadNotificationCount(): Promise<NotificationCountResponse> {
  return apiRequest<NotificationCountResponse>("/api/notifications/unread-count");
}

export function markNotificationRead(
  notificationId: number,
): Promise<PersistentNotification> {
  return apiRequest<PersistentNotification>(
    `/api/notifications/${notificationId}/read`,
    { method: "POST" },
  );
}
