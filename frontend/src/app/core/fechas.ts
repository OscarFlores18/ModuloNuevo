/** Fechas como las piden los bots: DD/MM/AAAA. */

const dos = (n: number) => String(n).padStart(2, '0');

/**
 * '2026-10' -> {desde: '01/10/2026', hasta: '31/10/2026'}.
 * Si el mes está en curso, «hasta» es hoy: ARCA no acepta fechas futuras.
 */
export function rangoDelMes(mes: string, hoy = new Date()): { desde: string; hasta: string } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(mes);
  if (!m) return null;
  const anio = Number(m[1]);
  const numero = Number(m[2]);
  return { desde: `01/${m[2]}/${anio}`, hasta: hastaSinPasarHoy(new Date(anio, numero, 0), hoy) };
}

/** '2026-10-05' (input date) -> '05/10/2026' */
export function isoADmy(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : '';
}

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];

/** '2026-10' -> 'octubre 2026' */
export function nombreDelMes(mes: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(mes);
  return m ? `${MESES[Number(m[2]) - 1]} ${m[1]}` : '';
}

/** Último día del período, sin pasar de hoy: un año en curso se controla hasta hoy. */
function hastaSinPasarHoy(fin: Date, hoy: Date): string {
  const f = fin > hoy ? hoy : fin;
  return `${dos(f.getDate())}/${dos(f.getMonth() + 1)}/${f.getFullYear()}`;
}

/** '2025' -> {desde: '01/01/2025', hasta: '31/12/2025'} */
export function rangoDelAnio(anio: string, hoy = new Date()): { desde: string; hasta: string } | null {
  if (!/^\d{4}$/.test(anio)) return null;
  return { desde: `01/01/${anio}`, hasta: hastaSinPasarHoy(new Date(Number(anio), 11, 31), hoy) };
}

/** Este año y los cinco anteriores, el más reciente primero. */
export function aniosRecientes(hoy = new Date()): string[] {
  return Array.from({ length: 6 }, (_, i) => String(hoy.getFullYear() - i));
}

/** '01/10/2026' -> '102026': la subcarpeta del período (<mm><aaaa>). */
export function carpetaDelPeriodo(desde: string): string {
  const m = /^\d{2}\/(\d{2})\/(\d{4})$/.exec(desde);
  return m ? `${m[1]}${m[2]}` : 'mmaaaa';
}

/** Duración legible entre dos ISO: «1 min 05 s». */
export function duracion(inicio: string | null, fin: string | null, ahora = Date.now()): string {
  if (!inicio) return '';
  const ms = (fin ? Date.parse(fin) : ahora) - Date.parse(inicio);
  if (!(ms >= 0)) return '';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${dos(s % 60)} s`;
  return `${Math.floor(m / 60)} h ${dos(m % 60)} min`;
}

export function tamanoLegible(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} MB`;
}

/**
 * El período como lo dice el estudio: «octubre 2026», «noviembre 2026 (al 17/11)»
 * si el mes no terminó, «año 2025», o el rango tal cual si no es un mes ni un año.
 * Recibe DD/MM/AAAA.
 */
export function periodoLegible(desde: string, hasta: string): string {
  const d = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(desde);
  const h = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(hasta);
  if (!d || !h) return `${desde} al ${hasta}`;
  const [, dd, dm, da] = d;
  const [, hd, hm, ha] = h;
  if (dd === '01' && dm === hm && da === ha) {
    const ultimo = new Date(Number(da), Number(dm), 0).getDate();
    const nombre = nombreDelMes(`${da}-${dm}`);
    return Number(hd) === ultimo ? nombre : `${nombre} (al ${hd}/${hm})`;
  }
  if (dd === '01' && dm === '01' && da === ha) {
    return hd === '31' && hm === '12' ? `año ${da}` : `año ${da} (al ${hd}/${hm})`;
  }
  return `${desde} al ${hasta}`;
}

/** Lo mismo desde el texto del resumen («01/10/2026 al 31/10/2026»). */
export function periodoLegibleDe(periodo: string): string {
  const [desde, hasta] = periodo.split(' al ');
  return hasta ? periodoLegible(desde, hasta) : periodo;
}
