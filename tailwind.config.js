/** Tailwind build config for Fintoit.
 *  Rebuild after adding utility classes:
 *    ./tailwindcss -c tailwind.config.js -i tailwind.input.css -o static/css/tailwind.css --minify
 *  (standalone CLI: https://github.com/tailwindlabs/tailwindcss/releases) */
module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Bricolage Grotesque','ui-sans-serif','system-ui','sans-serif'],
        mono: ['DM Mono','ui-monospace','monospace'],
        serif: ['Lora','ui-serif','Georgia','serif'],
      },
      colors: {
        ink: '#0a0a0f', paper: '#f7f6f2', muted: '#6b6b7a',
        brand: { blue: '#1d4ed8', green: '#16a34a' },
      },
    },
  },
  safelist: [
    { pattern: /^(bg|text|border|from|to|via|ring)-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|blue|indigo|violet|purple|fuchsia|pink|rose|gray|slate|zinc|neutral|stone)-(50|100|200|300|400|500|600|700|800|900)$/ },
  ],
};
