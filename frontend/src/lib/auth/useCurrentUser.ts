"use client";

import { useEffect, useState } from "react";

import { authApi } from "@/lib/api/auth";
import { session } from "@/lib/auth/session";
import type { User } from "@/types/api";

/** GET /api/v1/auth/me를 호출해 현재 로그인 사용자를 가져온다. 토큰이 없으면
 * 아예 호출하지 않는다(비로그인 상태를 401 에러로 처리하지 않기 위함). */
export function useCurrentUser() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!session.getAccessToken()) {
      setLoading(false);
      return;
    }
    authApi
      .me()
      .then((current) => {
        if (!active) return;
        setUser(current);
        session.setUserId(current.id);
      })
      .catch(() => {
        if (active) setError("사용자 정보를 불러오지 못했어요.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return { user, loading, error };
}
