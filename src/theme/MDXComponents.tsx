import React from 'react';
import MDXComponents from '@theme-original/MDXComponents';
import HubDownloadCard from '@site/src/components/HubDownloadCard';
import ResistorInterpreter, {
  ResistorLegendTable,
  ResistorWorkedExample,
} from '@site/src/components/ResistorInterpreter';

const MARKER_COMPONENTS: Array<{ marker: string; Component: React.ComponentType }> = [
  { marker: '#hub-download-card', Component: HubDownloadCard },
  { marker: '#resistor-legend', Component: ResistorLegendTable },
  { marker: '#resistor-example', Component: ResistorWorkedExample },
  { marker: '#resistor-interpreter', Component: ResistorInterpreter },
];

function findMarkerComponent(node: React.ReactNode): React.ComponentType | null {
  for (const child of React.Children.toArray(node)) {
    if (!React.isValidElement<{ href?: unknown; children?: React.ReactNode }>(child)) {
      continue;
    }
    if (typeof child.props.href === 'string') {
      for (const entry of MARKER_COMPONENTS) {
        if (child.props.href.includes(entry.marker)) {
          return entry.Component;
        }
      }
    }
    if (child.props.children) {
      const nested = findMarkerComponent(child.props.children);
      if (nested) {
        return nested;
      }
    }
  }
  return null;
}

export default {
  ...MDXComponents,
  HubDownloadCard,
  ResistorInterpreter,
  ResistorLegendTable,
  ResistorWorkedExample,
  p: (props: React.ComponentPropsWithoutRef<'p'>): React.ReactElement => {
    const MarkerComponent = findMarkerComponent(props.children);
    if (MarkerComponent) {
      return <MarkerComponent />;
    }
    const OriginalP = MDXComponents.p;
    if (OriginalP) {
      return <OriginalP {...props} />;
    }
    return <p {...props} />;
  },
};
