/** Fechas como las piden los bots: DD/MM/AAAA. */

const dos = (n: number) => String(n).padStart(2, '0');

/** '2026-10' -> {desde: '01/10/2026', hasta: '31/10/2026'} */
export function rangoDelMes(mes: string): { desde: string; hasta: string } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(mes);
  if (!m) return null;
  const anio = Number(m[1]);
  const numero = Number(m[2]);
  const ultimo = new Date(anio, numero, 0).getDate();
  return { desde: `01/${m[2]}/${anio}`, hasta: `${ultimo}/${m[2]}/${anio}` };
}

/** '2026-10-05' (input date) -> '05/10/2026' */
export function isoADmy(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : '';
}

/** El mes anterior al de hoy, como lo usa <input type="month">: lo habitual es controlar el mes cerrado. */
export function mesAnterior(hoy = new Date()): string {
  const d = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}`;
}

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];

/** '2026-10' -> 'octubre 2026' */
export function nombreDelMes(mes: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(mes);
  return m ? `${MESES[Number(m[2]) - 1]} ${m[1]}` : '';
}

/** Último día del período, sin pasar de hoy: un trimestre o año en curso se controla hasta hoy. */
function hastaSinPasarHoy(fin: Date, hoy: Date): string {
  const f = fin > hoy ? hoy : fin;
  return `${dos(f.getDate())}/${dos(f.getMonth() + 1)}/${f.getFullYear()}`;
}

const ORDINAL = ['1er', '2do', '3er', '4to'];

/** '2026-3' -> {desde: '01/07/2026', hasta: '30/09/2026'} (el 3er trimestre). */
export function rangoDelTrimestre(valor: string, hoy = new Date()): { desde: string; hasta: string } | null {
  const m = /^(\d{4})-([1-4])$/.exec(valor);
  if (!m) return null;
  const anio = Number(m[1]);
  const t = Number(m[2]);
  return { desde: `01/${dos(t * 3 - 2)}/${anio}`, hasta: hastaSinPasarHoy(new Date(anio, t * 3, 0), hoy) };
}

/** '2026-3' -> '3er trimestre 2026' */
export function nombreDelTrimestre(valor: string): string {
  const m = /^(\d{4})-([1-4])$/.exec(valor);
  return m ? `${ORDINAL[Number(m[2]) - 1]} trimestre ${m[1]}` : '';
}

/** Los trimestres ya empezados de los últimos tres años, el más reciente primero. */
export function trimestresRecientes(hoy = new Date()): { valor: string; texto: string }[] {
  const actual = Math.floor(hoy.getMonth() / 3) + 1;
  const lista: { valor: string; texto: string }[] = [];
  for (let anio = hoy.getFullYear(); anio >= hoy.getFullYear() - 2; anio--) {
    for (let t = anio === hoy.getFullYear() ? actual : 4; t >= 1; t--) {
      const meses = `${MESES[t * 3 - 3].slice(0, 3)}–${MESES[t * 3 - 1].slice(0, 3)}`;
      const enCurso = anio === hoy.getFullYear() && t === actual ? ' · en curso' : '';
      lista.push({ valor: `${anio}-${t}`, texto: `${ORDINAL[t - 1]} trimestre ${anio} (${meses})${enCurso}` });
    }
  }
  return lista;
}

/** El último trimestre cerrado: lo habitual es controlar un período terminado. */
export function trimestreAnterior(hoy = new Date()): string {
  const t = Math.floor(hoy.getMonth() / 3);
  return t === 0 ? `${hoy.getFullYear() - 1}-4` : `${hoy.getFullYear()}-${t}`;
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
