import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LabelOnlyCheck from "../components/LabelOnlyCheck";
import { VISION_FAILURES, pngFile, visionApiError } from "./visionFailureCases";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    checkLabelsBatch: vi.fn(),
  };
});

import { checkLabelsBatch } from "../api";

const mockedCheck = vi.mocked(checkLabelsBatch);

describe("LabelOnlyCheck vision failures", () => {
  beforeEach(() => {
    mockedCheck.mockReset();
  });

  async function uploadAndSubmit() {
    const user = userEvent.setup();
    render(<LabelOnlyCheck />);
    await user.upload(screen.getByLabelText(/Label Images/i), pngFile());
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /check 1 label/i })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: /check 1 label/i }));
    return user;
  }

  it.each(VISION_FAILURES)(
    "handles $code without a green Complete rail",
    async (failure) => {
      mockedCheck.mockRejectedValue(visionApiError(failure));
      await uploadAndSubmit();

      await waitFor(() => {
        expect(screen.getByText("Failed")).toBeInTheDocument();
      });
      expect(screen.queryByText("Complete")).not.toBeInTheDocument();
      expect(screen.getByTestId("error-result-card")).toBeInTheDocument();
      expect(screen.getByTestId("error-code")).toHaveTextContent(failure.code);
      expect(screen.queryByText(/^Pass$/)).not.toBeInTheDocument();
      expect(screen.getByText("Failed").className).toContain("text-red-700");
    },
  );
});
