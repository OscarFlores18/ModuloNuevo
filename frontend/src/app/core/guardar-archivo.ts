/**
 * Guardar archivos en la computadora de quien usa la página, eligiendo la carpeta
 * en el diálogo del sistema («Guardar como» / «Elegir carpeta»).
 *
 * Usa la File System Access API (Chrome y Edge). En otros navegadores cae a la
 * descarga común, que va a la carpeta de descargas (o pregunta, según el navegador).
 *
 * El diálogo se abre ANTES de pedir el archivo al servidor: el navegador solo lo
 * permite como respuesta directa al clic.
 */

interface OpcionesGuardar {
  suggestedName?: string;
  id?: string;
  startIn?: string;
  types?: { description: string; accept: Record<string, string[]> }[];
}

interface ArchivoDestino {
  createWritable(): Promise<{ write(datos: Blob): Promise<void>; close(): Promise<void> }>;
}

interface CarpetaDestino {
  getFileHandle(nombre: string, opciones: { create: boolean }): Promise<ArchivoDestino>;
}

interface VentanaConDialogos {
  showSaveFilePicker?: (opciones?: OpcionesGuardar) => Promise<ArchivoDestino>;
  showDirectoryPicker?: (opciones?: { id?: string; mode?: 'readwrite'; startIn?: string }) => Promise<CarpetaDestino>;
}

/** El mismo id para todos: el diálogo vuelve a abrir en la última carpeta elegida. */
const ID_DIALOGO = 'control-fct';

const TIPOS: Record<string, { description: string; accept: Record<string, string[]> }> = {
  xlsx: { description: 'Libro de Excel', accept: { 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] } },
  xls: { description: 'Libro de Excel 97-2003', accept: { 'application/vnd.ms-excel': ['.xls'] } },
  csv: { description: 'Texto separado por comas', accept: { 'text/csv': ['.csv'] } },
  txt: { description: 'Texto', accept: { 'text/plain': ['.txt'] } },
};

const ventana = (): VentanaConDialogos => window as unknown as VentanaConDialogos;

/** Cerrar el diálogo sin elegir no es un error. */
function esCancelacion(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError';
}

/** La descarga de siempre, para navegadores sin el diálogo. */
function descargaComun(nombre: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * «Guardar como»: el usuario elige carpeta (y puede cambiar el nombre).
 * Devuelve false si cerró el diálogo sin guardar.
 */
export async function guardarComo(nombre: string, obtener: () => Promise<Blob>): Promise<boolean> {
  const dialogo = ventana().showSaveFilePicker;
  if (!dialogo) {
    descargaComun(nombre, await obtener());
    return true;
  }

  let destino: ArchivoDestino;
  try {
    const tipo = TIPOS[nombre.split('.').pop()?.toLowerCase() ?? ''];
    destino = await dialogo({ suggestedName: nombre, id: ID_DIALOGO, startIn: 'downloads', types: tipo ? [tipo] : undefined });
  } catch (e) {
    if (esCancelacion(e)) return false;
    throw e;
  }

  const blob = await obtener();
  const w = await destino.createWritable();
  await w.write(blob);
  await w.close();
  return true;
}

/**
 * Varios archivos a una carpeta elegida en el diálogo. Si ya existe uno con el
 * mismo nombre, se reemplaza. Devuelve cuántos se guardaron, o null si se canceló.
 */
export async function guardarEnCarpeta(archivos: { nombre: string; obtener: () => Promise<Blob> }[]): Promise<number | null> {
  const dialogo = ventana().showDirectoryPicker;
  if (!dialogo) {
    for (const a of archivos) descargaComun(a.nombre, await a.obtener());
    return archivos.length;
  }

  let carpeta: CarpetaDestino;
  try {
    carpeta = await dialogo({ id: ID_DIALOGO, mode: 'readwrite', startIn: 'downloads' });
  } catch (e) {
    if (esCancelacion(e)) return null;
    throw e;
  }

  let guardados = 0;
  for (const a of archivos) {
    const blob = await a.obtener();
    const w = await (await carpeta.getFileHandle(a.nombre, { create: true })).createWritable();
    await w.write(blob);
    await w.close();
    guardados++;
  }
  return guardados;
}
