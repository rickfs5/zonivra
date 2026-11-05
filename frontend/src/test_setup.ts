import { setLocale } from "./locale";

// Existing suites assert Spanish wording; they keep running in Spanish. Localization tests select a locale explicitly.
setLocale("es", false);
