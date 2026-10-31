import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SingleReview from "../components/SingleReview";
import { VISION_FAILURES, pngFile, visionApiError } from "./visionFailureCases";
import type { ApplicationData, ReviewResult } from "../types";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    reviewLabel: vi.fn(),
    getColaPresets: vi.fn().mockResolvedValue([]),
  };
});

import { reviewLabel } from "../api";

const mockedReview = vi.mocked(reviewLabel);

function errorResult(overrides: Partial<ReviewResult> = {}): ReviewResult {
  return {
    filenames: ["front.png"],
    overall_status: "error",
    fields: [],
    extracted: { government_warning_present: false, field_locations: [] },
    processing_time_ms: 12,
    error: "The label reader timed out — your photo is fine. Try again shortly.",
    error_code: "UPSTREAM_TIMEOUT",
    request_id: "req-prefill-1",
    ...overrides,
  };
}

describe("SingleReview pre-fill and failed review rail", () => {
  beforeEach(() => {
    mockedReview.mockReset();
  });

  async function uploadPng() {
    const user = userEvent.setup();
    render(<SingleReview />);
    await user.upload(screen.getByLabelText(/Label Images/i), pngFile());
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /pre-fill fields from label photo/i })).toBeEnabled();
    });
    return user;
  }

  async function fillApplication(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/brand name/i), "North Star");
    await user.type(screen.getByLabelText(/class \/ type/i), "Bourbon");
    await user.type(screen.getByLabelText(/alcohol content/i), "45%");
    await user.type(screen.getByLabelText(/net contents/i), "750 mL");
  }

  it("shows a clear pre-fill failure and does not fill fields when vision returns an error body", async () => {
    mockedReview.mockResolvedValue(errorResult());
    const user = await uploadPng();
    await user.click(screen.getByRole("button", { name: /pre-fill fields from label photo/i }));

    await waitFor(() => {
      expect(screen.getByText(/the label reader timed out/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/fields pre-filled from label/i)).not.toBeInTheDocument();
    const brand = screen.getByLabelText(/brand name/i) as HTMLInputElement;
    expect(brand.value).toBe("");
  });

  it.each(VISION_FAILURES)(
    "does not treat $code ($status) as a pre-fill success",
    async (failure) => {
      mockedReview.mockRejectedValue(visionApiError(failure));
      const user = await uploadPng();
      await user.click(screen.getByRole("button", { name: /pre-fill fields from label photo/i }));

      await waitFor(() => {
        expect(screen.getByText(failure.message)).toBeInTheDocument();
      });
      expect(screen.queryByText(/fields pre-filled from label/i)).not.toBeInTheDocument();
      expect((screen.getByLabelText(/brand name/i) as HTMLInputElement).value).toBe("");
      expect((screen.getByLabelText(/brand name/i) as HTMLInputElement).value).not.toBe("Old Tom Distillery");
    },
  );

  it("does not merge extracted fields from a failed pre-fill", async () => {
    mockedReview.mockResolvedValue(
      errorResult({
        extracted: {
          brand_name: "SHOULD NOT APPEAR",
          government_warning_present: false,
          field_locations: [],
        },
      }),
    );
    const user = await uploadPng();
    await user.click(screen.getByRole("button", { name: /pre-fill fields from label photo/i }));
    await waitFor(() => {
      expect(screen.getByText(/timed out/i)).toBeInTheDocument();
    });
    const brand = screen.getByLabelText(/brand name/i) as HTMLInputElement;
    expect(brand.value).toBe("");
    expect(brand.value).not.toBe("SHOULD NOT APPEAR");
  });

  it.each(VISION_FAILURES)(
    "single review $code ends on a red Failed rail, not green Complete",
    async (failure) => {
      mockedReview.mockRejectedValue(visionApiError(failure));
      const user = await uploadPng();
      await fillApplication(user);
      await user.click(screen.getByRole("button", { name: /step 3: review label/i }));

      await waitFor(() => {
        expect(screen.getByText("Failed")).toBeInTheDocument();
      });
      expect(screen.queryByText("Complete")).not.toBeInTheDocument();
      expect(screen.getByTestId("error-result-card")).toBeInTheDocument();
      expect(screen.getByTestId("error-code")).toHaveTextContent(failure.code);
      expect(screen.queryByText(/^Pass$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^Fail$/)).not.toBeInTheDocument();
      expect(screen.getByText("Failed").className).toContain("text-red-700");
    },
  );
});

void ({} as ApplicationData);
