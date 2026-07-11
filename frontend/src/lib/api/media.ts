import { apiClient } from "@/lib/api/client";
import type { AssetType, MediaAsset } from "@/types/api";

/** userId는 없다 — 인증 토큰의 로그인 사용자로 서버가 항상 고정한다
 * (backend/app/api/v1/media.py 참조, Form 필드에서 제거됨). */
export interface UploadMediaAssetInput {
  file: File;
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
    sessionId,
    assetType,
    ageAtTime,
    locationAtTime,
    peopleAtTime,
    userComment,
  }: UploadMediaAssetInput) => {
    const form = new FormData();
    form.append("file", file);
    if (sessionId) form.append("session_id", sessionId);
    if (assetType) form.append("asset_type", assetType);
    if (ageAtTime !== undefined) form.append("age_at_time", String(ageAtTime));
    if (locationAtTime) form.append("location_at_time", locationAtTime);
    if (peopleAtTime) form.append("people_at_time", peopleAtTime);
    if (userComment) form.append("user_comment", userComment);

    return apiClient.post<MediaAsset>("/api/v1/media-assets", form);
  },
  /** 본인이 업로드한 미디어 전체를 최근 업로드순으로. */
  list: () => apiClient.get<MediaAsset[]>("/api/v1/media-assets"),
};
