/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        dark: {
          900: '#0a0a0f',
          850: '#0e0e16',
          800: '#12121a',
          700: '#1a1a26',
          600: '#22223a',
          500: '#2d2d4e',
        },
        accent: {
          green: '#00ff87',
          red: '#ff4466',
          blue: '#4488ff',
          yellow: '#ffd700',
          purple: '#9966ff',
        }
      }
    }
  },
  plugins: [],
}
