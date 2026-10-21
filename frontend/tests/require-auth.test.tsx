import { it, vi, expect } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { RequireAuth } from "@/components/auth/RequireAuth";
const { replace, me } = vi.hoisted(() => ({ replace: vi.fn(), me: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api/auth", () => ({ authApi: { me } }));
it("redirects an unauthenticated request without rendering private children", async () => {
  me.mockRejectedValue(new Error("unauthorized"));
  render(<RequireAuth><p>private manuscript</p></RequireAuth>);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  expect(screen.queryByText("private manuscript")).not.toBeInTheDocument();
});
