import { useEffect, useState } from "react";
import { Mp4View, type OpenRun } from "./Mp4View";
import { WebcamPanel } from "./WebcamPanel";
import { Sessions } from "./Sessions";
import { Feeds } from "./Feeds";
import { Shell, type Area } from "./app/Shell";
import { Configuracion, Desarrollador, Estadisticas, Eventos } from "./Views";
import { Resumen } from "./overview/Overview";
import { SimLab } from "./simulations/SimLab";
import { t } from "./i18n";
import { applyDocumentLanguage, useLocale } from "./locale";

/** Integrated shell: the ten product areas. The MP4 workspace stays mounted (hidden) so an analysis is never interrupted by navigation; the camera panel unmounts (releases the camera), feeds keep running on the server. */
export function App() {
  const locale = useLocale();                     // the whole tree re-renders on a language change; component state (e.g. a running analysis) is kept
  useEffect(() => applyDocumentLanguage(locale, t("mg.doc.title")), [locale]);
  const [area, setArea] = useState<Area>("resumen");
  const [open, setOpen] = useState<OpenRun | undefined>();
  const [focusFeed, setFocusFeed] = useState<string | null>(null);
  const [focusAlert, setFocusAlert] = useState<string | null>(null);
  const [returnTo, setReturnTo] = useState<string | null>(null);      // the incident the operator came from when jumping to a source (video or feed)
  useEffect(() => { if (area !== "video" && area !== "feeds") setReturnTo(null); }, [area]);
  const go = (a: string) => setArea(a as Area);
  const openRun = (id: string, base: string | null, tUs?: number) => { setOpen({ id, base, jumpRed: tUs === undefined, tUs, n: (open?.n ?? 0) + 1 }); setArea("video"); };
  const openFeed = (id: string) => { setFocusFeed(id); setArea("feeds"); };
  const openAlert = (id: string) => { setFocusAlert(id); setArea("eventos"); };
  return (
    <Shell area={area} setArea={setArea}>
      {returnTo && (area === "video" || area === "feeds") && <div className="banner info controls" data-testid="back-to-incident-bar" role="status">
        <span>{t("mg.inc.came_from")}</span><button className="primary" onClick={() => { openAlert(returnTo); setReturnTo(null); }} data-testid="back-to-incident">{t("mg.inc.back_incident")}</button>
        <button onClick={() => setReturnTo(null)} data-testid="back-to-incident-dismiss">{t("mg.inc.dismiss")}</button></div>}
      {area === "resumen" && <Resumen go={go} openFeed={openFeed} openAlert={openAlert} />}
      <div style={{ display: area === "video" ? undefined : "none" }}><Mp4View open={open} /></div>
      {area === "camara" && <WebcamPanel />}
      {area === "feeds" && <Feeds focusFeed={focusFeed} onFocusHandled={() => setFocusFeed(null)} />}
      {area === "sesiones" && <Sessions />}
      {area === "eventos" && <Eventos focusAlert={focusAlert} onFocusHandled={() => setFocusAlert(null)} openFeed={openFeed} openRun={openRun} go={go} remember={setReturnTo} />}
      {area === "simulaciones" && <SimLab openRun={openRun} go={go} openAlert={openAlert} />}
      {area === "estadisticas" && <Estadisticas />}
      {area === "desarrollador" && <Desarrollador />}
      {area === "configuracion" && <Configuracion />}
    </Shell>
  );
}
