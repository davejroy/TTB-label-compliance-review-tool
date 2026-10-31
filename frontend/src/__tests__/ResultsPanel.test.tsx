import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ResultsPanel from '../components/ResultsPanel';
import LabelCheckResultsPanel from '../components/LabelCheckResultsPanel';
import type { ReviewResult, LabelCheckResult } from '../types';
import { RETAKE_MESSAGE } from '../errorContract';

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

describe('ResultsPanel - retake, error, and fail states', () => {
  const baseExtracted = {
    government_warning_present: true,
    field_locations: [],
  };

  it('renders a distinct retake card from error_code and status, not message matching', async () => {
    const user = userEvent.setup();
    const onRetake = vi.fn();
    const result: ReviewResult = {
      filenames: ['blurry.jpg'],
      overall_status: 'retake',
      processing_time_ms: 1200,
      error: RETAKE_MESSAGE,
      error_code: 'PHOTO_UNREADABLE_FIELD',
      request_id: 'req-retake-1',
      retake: {
        field: 'ABV',
        area: 'front',
        cause: 'glare',
        tip: 'Hold the phone parallel to the label and avoid overhead light glare.',
      },
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
      extracted: baseExtracted,
    };

    render(<ResultsPanel result={result} files={[]} onRetake={onRetake} />);
    expect(screen.getByTestId('retake-card')).toBeInTheDocument();
    expect(screen.getByText(RETAKE_MESSAGE)).toBeInTheDocument();
    expect(screen.getByText('ABV')).toBeInTheDocument();
    expect(screen.getByText('Glare')).toBeInTheDocument();
    expect(screen.getByText(/Hold the phone parallel/)).toBeInTheDocument();
    expect(screen.getByText('Brand Name')).toBeInTheDocument();
    expect(screen.queryByTestId('error-result-card')).not.toBeInTheDocument();
    expect(screen.queryByText('Could not process label')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Retake front photo/i }));
    expect(onRetake).toHaveBeenCalledWith('front');
  });

  it('renders a distinct error card with request_id', () => {
    const result: ReviewResult = {
      filenames: ['bad.jpg'],
      overall_status: 'error',
      processing_time_ms: 80,
      error: 'The review service timed out. Please try again.',
      error_code: 'UPSTREAM_TIMEOUT',
      request_id: 'req-error-99',
      fields: [],
      extracted: { government_warning_present: false, field_locations: [] },
    };

    render(<ResultsPanel result={result} files={[]} />);
    expect(screen.getByTestId('error-result-card')).toBeInTheDocument();
    expect(screen.getByText('Could not process label')).toBeInTheDocument();
    expect(screen.getByText('The review service timed out. Please try again.')).toBeInTheDocument();
    expect(screen.getByTestId('request-id')).toHaveTextContent('req-error-99');
    expect(screen.queryByTestId('retake-card')).not.toBeInTheDocument();
    expect(screen.queryByText('Fail')).not.toBeInTheDocument();
  });

  it('renders a fail state for a real compliance violation', () => {
    const result: ReviewResult = {
      filenames: ['label.jpg'],
      overall_status: 'fail',
      processing_time_ms: 1800,
      fields: [
        {
          field: 'government_warning',
          label_name: 'Government Warning',
          status: 'fail',
          application_value: 'Required',
          label_value: '',
          message: 'Government Warning statement not found on label.',
        },
      ],
      extracted: { government_warning_present: false, field_locations: [] },
    };

    render(<ResultsPanel result={result} files={[]} />);
    expect(screen.getAllByText('Fail').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Government Warning statement not found on label.')).toBeInTheDocument();
    expect(screen.queryByTestId('retake-card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('error-result-card')).not.toBeInTheDocument();
    expect(screen.queryByText(RETAKE_MESSAGE)).not.toBeInTheDocument();
  });
});

describe('LabelCheckResultsPanel - retake and error states', () => {
  it('renders a retake card with a Retake button', async () => {
    const user = userEvent.setup();
    const onRetake = vi.fn();
    const result: LabelCheckResult = {
      filenames: ['back.jpg'],
      overall_status: 'retake',
      beverage_type: 'wine',
      processing_time_ms: 900,
      error: RETAKE_MESSAGE,
      error_code: 'PHOTO_QUALITY_LOW',
      request_id: 'req-label-retake',
      retake: {
        field: 'warning',
        area: 'back',
        cause: 'blur',
        tip: 'Hold the phone still and tap the label to focus before you take the photo.',
      },
      checks: [],
      extracted: { government_warning_present: true, origin_guess: 'unknown', field_locations: [] },
    };
    render(<LabelCheckResultsPanel result={result} files={[]} onRetake={onRetake} />);
    expect(screen.getByTestId('retake-card')).toBeInTheDocument();
    expect(screen.getByText(RETAKE_MESSAGE)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Retake back photo/i }));
    expect(onRetake).toHaveBeenCalledWith('back');
  });

  it('renders an error card with request_id', () => {
    const result: LabelCheckResult = {
      filenames: ['notes.jpg'],
      overall_status: 'error',
      processing_time_ms: 20,
      error: 'File is not a supported image.',
      error_code: 'UNSUPPORTED_FILE',
      request_id: 'req-label-error',
      checks: [],
      extracted: { government_warning_present: false, field_locations: [] },
    };
    render(<LabelCheckResultsPanel result={result} files={[]} />);
    expect(screen.getByTestId('error-result-card')).toBeInTheDocument();
    expect(screen.getByTestId('request-id')).toHaveTextContent('req-label-error');
    expect(screen.queryByTestId('retake-card')).not.toBeInTheDocument();
  });
});
