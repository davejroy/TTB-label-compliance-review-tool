import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WarningFormatView from "../components/WarningFormatView";
import type { WarningFormatResult, WarningTextDiff } from "../types";

const format: WarningFormatResult = {
  status: "advisory",
  required_min_height_mm: 2.0,
  max_characters_per_inch: 25,
  estimated_characters_per_inch: 32.4,
  findings: [
    {
      code: "header_bold",
      status: "advisory",
      citation: "27 CFR 16.22(a)(2)",
      message: "The 'GOVERNMENT WARNING' heading does not appear to be in bold type.",
      estimated: true,
    },
  ],
};

const textDiff: WarningTextDiff = {
  exact_match: false,
  expected_words: ["GOVERNMENT", "WARNING:", "women"],
  observed_words: ["GOVERNMENT", "WARNING:", "persons"],
  missing_words: [],
  extra_words: [],
  changed_words: [
    {
      kind: "changed",
      expected: "women",
      observed: "persons",
      expected_index: 2,
      observed_index: 2,
    },
  ],
};

describe("WarningFormatView", () => {
  it("renders the rolled-up 16.22 format status and statutory limits", () => {
    render(<WarningFormatView format={format} />);
    expect(screen.getByText(/27 CFR 16.22 Warning Format/i)).toBeInTheDocument();
    expect(screen.getByText("Advisory")).toBeInTheDocument();
    expect(screen.getByText("≥ 2.0 mm")).toBeInTheDocument();
    expect(screen.getByText("25")).toBeInTheDocument();
    expect(screen.getByText("32.4")).toBeInTheDocument();
    expect(screen.queryByText(/does not appear to be in bold type/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show details/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("expands findings and word-level diffs", () => {
    render(<WarningFormatView format={format} textDiff={textDiff} />);
    fireEvent.click(screen.getByRole("button", { name: /Show details/i }));
    expect(screen.getByRole("button", { name: /Hide details/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText(/27 CFR 16.22\(a\)\(2\)/)).toBeInTheDocument();
    expect(screen.getByText(/does not appear to be in bold type/i)).toBeInTheDocument();
    expect(screen.getByText(/Changed ‘women’ → ‘persons’/i)).toBeInTheDocument();
  });
});
