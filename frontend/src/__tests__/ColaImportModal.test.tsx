import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ColaImportModal from '../components/ColaImportModal';
import type { ColaPreset } from '../types';

const mockPresets: ColaPreset[] = [
  {
    id: 'preset_bourbon',
    name: 'Bourbon Whiskey',
    description: 'Spirits 45% ABV',
    application: {
      beverage_type: 'distilled_spirits',
      brand_name: 'Old Tom Distillery',
      class_type: 'Kentucky Straight Bourbon Whiskey',
      alcohol_content: '45% Alc./Vol. (90 Proof)',
      net_contents: '750 mL',
      name_and_address: 'Old Tom Distillery, Bardstown, KY',
      country_of_origin: '',
    },
  },
];

vi.mock('../api', () => ({
  getColaPresets: vi.fn(() => Promise.resolve(mockPresets)),
  parseColaTemplate: vi.fn(),
}));

describe('ColaImportModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <ColaImportModal isOpen={false} onClose={vi.fn()} onSelectApplication={vi.fn()} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders modal with presets and template upload when isOpen is true', async () => {
    render(
      <ColaImportModal isOpen={true} onClose={vi.fn()} onSelectApplication={vi.fn()} />
    );

    expect(screen.getByText('Pre-fill COLA Application Data')).toBeInTheDocument();
    expect(screen.getByText('Upload CSV or JSON Template')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Old Tom Distillery')).toBeInTheDocument();
    });
  });

  it('calls onSelectApplication and onClose when a preset is clicked', async () => {
    const onSelectApplication = vi.fn();
    const onClose = vi.fn();

    render(
      <ColaImportModal
        isOpen={true}
        onClose={onClose}
        onSelectApplication={onSelectApplication}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Old Tom Distillery')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Old Tom Distillery'));
    expect(onSelectApplication).toHaveBeenCalledWith(mockPresets[0].application);
    expect(onClose).toHaveBeenCalled();
  });

  it('closes on Escape key press', () => {
    const onClose = vi.fn();
    render(
      <ColaImportModal
        isOpen={true}
        onClose={onClose}
        onSelectApplication={vi.fn()}
      />
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
