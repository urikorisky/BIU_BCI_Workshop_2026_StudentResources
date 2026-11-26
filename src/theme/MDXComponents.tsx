import React from 'react';
import MDXComponents from '@theme-original/MDXComponents';
import HubDownloadCard from '@site/src/components/HubDownloadCard';

const HUB_DOWNLOAD_MARKER = '#hub-download-card';

function hasHubDownloadMarker(node: React.ReactNode): boolean {
  for (const child of React.Children.toArray(node)) {
    if (!React.isValidElement<{ href?: unknown; children?: React.ReactNode }>(child)) {
      continue;
    }
    if (
      typeof child.props.href === 'string' &&
      child.props.href.includes(HUB_DOWNLOAD_MARKER)
    ) {
      return true;
    }
    if (child.props.children && hasHubDownloadMarker(child.props.children)) {
      return true;
    }
  }
  return false;
}

export default {
  ...MDXComponents,
  HubDownloadCard,
  p: (props: React.ComponentPropsWithoutRef<'p'>): React.ReactElement => {
    if (hasHubDownloadMarker(props.children)) {
      return <HubDownloadCard />;
    }
    const OriginalP = MDXComponents.p;
    if (OriginalP) {
      return <OriginalP {...props} />;
    }
    return <p {...props} />;
  },
};
