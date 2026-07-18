"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { adminApi } from "@/lib/api/admin";
import type { AdminSessionDetail, AdminUserDetail, Autobiography } from "@/types/api";

const PDF_POLL_INTERVAL_MS = 4000;

const SESSION_TYPE_LABEL: Record<string, string> = {
  photo: "사진",
  fixed_question: "고정 질문",
  episode: "에피소드",
};

export function UserLookupSection() {
  const [identifier, setIdentifier] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<AdminUserDetail | null>(null);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifier.trim()) return;
    setLoading(true);
    setError(null);
    adminApi
      .lookupUser(identifier.trim())
      .then(setUser)
      .catch(() => {
        setUser(null);
        setError("유저를 찾을 수 없어요.");
      })
      .finally(() => setLoading(false));
  };

  const refetch = () => {
    if (!user) return;
    adminApi.lookupUser(user.id).then(setUser);
  };

  return (
    <section>
      <h2 className="mb-1 text-lg font-semibold text-black">유저 검색 및 관리</h2>
      <p className="mb-4 text-sm text-black/40">
        유저 UUID 또는 이메일로 조회해 프로필·세션 산문을 보고 고칠 수 있어요.
        이메일 변경/비밀번호 재설정은 되돌리기 어려우니 신중하게 사용하세요.
      </p>
      <form onSubmit={handleSearch} className="mb-4 flex gap-2">
        <input
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          placeholder="유저 UUID 또는 이메일"
          className="flex-1 rounded-xl border border-black/10 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-full border border-black/10 px-4 py-2 text-sm text-black/60 disabled:opacity-40"
        >
          {loading ? "조회 중..." : "조회"}
        </button>
      </form>

      {error && <p className="text-base text-black/50">{error}</p>}
      {user && <UserDetailPanel user={user} onChanged={refetch} />}
    </section>
  );
}

function UserDetailPanel({ user, onChanged }: { user: AdminUserDetail; onChanged: () => void }) {
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-black/10 p-4">
      <div className="text-sm text-black/70">
        <p className="font-medium text-black">
          {user.name} · {user.email}
        </p>
        <p className="mt-1 text-black/40">
          user_id {user.id} · {user.current_stage} · role={user.role}
        </p>
      </div>

      <EmailUpdateForm userId={user.id} currentEmail={user.email} onChanged={onChanged} />
      <PasswordResetForm userId={user.id} />

      <UserAutobiographiesSection userId={user.id} />

      <div>
        <p className="mb-2 text-sm font-medium text-black">세션 ({user.sessions.length}개)</p>
        <ul className="flex flex-col gap-3">
          {user.sessions.map((session) => (
            <SessionProseEditor
              key={session.id}
              userId={user.id}
              session={session}
              onChanged={onChanged}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

/** 고객이 완성한 자서전 목록 + 실물 인쇄용 PDF 다운로드/생성. 고객 본인이 보는
 * "나의 책장"과 달리, 관리자는 이 유저의 소유가 아니므로 조판 트리거도 전용
 * 엔드포인트(admin/users/{id}/autobiographies/{id}/pdf/generate)를 쓴다. */
function UserAutobiographiesSection({ userId }: { userId: string }) {
  const [books, setBooks] = useState<Autobiography[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pdfTriggeredIds, setPdfTriggeredIds] = useState<Set<string>>(new Set());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const list = await adminApi.listUserAutobiographies(userId);
      setBooks(list);
      setPdfTriggeredIds((prev) => {
        if (prev.size === 0) return prev;
        const stillPending = new Set(
          list.filter((b) => prev.has(b.id) && !b.pdf_url).map((b) => b.id),
        );
        return stillPending.size === prev.size ? prev : stillPending;
      });
    } catch {
      setError("자서전 목록을 불러오지 못했어요.");
    }
  }, [userId]);

  useEffect(() => {
    // loading 초기값이 이미 true라 여기서 다시 setLoading(true)를 부를 필요가 없다
    // (react-hooks/set-state-in-effect).
    void refresh().finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (pdfTriggeredIds.size === 0) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (!pollRef.current) {
      pollRef.current = setInterval(() => void refresh(), PDF_POLL_INTERVAL_MS);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [pdfTriggeredIds, refresh]);

  async function handleGeneratePdf(bookId: string) {
    setPdfTriggeredIds((prev) => new Set(prev).add(bookId));
    try {
      await adminApi.generateUserAutobiographyPdf(userId, bookId);
    } catch {
      setError("PDF를 만들지 못했어요.");
      setPdfTriggeredIds((prev) => {
        const next = new Set(prev);
        next.delete(bookId);
        return next;
      });
    }
  }

  return (
    <div>
      <p className="mb-2 text-sm font-medium text-black">완성한 자서전(PDF)</p>
      {loading && <p className="text-sm text-black/40">불러오는 중...</p>}
      {error && <p className="text-sm text-black/40">{error}</p>}
      {!loading && !error && books.length === 0 && (
        <p className="text-sm text-black/40">아직 완성한 자서전이 없어요.</p>
      )}
      <ul className="flex flex-col gap-3">
        {books.map((book) => {
          const pdfTriggered = pdfTriggeredIds.has(book.id);
          return (
            <li key={book.id} className="rounded-xl border border-black/10 p-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="text-black/70">
                  {book.title ?? "제목 없음"} ·{" "}
                  {new Date(book.updated_at).toLocaleDateString("ko-KR")} 완성
                </span>
                {book.pdf_url ? (
                  <a
                    href={book.pdf_url}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 rounded-full border border-black/10 px-3 py-1.5 text-black/60 underline"
                  >
                    다운로드
                  </a>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleGeneratePdf(book.id)}
                    disabled={pdfTriggered}
                    className="shrink-0 rounded-full border border-black/10 px-3 py-1.5 text-black/60 disabled:opacity-40"
                  >
                    {pdfTriggered ? "만드는 중..." : "PDF 생성"}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EmailUpdateForm({
  userId,
  currentEmail,
  onChanged,
}: {
  userId: string;
  currentEmail: string;
  onChanged: () => void;
}) {
  const [newEmail, setNewEmail] = useState(currentEmail);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newEmail.trim() === currentEmail) return;
    if (!confirm(`이 유저의 로그인 이메일을 "${newEmail.trim()}"로 바꿀까요?`)) return;
    setBusy(true);
    setMessage(null);
    adminApi
      .updateUserEmail(userId, newEmail.trim())
      .then(() => {
        setMessage("이메일이 변경됐어요.");
        onChanged();
      })
      .catch(() => setMessage("변경하지 못했어요."))
      .finally(() => setBusy(false));
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2 text-sm">
      <span className="w-24 shrink-0 text-black/50">이메일 변경</span>
      <input
        type="email"
        value={newEmail}
        onChange={(e) => setNewEmail(e.target.value)}
        className="flex-1 rounded-xl border border-black/10 px-3 py-1.5"
      />
      <button
        type="submit"
        disabled={busy}
        className="shrink-0 rounded-full border border-black/10 px-3 py-1.5 text-black/60 disabled:opacity-40"
      >
        저장
      </button>
      {message && <span className="text-black/40">{message}</span>}
    </form>
  );
}

function PasswordResetForm({ userId }: { userId: string }) {
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      setMessage("비밀번호는 8자 이상이어야 해요.");
      return;
    }
    if (!confirm("이 유저의 비밀번호를 재설정할까요?")) return;
    setBusy(true);
    setMessage(null);
    adminApi
      .resetUserPassword(userId, newPassword)
      .then(() => {
        setMessage("비밀번호가 재설정됐어요.");
        setNewPassword("");
      })
      .catch(() => setMessage("재설정하지 못했어요."))
      .finally(() => setBusy(false));
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2 text-sm">
      <span className="w-24 shrink-0 text-black/50">비밀번호 재설정</span>
      <input
        type="password"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        placeholder="새 비밀번호 (8자 이상)"
        className="flex-1 rounded-xl border border-black/10 px-3 py-1.5"
      />
      <button
        type="submit"
        disabled={busy}
        className="shrink-0 rounded-full border border-black/10 px-3 py-1.5 text-black/60 disabled:opacity-40"
      >
        저장
      </button>
      {message && <span className="text-black/40">{message}</span>}
    </form>
  );
}

function SessionProseEditor({
  userId,
  session,
  onChanged,
}: {
  userId: string;
  session: AdminSessionDetail;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.session_prose ?? "");
  const [busy, setBusy] = useState(false);

  const handleSave = () => {
    setBusy(true);
    adminApi
      .updateUserSessionProse(userId, session.id, draft)
      .then(() => {
        setEditing(false);
        onChanged();
      })
      .finally(() => setBusy(false));
  };

  return (
    <li className="rounded-xl border border-black/10 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-black/70">
          {SESSION_TYPE_LABEL[session.session_type] ?? session.session_type} · {session.status}
        </span>
        {session.session_prose !== null && (
          <button
            type="button"
            onClick={() => {
              setDraft(session.session_prose ?? "");
              setEditing((v) => !v);
            }}
            className="text-black/40 underline"
          >
            {editing ? "취소" : "산문 수정"}
          </button>
        )}
      </div>
      {editing ? (
        <div className="mt-2 flex flex-col gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={6}
            className="w-full rounded-xl border border-black/10 p-2"
          />
          <button
            type="button"
            onClick={handleSave}
            disabled={busy}
            className="self-end rounded-full border border-black/10 px-3 py-1.5 text-black/60 disabled:opacity-40"
          >
            {busy ? "저장 중..." : "저장"}
          </button>
        </div>
      ) : (
        <p className="mt-1 line-clamp-2 text-black/40">
          {session.session_prose ?? "(아직 산문 재조립 전)"}
        </p>
      )}
    </li>
  );
}
