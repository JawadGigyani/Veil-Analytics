import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

export default [{
  files: ["**/*.{js,mjs,ts,tsx}"],
  ignores: [".next/**", "node_modules/**"],
  languageOptions: { parser: tsParser, globals: { process: "readonly", Request: "readonly", React: "readonly", fetch: "readonly", window: "readonly", File: "readonly", FormData: "readonly", URL: "readonly", URLSearchParams: "readonly" }, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
  plugins: { "@typescript-eslint": tsPlugin },
  rules: {
    "@typescript-eslint/no-unused-vars": "warn",
    "no-undef": "error"
  }
}];
