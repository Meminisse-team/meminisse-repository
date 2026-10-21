/** Tokens live exclusively in server-managed HttpOnly cookies. Remove legacy storage. */
export function clearLegacySession() {
  if (typeof window === "undefined") return;
  for (const key of ["meminisse.access_token", "meminisse.refresh_token", "meminisse.user_id"]) window.localStorage.removeItem(key);
  window.sessionStorage.removeItem("meminisse.signup_draft");
}
