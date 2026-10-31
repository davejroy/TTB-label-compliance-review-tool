/**
 * Tests for the demo access gate: login card, fail-open, and 401 session reset.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DemoLogin, { ACCESS_EXPIRED_MESSAGE } from "../components/DemoLogin";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DemoLogin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("renders children without a login card when auth is disabled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { auth_enabled: false })),
    );
    render(
      <DemoLogin>
        <div>App content</div>
      </DemoLogin>,
    );
    expect(await screen.findByText("App content")).toBeInTheDocument();
    expect(screen.queryByText("Demo access")).not.toBeInTheDocument();
  });

  it("clears the stored token and shows the login card when an API call returns 401", async () => {
    sessionStorage.setItem("ttb_demo_token", "stale-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("/api/demo-info")) {
          return Promise.resolve(jsonResponse(200, { auth_enabled: true }));
        }
        return Promise.resolve(
          jsonResponse(401, {
            error_code: "AUTH_REQUIRED",
            message: "Invalid or missing access token.",
          }),
        );
      }),
    );

    render(
      <DemoLogin>
        <div>App content</div>
      </DemoLogin>,
    );
    expect(await screen.findByText("App content")).toBeInTheDocument();

    const { reviewLabel } = await import("../api");
    const mockFile = new File(["img"], "label.jpg", { type: "image/jpeg" });
    const mockApp = {
      beverage_type: "distilled_spirits" as const,
      brand_name: "Test",
      class_type: "Bourbon",
      alcohol_content: "45%",
      net_contents: "750 mL",
    };
    await act(async () => {
      await expect(reviewLabel([mockFile], mockApp)).rejects.toMatchObject({ status: 401 });
    });

    await waitFor(() => {
      expect(screen.getByText("Demo access")).toBeInTheDocument();
      expect(screen.getByText(ACCESS_EXPIRED_MESSAGE)).toBeInTheDocument();
    });
    expect(screen.queryByText("App content")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("ttb_demo_token")).toBeNull();
  });
});
