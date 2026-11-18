/**
 * Lectura y escritura seguras en localStorage / sessionStorage. En modo privado,
 * con el almacenamiento bloqueado o lleno, no rompe: devuelve el valor por
 * defecto y lo guardado dura hasta recargar.
 */
export type Almacen = 'local' | 'session';

function almacen(tipo: Almacen): Storage | null {
  try {
    return (tipo === 'local' ? globalThis.localStorage : globalThis.sessionStorage) ?? null;
  } catch {
    return null;
  }
}

export function leerTexto(tipo: Almacen, clave: string): string | null {
  try {
    return almacen(tipo)?.getItem(clave) ?? null;
  } catch {
    return null;
  }
}

export function escribirTexto(tipo: Almacen, clave: string, valor: string | null): void {
  try {
    const a = almacen(tipo);
    if (!a) return;
    if (valor === null) a.removeItem(clave);
    else a.setItem(clave, valor);
  } catch {
    // Sin almacenamiento o sin lugar: queda solo en memoria.
  }
}
