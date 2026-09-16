/**
 * Tests for src/components/StatusBadge.tsx
 *
 * Verifies that the correct label text, icon, and CSS class are
 * rendered for each of the three statuses: pass, warning, fail.
 * Also verifies the size prop classes.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StatusBadge from '../components/StatusBadge';

describe('StatusBadge', () => {
  it('renders "Pass" text for status="pass"', () => {
    render(<StatusBadge status="pass" />);
    expect(screen.getByText('Pass')).toBeInTheDocument();
  });

  it('renders "Needs Review" text for status="warning"', () => {
    render(<StatusBadge status="warning" />);
    expect(screen.getByText('Needs Review')).toBeInTheDocument();
  });

  it('renders "Fail" text for status="fail"', () => {
    render(<StatusBadge status="fail" />);
    expect(screen.getByText('Fail')).toBeInTheDocument();
  });

  it('applies green class for pass status', () => {
    const { container } = render(<StatusBadge status="pass" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('green');
  });

  it('applies amber class for warning status', () => {
    const { container } = render(<StatusBadge status="warning" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('amber');
  });

  it('applies red class for fail status', () => {
    const { container } = render(<StatusBadge status="fail" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('red');
  });

  it('applies larger text class for size="lg"', () => {
    const { container } = render(<StatusBadge status="pass" size="lg" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('text-lg');
  });

  it('applies smaller text class for size="sm"', () => {
    const { container } = render(<StatusBadge status="pass" size="sm" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('text-sm');
  });

  it('defaults to medium size when size prop is omitted', () => {
    const { container } = render(<StatusBadge status="pass" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('text-base');
  });

  it('renders the pass check-mark icon as aria-hidden', () => {
    const { container } = render(<StatusBadge status="pass" />);
    const icon = container.querySelector('[aria-hidden="true"]');
    expect(icon).toBeInTheDocument();
    expect(icon?.textContent).toBe('\u2713'); // ✓
  });

  it('renders the fail icon as aria-hidden', () => {
    const { container } = render(<StatusBadge status="fail" />);
    const icon = container.querySelector('[aria-hidden="true"]');
    expect(icon?.textContent).toBe('\u2715'); // ✕
  });
});
