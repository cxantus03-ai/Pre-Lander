/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#07080a", 900: "#0c0e11", 850: "#111418", 800: "#161a1f", 700: "#1f252c", 600: "#2a313a", 500: "#3a434e" },
        pos: { DEFAULT: "#3fb37f", dim: "#1d4d38" },
        warn: { DEFAULT: "#d4a23a", dim: "#4d3b16" },
        neg: { DEFAULT: "#e05d5d", dim: "#4d2020" },
        mute: { DEFAULT: "#8a94a0", dim: "#5b6470" },
        accent: "#5aa9e6",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "Liberation Mono", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      fontSize: { xxs: ["10px", "14px"] },
    },
  },
  plugins: [],
};
