/**
 * Tests for Instructions button in App header and interaction.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "../App";

describe("App Header Instructions Button", () => {
  beforeEach(() => {
    // Mock demo-info endpoint to fail-open (auth_enabled: false)
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/demo-info")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ auth_enabled: false, username: "evaluator" }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        });
      })
    );
  });

  it("renders the Instructions button in the main app header without login", async () => {
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Open instructions and help guide/i })
      ).toBeInTheDocument();
    });

    const instructionsBtn = screen.getByRole("button", {
      name: /Open instructions and help guide/i,
    });
    expect(instructionsBtn).toHaveTextContent(/Instructions/i);

    // Click to open modal
    fireEvent.click(instructionsBtn);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText(/TTB Label Compliance Review Tool — User Guide/i)
    ).toBeInTheDocument();

    // Close the modal
    const closeBtn = screen.getByRole("button", { name: /Got it, Close Guide/i });
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
