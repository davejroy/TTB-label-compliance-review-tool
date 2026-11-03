import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import LabelImageViewer from '../components/LabelImageViewer';
import type { FieldLocation } from '../types';

function pngFile(name: string): File {
  return new File(['fake-png'], name, { type: 'image/png' });
}

describe('LabelImageViewer image_index binding', () => {
  it('draws a box only on the photo it was extracted from', () => {
    const files = [pngFile('front.png'), pngFile('back.png')];
    const locations: FieldLocation[] = [
      {
        field: 'government_warning',
        image_index: 1,
        confidence: 'high',
        x: 0.2,
        y: 0.6,
        width: 0.5,
        height: 0.2,
      },
    ];
    const { container } = render(
      <LabelImageViewer
        files={files}
        fieldLocations={locations}
        highlightedField="government_warning"
      />,
    );
    const overlays = container.querySelectorAll('.absolute.border-2');
    expect(overlays).toHaveLength(1);
    const panels = container.querySelectorAll('.relative.inline-block');
    expect(panels).toHaveLength(2);
    expect(panels[0].querySelectorAll('.absolute.border-2')).toHaveLength(0);
    expect(panels[1].querySelectorAll('.absolute.border-2')).toHaveLength(1);
  });
});
