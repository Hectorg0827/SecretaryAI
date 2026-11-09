/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#eff6ff',
          100: '#dbeafe',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        // Lola — executive palette (navy / charcoal / refined gold)
        lola: {
          ink:     '#070B14',
          navy:    '#0B1324',
          deep:    '#101A30',
          panel:   '#131C2E',
          line:    '#24304A',
          mute:    '#8C97AD',
          text:    '#E8ECF4',
          gold:    '#D6A84E',
          amber:   '#F0C674',
          glow:    '#FFE2A3',
          danger:  '#E07A6B',
          ok:      '#6FCF97',
        },
      },
      boxShadow: {
        lola: '0 24px 60px -20px rgb(3 7 18 / 0.65), 0 0 0 1px rgb(214 168 78 / 0.10)',
        'lola-orb': '0 10px 30px -6px rgb(214 168 78 / 0.45), 0 0 0 1px rgb(214 168 78 / 0.25)',
        card: '0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.04)',
        'card-md': '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.05)',
      },
      animation: {
        'fade-in':   'fadeIn 0.15s ease-out',
        'slide-down':'slideDown 0.2s ease-out',
      },
      keyframes: {
        fadeIn:    { from: { opacity: '0' }, to: { opacity: '1' } },
        slideDown: { from: { opacity: '0', transform: 'translateY(-6px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
    },
  },
  plugins: [],
};
