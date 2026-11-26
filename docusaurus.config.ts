import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'BCI Workshop 2026',
  tagline: 'Brain-Computer Interface Workshop — Bar-Ilan University',
  url: 'https://urikorisky.github.io',
  baseUrl: '/BIU_BCI_Workshop_2026_StudentResources/',
  organizationName: 'urikorisky',
  projectName: 'BIU_BCI_Workshop_2026_StudentResources',
  trailingSlash: false,

  onBrokenLinks: 'warn',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'he',
    locales: ['he', 'en'],
    localeConfigs: {
      he: {
        label: 'עברית',
        direction: 'rtl',
        htmlLang: 'he',
      },
      en: {
        label: 'English',
        direction: 'ltr',
        htmlLang: 'en',
      },
    },
  },

  presets: [
    [
      'classic',
      {
        docs: {
          path: 'he',
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'BCI Workshop 2026',
      items: [
        {
          type: 'localeDropdown',
          position: 'right',
        },
        {
          href: 'https://github.com/urikorisky/BIU_BCI_Workshop_2026_StudentResources',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `BCI Workshop 2026 — Bar-Ilan University`,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
