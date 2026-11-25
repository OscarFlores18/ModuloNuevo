import { ClienteEntrada } from '../../core/models';

/**
 * Clientes pegados como JSON, con el formato del `config.json`:
 *
 *   {"CLIENTES": {"ACME S.A.": {"ARCA": {"cuit_login": "…", "clave": "…", "cuit_representado": "…"}}}}
 *
 * También sirve sin «CLIENTES», un cliente suelto ({"nombre": …, "arca": {…}}) o una
 * lista. Perdona lo típico de copiar a mano: «{CLIENTES{» sin comillas ni dos puntos
 * y llaves de cierre de menos.
 */
export function leerClientesJson(texto: string): ClienteEntrada[] {
  const datos = parsear(texto);
  const raiz = buscar(datos, 'clientes') ?? datos;
  const clientes: ClienteEntrada[] = [];
  if (Array.isArray(raiz)) {
    for (const item of raiz) clientes.push(cliente(texto_(buscar(item, 'nombre')), item));
  } else if (esObjeto(raiz) && (buscar(raiz, 'nombre') !== undefined || buscar(raiz, 'arca') !== undefined)) {
    clientes.push(cliente(texto_(buscar(raiz, 'nombre')), raiz));
  } else if (esObjeto(raiz)) {
    for (const [nombre, item] of Object.entries(raiz)) clientes.push(cliente(nombre, item));
  }
  if (!clientes.length) throw new Error('No se encontró ningún cliente en el JSON.');
  return clientes;
}

function parsear(texto: string): unknown {
  const limpio = texto.trim();
  if (!limpio) throw new Error('Pegá el JSON del cliente.');
  try {
    return JSON.parse(limpio);
  } catch {
    // Se intenta arreglar: «{CLIENTES{» → «{"CLIENTES":{» y se cierran las llaves que falten.
    let arreglado = limpio.replace(/^\{\s*"?clientes"?\s*:?\s*\{/i, '{"CLIENTES":{');
    arreglado += cierresQueFaltan(arreglado);
    try {
      return JSON.parse(arreglado);
    } catch {
      throw new Error('El texto no es un JSON válido. Revisá comillas, comas y llaves.');
    }
  }
}

/** Las «}» / «]» que faltan al final, en orden (fuera de los textos entre comillas). */
function cierresQueFaltan(s: string): string {
  const pila: string[] = [];
  let enTexto = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (enTexto) {
      if (c === '\\') i++;
      else if (c === '"') enTexto = false;
    } else if (c === '"') enTexto = true;
    else if (c === '{') pila.push('}');
    else if (c === '[') pila.push(']');
    else if (c === '}' || c === ']') pila.pop();
  }
  return pila.reverse().join('');
}

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** La clave sin importar mayúsculas («ARCA», «arca»). */
function buscar(v: unknown, clave: string): unknown {
  if (!esObjeto(v)) return undefined;
  const k = Object.keys(v).find((x) => x.toLowerCase() === clave);
  return k === undefined ? undefined : v[k];
}

function texto_(v: unknown): string {
  return v === undefined || v === null ? '' : String(v).trim();
}

function cliente(nombre: string, item: unknown): ClienteEntrada {
  if (!nombre) throw new Error('Hay un cliente sin nombre en el JSON.');
  if (!esObjeto(item)) throw new Error(`«${nombre}»: faltan sus datos.`);
  const arca = esObjeto(buscar(item, 'arca')) ? buscar(item, 'arca') : item;
  const digitos = (clave: string) => texto_(buscar(arca, clave)).replace(/\D/g, '');
  const email = texto_(buscar(item, 'email') ?? buscar(item, 'mail'));
  return {
    nombre,
    arca: { cuit_login: digitos('cuit_login'), clave: texto_(buscar(arca, 'clave')), cuit_representado: digitos('cuit_representado') },
    ...(email ? { email } : {}),
  };
}
