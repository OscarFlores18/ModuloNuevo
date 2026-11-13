/**
 * «Guardar como» del sistema para cada archivo: el usuario elige la carpeta (y
 * puede cambiar el nombre). Usa la File System Access API (Chrome y Edge); en
 * otros navegadores cae a la descarga común.
 *
 * El diálogo se abre ANTES de pedir el archivo al servidor: el navegador solo lo
 * permite unos segundos después de un clic. Si ese permiso venció (el usuario
 * tardó en el diálogo anterior), devuelve 'sin-permiso' y hay que pedir otro clic.
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

type DialogoGuardar = (opciones?: OpcionesGuardar) => Promise<ArchivoDestino>;

export type ResultadoGuardar = 'guardado' | 'cancelado' | 'sin-permiso';

/** El mismo id para todos: el diálogo vuelve a abrir en la última carpeta elegida. */
const ID_DIALOGO = 'control-fct';

const TIPOS: Record<string, { description: string; accept: Record<string, string[]> }> = {
  xlsx: { description: 'Libro de Excel', accept: { 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] } },
  xls: { description: 'Libro de Excel 97-2003', accept: { 'application/vnd.ms-excel': ['.xls'] } },
  csv: { description: 'Texto separado por comas', accept: { 'text/csv': ['.csv'] } },
  txt: { description: 'Texto', accept: { 'text/plain': ['.txt'] } },
};

function descargaComun(nombre: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function guardarComo(nombre: string, obtener: () => Promise<Blob>): Promise<ResultadoGuardar> {
  const dialogo = (window as unknown as { showSaveFilePicker?: DialogoGuardar }).showSaveFilePicker;
  if (!dialogo) {
    descargaComun(nombre, await obtener());
    return 'guardado';
  }

  let destino: ArchivoDestino;
  try {
    const tipo = TIPOS[nombre.split('.').pop()?.toLowerCase() ?? ''];
    destino = await dialogo({ suggestedName: nombre, id: ID_DIALOGO, startIn: 'downloads', types: tipo ? [tipo] : undefined });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') return 'cancelado';
    if (e instanceof DOMException && (e.name === 'SecurityError' || e.name === 'NotAllowedError')) return 'sin-permiso';
    throw e;
  }

  const blob = await obtener();
  const w = await destino.createWritable();
  await w.write(blob);
  await w.close();
  return 'guardado';
}
