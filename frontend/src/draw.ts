import { t } from "./i18n";
import { scaleBox, type RiskState, type Zone } from "./lib";

export const STATE_COLOR: Record<RiskState, string> = { GREEN: "#2e9e5b", YELLOW: "#e0a800", RED: "#d64545", UNKNOWN: "#8a8f98" };
type Rect = { x: number; y: number; w: number; h: number };

/** Zone polygons in frame coordinates, scaled into the displayed picture rectangle. UNKNOWN is dotted grey (never green). */
export function drawZones(ctx: CanvasRenderingContext2D, zones: Zone[], r: Rect, fw: number, fh: number, stateOf: (zoneId: string) => RiskState | null) {
  for (const z of zones) {
    if (z.polygon.length < 3) continue;
    const st = z.kind === "restricted" ? stateOf(z.zone_id) ?? "UNKNOWN" : null;
    const col = st ? STATE_COLOR[st] : "#5b8def";
    ctx.save();
    ctx.beginPath();
    z.polygon.forEach(([x, y], i) => {
      const px = r.x + (x / fw) * r.w, py = r.y + (y / fh) * r.h;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.closePath();
    ctx.globalAlpha = 0.16;
    ctx.fillStyle = col;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2.5;
    ctx.setLineDash(st === "UNKNOWN" ? [3, 4] : z.kind === "monitored" ? [8, 5] : []);
    ctx.strokeStyle = col;
    ctx.stroke();
    ctx.setLineDash([]);
    const [x0, y0] = z.polygon[0];
    const label = `${z.name}${st ? " · " + t(`mg.risk.badge.${st}`) : ""}`;
    ctx.font = "bold 13px system-ui, sans-serif";
    const tw = ctx.measureText(label).width + 10, lx = r.x + (x0 / fw) * r.w, ly = r.y + (y0 / fh) * r.h;
    ctx.fillStyle = col;
    ctx.fillRect(lx, ly, tw, 20);
    ctx.fillStyle = st === "YELLOW" ? "#000" : "#fff";
    ctx.fillText(label, lx + 5, ly + 14);
    ctx.restore();
  }
}

export function drawDraft(ctx: CanvasRenderingContext2D, pts: number[][], r: Rect, fw: number, fh: number) {
  if (!pts.length) return;
  ctx.save();
  ctx.strokeStyle = "#ffffff";
  ctx.fillStyle = "#5b8def";
  ctx.lineWidth = 2;
  ctx.setLineDash([5, 4]);
  ctx.beginPath();
  pts.forEach(([x, y], i) => {
    const px = r.x + (x / fw) * r.w, py = r.y + (y / fh) * r.h;
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();
  for (const [x, y] of pts) ctx.fillRect(r.x + (x / fw) * r.w - 3, r.y + (y / fh) * r.h - 3, 6, 6);
  ctx.restore();
}

export function drawLiveBoxes(ctx: CanvasRenderingContext2D, objs: { label: string; bbox: { x: number; y: number; w: number; h: number }; permille: number | null; track: string | null }[],
  r: Rect, fw: number, fh: number, color: (label: string) => string) {
  ctx.save();
  ctx.lineWidth = 2;
  ctx.font = "13px system-ui, sans-serif";
  for (const o of objs) {
    const b = scaleBox(o.bbox, fw, fh, r.w, r.h);
    ctx.strokeStyle = color(o.label);
    ctx.strokeRect(r.x + b.x, r.y + b.y, b.w, b.h);
    const label = `${o.label}${o.track ? " " + o.track : ` ${t("mg.xd.draw.no_id")}`} ${((o.permille ?? 0) / 10).toFixed(0)}%`;
    const tw = ctx.measureText(label).width + 8;
    ctx.fillStyle = color(o.label);
    ctx.fillRect(r.x + b.x, Math.max(0, r.y + b.y - 18), tw, 18);
    ctx.fillStyle = "#000";
    ctx.fillText(label, r.x + b.x + 4, Math.max(13, r.y + b.y - 5));
  }
  ctx.restore();
}
