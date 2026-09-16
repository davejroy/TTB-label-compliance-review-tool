import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ResultsPanel from '../components/ResultsPanel';
import LabelCheckResultsPanel from '../components/LabelCheckResultsPanel';
import type { ReviewResult, LabelCheckResult } from '../types';

describe('ResultsPanel - processing time surfacing', () => {
  const mockReviewResult: ReviewResult = {
    filenames: ['test-label.jpg'],
    overall_status: 'pass',
    processing_time_ms: 2400,
    fields: [
      {
        field: 'brand_name',
        label_name: 'Brand Name',
        status: 'pass',
        application_value: 'Old Tom',
        label_value: 'Old Tom',
        message: 'Matches application',
      },
    ],
    extracted: {
      government_warning_present: true,
      field_locations: [],
    },
  };

  it('surfaces processing_time_ms in seconds', () => {
    render(<ResultsPanel result={mockReviewResult} files={[]} />);
    expect(screen.getByText('Processed in 2.4s')).toBeInTheDocument();
  });

  it('handles missing or zero processing_time_ms gracefully', () => {
    const withoutTime = { ...mockReviewResult, processing_time_ms: 0 };
    render(<ResultsPanel result={withoutTime} files={[]} />);
    expect(screen.queryByText(/Processed in/)).not.toBeInTheDocument();
  });
});

describe('LabelCheckResultsPanel - processing time surfacing', () => {
  const mockLabelCheckResult: LabelCheckResult = {
    filenames: ['label1.jpg'],
    overall_status: 'pass',
    beverage_type: 'distilled_spirits',
    processing_time_ms: 1850,
    checks: [
      {
        field: 'brand_name',
        label_name: 'Brand Name',
        status: 'pass',
        application_value: 'Required',
        label_value: 'Bourbon Brand',
        message: 'Present on label',
      },
    ],
    extracted: {
      government_warning_present: true,
      origin_guess: 'domestic',
      field_locations: [],
    },
  };

  it('surfaces processing_time_ms in summary line', () => {
    render(<LabelCheckResultsPanel result={mockLabelCheckResult} files={[]} />);
    expect(screen.getByText(/Processed in 1.9s/)).toBeInTheDocument();
  });
});
