import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConfidenceBadge from '../components/ConfidenceBadge';

describe('ConfidenceBadge', () => {
  it('renders "High confidence" for confidence="high"', () => {
    render(<ConfidenceBadge confidence="high" />);
    expect(screen.getByText('High confidence')).toBeInTheDocument();
  });

  it('renders "Medium confidence" for confidence="medium"', () => {
    render(<ConfidenceBadge confidence="medium" />);
    expect(screen.getByText('Medium confidence')).toBeInTheDocument();
  });

  it('renders "Low confidence" for confidence="low"', () => {
    render(<ConfidenceBadge confidence="low" />);
    expect(screen.getByText('Low confidence')).toBeInTheDocument();
  });

  it('applies green class for high confidence', () => {
    const { container } = render(<ConfidenceBadge confidence="high" />);
    expect(container.querySelector('span')?.className).toContain('green');
  });

  it('applies amber class for medium confidence', () => {
    const { container } = render(<ConfidenceBadge confidence="medium" />);
    expect(container.querySelector('span')?.className).toContain('amber');
  });

  it('applies red class for low confidence', () => {
    const { container } = render(<ConfidenceBadge confidence="low" />);
    expect(container.querySelector('span')?.className).toContain('red');
  });

  it('renders as an inline span element', () => {
    const { container } = render(<ConfidenceBadge confidence="high" />);
    const span = container.querySelector('span');
    expect(span).toBeInTheDocument();
    expect(span?.tagName.toLowerCase()).toBe('span');
  });

  it('includes rounded-full styling class', () => {
    const { container } = render(<ConfidenceBadge confidence="low" />);
    expect(container.querySelector('span')?.className).toContain('rounded-full');
  });
});
