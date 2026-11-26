/**
 * Lo del Conversor guardado en el navegador (IndexedDB), para que recargar la página no
 * borre lo leído, lo corregido ni las fotos. Si el navegador no deja guardar, sigue
 * funcionando igual, solo que sin recordar.
 */

const BASE = 'cf-conversor';
const TABLA = 'datos';

function abrir(): Promise<IDBDatabase> {
  return new Promise((ok, mal) => {
    const pedido = indexedDB.open(BASE, 1);
    pedido.onupgradeneeded = () => pedido.result.createObjectStore(TABLA);
    pedido.onsuccess = () => ok(pedido.result);
    pedido.onerror = () => mal(pedido.error);
  });
}

async function operar<T>(modo: IDBTransactionMode, f: (t: IDBObjectStore) => IDBRequest): Promise<T | undefined> {
  try {
    const db = await abrir();
    return await new Promise<T>((ok, mal) => {
      const pedido = f(db.transaction(TABLA, modo).objectStore(TABLA));
      pedido.onsuccess = () => ok(pedido.result as T);
      pedido.onerror = () => mal(pedido.error);
    }).finally(() => db.close());
  } catch {
    return undefined;
  }
}

export const guardar = (clave: string, valor: unknown) => operar<void>('readwrite', (t) => t.put(valor, clave));
export const recuperar = <T>(clave: string) => operar<T>('readonly', (t) => t.get(clave));
export const borrar = (clave: string) => operar<void>('readwrite', (t) => t.delete(clave));
export const borrarTodo = () => operar<void>('readwrite', (t) => t.clear());

/** Guardar seguido (cada cambio) sin escribir mil veces: espera a que se quede quieto. */
export function conDemora(f: () => void, ms = 400): () => void {
  let t: ReturnType<typeof setTimeout> | null = null;
  return () => {
    if (t) clearTimeout(t);
    t = setTimeout(f, ms);
  };
}
