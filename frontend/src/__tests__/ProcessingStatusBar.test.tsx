import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ProcessingStatusBar from '../components/ProcessingStatusBar';

describe('ProcessingStatusBar', () => {
  it('renders all 4 processing stage labels', () => {
    render(<ProcessingStatusBar step={0} done={false} />);
    expect(screen.getByText('Uploading')).toBeInTheDocument();
    expect(screen.getByText('Reading label')).toBeInTheDocument();
    expect(screen.getByText('Checking')).toBeInTheDocument();
    expect(screen.getByText('Complete')).toBeInTheDocument();
  });

  it('renders dynamic statusMessage when provided and not done', () => {
    render(
      <ProcessingStatusBar
        step={2}
        done={false}
        statusMessage="Server is waking up / analyzing with Claude Vision…"
      />
    );
    expect(
      screen.getByText('Server is waking up / analyzing with Claude Vision…')
    ).toBeInTheDocument();
  });

  it('does not render statusMessage when done=true', () => {
    render(
      <ProcessingStatusBar
        step={3}
        done={true}
        statusMessage="Server is waking up…"
      />
    );
    expect(screen.queryByText('Server is waking up…')).not.toBeInTheDocument();
  });

  it('marks active stage with primary styling when not done', () => {
    render(<ProcessingStatusBar step={1} done={false} />);
    const activeText = screen.getByText('Reading label');
    expect(activeText.className).toContain('text-[#15396a]');
  });

  it('marks all stages complete when done=true', () => {
    render(<ProcessingStatusBar step={3} done={true} />);
    const uploadingText = screen.getByText('Uploading');
    expect(uploadingText.className).toContain('text-green-600');
    const completeText = screen.getByText('Complete');
    expect(completeText.className).toContain('text-green-600');
  });
});
