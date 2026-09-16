/**
 * Tests for src/components/InstructionsModal.tsx and its integration.
 *
 * Verifies that:
 * 1. The modal does not render when isOpen=false.
 * 2. When isOpen=true, it renders all required sections:
 *    - What the tool does (COLA match vs Label-Only)
 *    - How to upload / take photos (front+back, multi-photo merge, photo roles)
 *    - Photo tips (lighting, flat/focus, avoid glare)
 *    - What happens on review (Pass / Needs Review / Fail)
 *    - Retake vs fail (LowConfidence retake prompts vs hard compliance fails)
 *    - Government Warning exact casing (GOVERNMENT WARNING:)
 *    - Formula-dependent warnings (sulfites/allergens needing production records)
 *    - Known gaps (type-size 27 CFR 16.22 and T.D. TTB-200 fill modernization)
 * 3. Close callbacks fire on close button click, backdrop click, and Escape key press.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import InstructionsModal from "../components/InstructionsModal";

describe("InstructionsModal", () => {
  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <InstructionsModal isOpen={false} onClose={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders dialog and all core instructions when isOpen is true", () => {
    render(<InstructionsModal isOpen={true} onClose={() => {}} />);

    // Dialog presence
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /TTB Label Compliance Review Tool — User Guide/i })
    ).toBeInTheDocument();

    // 1. What the tool does
    expect(screen.getByText(/What the Tool Does/i)).toBeInTheDocument();
    expect(screen.getByText(/COLA Application Match/i)).toBeInTheDocument();
    expect(screen.getByText(/Label-Only Check/i)).toBeInTheDocument();

    // 2. How to upload / photo roles
    expect(screen.getByText(/How to Upload & Photo Roles/i)).toBeInTheDocument();
    expect(screen.getByText(/Front \+ Back Panels:/i)).toBeInTheDocument();
    expect(screen.getByText(/Multi-Photo Merging:/i)).toBeInTheDocument();

    // 3. Photo tips
    expect(screen.getByText(/Photo Tips for Optimal Accuracy/i)).toBeInTheDocument();
    expect(screen.getByText(/Flat & Focused:/i)).toBeInTheDocument();
    expect(screen.getByText(/Even Lighting & Avoid Glare:/i)).toBeInTheDocument();

    // 4. What happens on review (Pass / Needs Review / Fail)
    expect(screen.getByText(/Understanding Review Results/i)).toBeInTheDocument();
    expect(screen.getByText(/✓ Pass/i)).toBeInTheDocument();
    expect(screen.getByText(/⚠ Needs Review/i)).toBeInTheDocument();
    expect(screen.getByText(/✕ Fail/i)).toBeInTheDocument();

    // 5. Retake vs fail
    expect(screen.getByText(/Retake Prompts vs. Hard Compliance Fails/i)).toBeInTheDocument();
    expect(screen.getByText(/LowConfidence Retake Prompts:/i)).toBeInTheDocument();
    expect(screen.getByText(/Hard Compliance Fails:/i)).toBeInTheDocument();

    // 6. Government Warning casing & formula-dependent warnings
    expect(screen.getByText(/Government Warning Exact Casing/i)).toBeInTheDocument();
    expect(screen.getByText("GOVERNMENT WARNING:")).toBeInTheDocument();
    expect(screen.getByText(/Formula-Dependent Warnings \(Sulfites & Allergens\):/i)).toBeInTheDocument();
    expect(screen.getByText(/production records/i)).toBeInTheDocument();

    // 7. Known gaps
    expect(screen.getByText(/Known Gaps & Current Scope/i)).toBeInTheDocument();
    expect(screen.getByText(/27 CFR 16\.22/i)).toBeInTheDocument();
    expect(screen.getByText(/T\.D\. TTB-200/i)).toBeInTheDocument();
  });

  it("calls onClose when the close icon button is clicked", () => {
    const handleClose = vi.fn();
    render(<InstructionsModal isOpen={true} onClose={handleClose} />);

    const closeBtn = screen.getByLabelText(/Close instructions guide/i);
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the bottom footer button is clicked", () => {
    const handleClose = vi.fn();
    render(<InstructionsModal isOpen={true} onClose={handleClose} />);

    const footerBtn = screen.getByRole("button", { name: /Got it, Close Guide/i });
    fireEvent.click(footerBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the backdrop overlay is clicked", () => {
    const handleClose = vi.fn();
    render(<InstructionsModal isOpen={true} onClose={handleClose} />);

    const dialog = screen.getByRole("dialog");
    fireEvent.click(dialog);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape key is pressed", () => {
    const handleClose = vi.fn();
    render(<InstructionsModal isOpen={true} onClose={handleClose} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
