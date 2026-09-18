import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import TypeSizeDetailsView from "../components/TypeSizeDetailsView";
import type { TypeSizeMeasurement } from "../types";

describe("TypeSizeDetailsView", () => {
  it("renders calibrated pass measurement correctly", () => {
    const details: TypeSizeMeasurement = {
      method: "scale_marker_card_id1",
      pixels_per_mm: 14.5,
      measured_capital_height_mm: 2.24,
      required_min_height_mm: 2.0,
      uncertainty_mm: 0.08,
      skew_angle_deg: 4.2,
      state: "pass",
      verification_note:
        "Calibrated capital letter height 2.24 mm meets or exceeds statutory 27 CFR 16.22 requirement (2.0 mm).",
    };

    render(<TypeSizeDetailsView details={details} />);

    expect(
      screen.getByText(/27 CFR 16.22 Type Size Calibration: ISO\/IEC 7810 ID-1 Card/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Pass (Calibrated mm)")).toBeInTheDocument();
    expect(screen.getByText("≥ 2.0 mm")).toBeInTheDocument();
    expect(screen.getByText("2.24 mm")).toBeInTheDocument();
    expect(screen.getByText("14.5 px/mm")).toBeInTheDocument();
    expect(screen.getByText(/± 0.08 mm/i)).toBeInTheDocument();
    expect(
      screen.getByText(/meets or exceeds statutory 27 CFR 16.22 requirement/i)
    ).toBeInTheDocument();
  });

  it("renders deficient type size fail state correctly", () => {
    const details: TypeSizeMeasurement = {
      method: "scale_marker_card_id1",
      pixels_per_mm: 12.0,
      measured_capital_height_mm: 1.42,
      required_min_height_mm: 2.0,
      uncertainty_mm: 0.06,
      skew_angle_deg: 3.1,
      state: "fail",
      verification_note:
        "Calibrated capital letter height 1.42 mm is below the statutory 27 CFR 16.22 requirement (2.0 mm).",
    };

    render(<TypeSizeDetailsView details={details} />);

    expect(screen.getByText("Deficient Type Size")).toBeInTheDocument();
    expect(screen.getByText("1.42 mm")).toBeInTheDocument();
    expect(screen.getByText(/is below the statutory 27 CFR 16.22 requirement/i)).toBeInTheDocument();
  });

  it("renders cannot_measure retake state correctly", () => {
    const details: TypeSizeMeasurement = {
      method: "scale_marker_card_id1",
      required_min_height_mm: 2.0,
      state: "cannot_measure",
      verification_note:
        "Scale marker perspective tilt (28.4°) exceeds maximum 25.0° threshold. Hold camera parallel.",
    };

    render(<TypeSizeDetailsView details={details} />);

    expect(screen.getByText("Cannot Measure (Retake Advised)")).toBeInTheDocument();
    expect(screen.getByText("N/A (uncalibrated)")).toBeInTheDocument();
    expect(screen.getByText(/Scale marker perspective tilt/i)).toBeInTheDocument();
  });

  it("renders uncalibrated estimate advisory state correctly", () => {
    const details: TypeSizeMeasurement = {
      method: "uncalibrated_estimate",
      required_min_height_mm: 2.0,
      state: "warning",
      verification_note:
        "Uncalibrated photo without physical scale marker. Statutory threshold is >=2.0 mm.",
    };

    render(<TypeSizeDetailsView details={details} />);

    expect(
      screen.getByText(/27 CFR 16.22 Type Size Calibration: Uncalibrated Photo/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Needs Review / Advisory")).toBeInTheDocument();
  });
});
