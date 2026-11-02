/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: 'rgb(var(--bg-primary) / <alpha-value>)',
          secondary: 'rgb(var(--bg-secondary) / <alpha-value>)',
          tertiary: 'rgb(var(--bg-tertiary) / <alpha-value>)',
          hover: 'rgb(var(--bg-hover) / <alpha-value>)',
          active: 'rgb(var(--bg-active) / <alpha-value>)',
        },
        workspace: 'rgb(var(--workspace) / <alpha-value>)',
        fg: {
          primary: 'rgb(var(--fg-primary) / <alpha-value>)',
          secondary: 'rgb(var(--fg-secondary) / <alpha-value>)',
          accent: 'rgb(var(--accent) / <alpha-value>)',
        },
        border: {
          subtle: 'rgb(var(--separator) / <alpha-value>)',
          focus: 'rgb(var(--control-accent) / <alpha-value>)',
        },
        status: {
          success: 'rgb(var(--success) / <alpha-value>)',
          warning: 'rgb(var(--warning) / <alpha-value>)',
          error: 'rgb(var(--error) / <alpha-value>)',
          info: 'rgb(var(--accent) / <alpha-value>)',
        },
      },
      width: {
        sidebar: '240px',
        'sidebar-collapsed': '56px',
      },
    },
  },
  plugins: [],
}
