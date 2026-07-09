import { apiClient } from "@/lib/api/client";
import type { AssetType, MediaAsset } from "@/types/api";

export interface UploadMediaAssetInput {
  file: File;
  userId: string;
  sessionId?: string;
  assetType?: AssetType;
  ageAtTime?: number;
  locationAtTime?: string;
  peopleAtTime?: string;
  userComment?: string;
}

export const mediaApi = {
  upload: ({
    file,
    userId,
    sessionId,
    assetType,
    ageAtTime,
    locationAtTime,
    peopleAtTime,
    userComment,
  }: UploadMediaAssetInput) => {
    const form = new FormData();
    form.append("file", file);
    form.append("user_id", userId);
    if (sessionId) form.append("session_id", sessionId);
    if (assetType) form.append("asset_type", assetType);
    if (ageAtTime !== undefined) form.append("age_at_time", String(ageAtTime));
    if (locationAtTime) form.append("location_at_time", locationAtTime);
    if (peopleAtTime) form.append("people_at_time", peopleAtTime);
    if (userComment) form.append("user_comment", userComment);

    return apiClient.post<MediaAsset>("/api/v1/media-assets", form);
  },
};
