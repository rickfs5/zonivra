import { t, tf } from "../i18n";
import { Icon, ProvTag, type Provenance } from "../shared/ui";
import { EVIDENCE_CLASSES, type EvidenceClass, type HeroFacts } from "./heroState";

type Tone = "ok" | "warn" | "err" | "idle";
const GLYPH: Record<Tone, string> = { ok: "●", warn: "▲", err: "■", idle: "○" };
function Tile({ label, value, tone, testid, facts }: { label: string; value: string; tone: Tone; testid: string; facts?: string }) {
  return (
    <div className={`hero-tile tone-${tone}`} data-testid={testid} data-tone={tone}>
      <span className="hero-tile-l">{label}</span>
      <span className="hero-tile-v"><span className="hero-glyph" aria-hidden="true">{GLYPH[tone]}</span>{value}</span>
      {facts ? <span className="hero-tile-f mono">{facts}</span> : null}
    </div>
  );
}

/** Dashboard hero (UI-REVAMP-22). Every value comes from `HeroFacts` (the monitor API); empty and idle states are stated, never dressed up as health. */
export function OverviewHero({ facts, modelId, go }: { facts: HeroFacts; modelId: string | null; go: (s: string) => void }) {
  const modelTone: Tone = facts.model === "error" ? "err" : facts.model === "loaded" ? "ok" : "idle";
  const analysisTone: Tone = facts.analysis === "idle" ? "idle" : "ok";
  const srcTone: Tone = facts.failedFeeds > 0 ? "err" : facts.sources.active > 0 ? "ok" : "idle";
  const ctas: [string, string, string, string][] = [["video", "mg.hero.cta_video", "mg.hero.cta_video_d", "hero-go-video"], ["camara", "mg.hero.cta_cam", "mg.hero.cta_cam_d", "hero-go-cam"],
    ["simulaciones", "mg.hero.cta_scn", "mg.hero.cta_scn_d", "hero-go-sim"], ["configuracion", "mg.hero.cta_ext", "mg.hero.cta_ext_d", "hero-go-ext"]];
  return (
    <section className="hero" data-testid="ov-hero" aria-labelledby="hero-title">
      <div className="hero-head">
        <div><p className="hero-eyebrow">{t("mg.hero.eyebrow")}</p><h3 id="hero-title" className="hero-title">{t("mg.brand")}</h3><p className="hero-tagline">{t("mg.hero.tagline")}</p></div>
      </div>
      <div className="hero-tiles">
        <Tile label={t("mg.hero.mode")} value={t("mg.hero.mode_val")} tone="ok" testid="hero-mode" />
        <Tile label={t("mg.hero.model")} value={t(`mg.hero.model.${facts.model}`)} tone={modelTone} testid="hero-model" facts={modelId ?? undefined} />
        <Tile label={t("mg.hero.sources")} value={tf("mg.hero.sources_val", { active: facts.sources.active, limit: facts.sources.limit })} tone={srcTone} testid="hero-sources" />
        <Tile label={t("mg.hero.analysis")} value={t(`mg.hero.analysis.${facts.analysis}`)} tone={analysisTone} testid="hero-analysis" />
      </div>
      <h4 className="hero-start">{t("mg.hero.start")}</h4>
      <div className="hero-ctas">
        {ctas.map(([icon, title, desc, tid]) => (
          <button key={tid} className="hero-cta" onClick={() => go(icon)} data-testid={tid}><span className="hero-cta-i" aria-hidden="true"><Icon name={icon} size={20} /></span>
            <span><b>{t(title)}</b><span className="hero-cta-d">{t(desc)}</span></span></button>))}
      </div>
    </section>
  );
}

const CLASS_PROV: Record<EvidenceClass, Provenance> = { real: "real", synthetic: "synthetic", external: "unverified", unverified: "unverified" };
/** Bottom-of-page legend: what each evidence label means. Static text, no data. */
export function EvidenceLegend() {
  return (
    <section className="card legend-card" data-testid="ov-legend" aria-labelledby="legend-title">
      <h3 id="legend-title">{t("mg.hero.legend.title")}</h3>
      <dl className="legend-list">
        {EVIDENCE_CLASSES.map((c) => (
          <div key={c} className="legend-item" data-class={c}><dt>{c === "external" ? <span className="tag rep">{t("mg.hero.legend.external")}</span> : <><ProvTag kind={CLASS_PROV[c]} /> <span className="muted">{t(`mg.hero.legend.${c}`)}</span></>}</dt><dd>{t(`mg.hero.legend.${c}_d`)}</dd></div>))}
      </dl>
      <p className="muted">{t("mg.hero.legend.note")}</p>
    </section>
  );
}
