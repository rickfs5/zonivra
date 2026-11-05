import { t } from "../i18n";
import { useLocale } from "../locale";
import { getResolvedTheme, setThemePref, THEMES, useThemePref, type ThemePref } from "../theme";
import { Panel } from "./ui";

const ICON: Record<ThemePref, string> = {
  light: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 17a5 5 0 100-10 5 5 0 000 10z",
  dark: "M21 13.2A8.5 8.5 0 1110.8 3a6.8 6.8 0 0010.2 10.2z",
  system: "M3 5h18v11H3zM8 20h8M12 16v4",
};
const Glyph = ({ k }: { k: ThemePref }) => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={ICON[k]} /></svg>;

/** Compact top-bar selector: a button group, each option exposes its state (aria-pressed) and is reachable/operable by keyboard. Purely presentational state (localStorage + <html data-theme>). */
export function ThemeSwitch() {
  useLocale();
  const pref = useThemePref();
  return (
    <div className="theme-switch" role="group" aria-label={t("mg.theme.group")} data-testid="theme-switch">
      {THEMES.map((k) => (
        <button key={k} aria-pressed={pref === k} aria-label={t(`mg.theme.switch_${k}`)} title={t(`mg.theme.switch_${k}`)} data-testid={`theme-${k}`} onClick={() => setThemePref(k)}><Glyph k={k} /><span>{t(`mg.theme.${k}`)}</span></button>))}
    </div>
  );
}

/** Settings card: the same three choices with a swatch preview, the active and the resolved appearance, and the explicit statement that nothing operational changes. */
export function AppearancePanel() {
  useLocale();
  const pref = useThemePref();
  return (
    <Panel title={t("mg.theme.title")} testid="cfg-appearance">
      <p className="muted" style={{ marginTop: 0 }}>{t("mg.theme.sub")}</p>
      <div className="theme-cards" role="group" aria-label={t("mg.theme.group")}>
        {THEMES.map((k) => (
          <button key={k} className="theme-card" aria-pressed={pref === k} data-testid={`theme-card-${k}`} onClick={() => setThemePref(k)}>
            <span className={`swatch sw-${k}`} aria-hidden="true"><i /><i /></span>
            <b>{t(`mg.theme.${k}`)}</b>
          </button>))}
      </div>
      <p data-testid="theme-active"><span className="muted">{t("mg.theme.pref")}</span> <b>{t(`mg.theme.${pref}`)}</b> · <span className="muted">{t("mg.theme.active")}</span> <b>{t(`mg.theme.${getResolvedTheme()}`)}</b></p>
      <p className="muted">{t("mg.theme.note")}</p>
    </Panel>
  );
}
