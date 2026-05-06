/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/renderer/**/*.{html,tsx,ts}'],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#1e1e1e',
          secondary: '#252526',
          tertiary: '#2d2d2d',
          hover: '#3c3c3c',
          active: '#094771',
        },
        fg: {
          primary: '#cccccc',
          secondary: '#858585',
          accent: '#569cd6',
        },
        border: {
          subtle: '#3c3c3c',
          focus: '#007acc',
        },
        status: {
          success: '#4caf50',
          warning: '#ff9800',
          error: '#f44336',
          info: '#2196f3',
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
