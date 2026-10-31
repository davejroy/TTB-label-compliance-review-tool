import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BatchReview from "../components/BatchReview";
import { VISION_FAILURES, pngFile, visionApiError } from "./visionFailureCases";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    reviewLabelsBatchStream: vi.fn(),
    getColaPresets: vi.fn().mockResolvedValue([]),
  };
});

import { reviewLabelsBatchStream } from "../api";

const mockedStream = vi.mocked(reviewLabelsBatchStream);

describe("BatchReview vision failures", () => {
  beforeEach(() => {
    mockedStream.mockReset();
  });

  async function prepareOneLabel() {
    const user = userEvent.setup();
    render(<BatchReview />);
    const removeButtons = screen.getAllByRole("button", { name: /remove/i });
    await user.click(removeButtons[removeButtons.length - 1]);

    await user.type(screen.getByLabelText(/brand name/i), "North Star");
    await user.type(screen.getByLabelText(/class \/ type/i), "Bourbon");
    await user.type(screen.getByLabelText(/alcohol content/i), "45%");
    await user.type(screen.getByLabelText(/net contents/i), "750 mL");
    await user.upload(screen.getByLabelText(/Label Images/i), pngFile());

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /review 1 label/i })).toBeEnabled();
    });
    return user;
  }

  it.each(VISION_FAILURES)(
    "handles $code without a green completion",
    async (failure) => {
      mockedStream.mockRejectedValue(visionApiError(failure));
      const user = await prepareOneLabel();
      await user.click(screen.getByRole("button", { name: /review 1 label/i }));

      await waitFor(() => {
        expect(screen.getByTestId("error-result-card")).toBeInTheDocument();
      });
      expect(screen.getByTestId("error-code")).toHaveTextContent(failure.code);
      expect(screen.queryByText(/^Pass$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/fields pre-filled from label/i)).not.toBeInTheDocument();
      expect(screen.getByText(/Label 1 — (processing|waiting)/i)).toBeInTheDocument();
    },
  );
});
