// Tailwind only runs if PostCSS is told to run it. Without this file the
// `@tailwind` lines in src/index.css were passed through untouched and the
// production build shipped ~200 bytes of CSS — i.e. a completely unstyled app.
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
