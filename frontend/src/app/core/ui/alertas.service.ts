import { Injectable } from '@angular/core';
import Swal, { SweetAlertOptions } from 'sweetalert2';

/**
 * Alertas estandarizadas con SweetAlert2 y el aspecto de la app (Tailwind:
 * `slate` para texto y bordes, `blue` para la acción principal).
 *
 * Los estilos propios de SweetAlert2 se apagan (`buttonsStyling: false`) y los
 * botones toman las clases de `customClass`. Tailwind v4 detecta las clases de
 * este archivo, así que no hace falta registrarlas aparte.
 */

const BOTON = 'rounded-lg px-4 py-2 text-sm font-bold focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2';

const CLASES: SweetAlertOptions['customClass'] = {
  popup: 'rounded-2xl border border-slate-200 bg-white shadow-xl',
  title: 'text-lg font-extrabold text-slate-800',
  htmlContainer: 'text-sm leading-relaxed text-slate-600',
  actions: 'gap-2',
  confirmButton: `${BOTON} bg-blue-600 text-white hover:bg-blue-700 focus-visible:ring-blue-500`,
  cancelButton: `${BOTON} bg-slate-200 text-slate-700 hover:bg-slate-300 focus-visible:ring-slate-400`,
};

/** El texto llega de la API o del bot: se escapa antes de ponerlo en el HTML. */
function esc(texto: unknown): string {
  return String(texto ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

@Injectable({ providedIn: 'root' })
export class AlertasService {
  /** Alerta verde con botón azul. */
  async mostrarExito(titulo: string, mensaje = ''): Promise<void> {
    await Swal.fire({
      icon: 'success',
      iconColor: '#15803d',
      title: titulo,
      text: mensaje || undefined,
      confirmButtonText: 'Aceptar',
      buttonsStyling: false,
      customClass: CLASES,
    });
  }

  /**
   * Alerta roja pensada para el contador: el título es el mensaje claro y en
   * grande; el error técnico (Python, Playwright, HTTP) queda plegado y en letra
   * chica, para copiarlo si hay que reportarlo.
   */
  async mostrarErrorHumano(titulo: string, detalleTecnico = '', mensaje = ''): Promise<void> {
    const explicacion = mensaje ? `<p class="m-0 text-base text-slate-700">${esc(mensaje)}</p>` : '';
    const tecnico = detalleTecnico.trim()
      ? `<details class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-2 text-left">
           <summary class="cursor-pointer text-xs font-bold text-slate-500">Detalle técnico</summary>
           <pre class="m-0 mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-600">${esc(detalleTecnico.trim())}</pre>
         </details>`
      : '';
    await Swal.fire({
      icon: 'error',
      iconColor: '#c53030',
      title: titulo,
      html: explicacion + tecnico || undefined,
      confirmButtonText: 'Entendido',
      buttonsStyling: false,
      customClass: CLASES,
    });
  }

  /** Confirmación de algo que no se puede deshacer: botón rojo y «Cancelar» gris. */
  async confirmarAccionPeligrosa(titulo: string, texto: string, textoConfirmar = 'Sí, detener'): Promise<boolean> {
    const r = await Swal.fire({
      icon: 'warning',
      iconColor: '#c53030',
      title: titulo,
      text: texto,
      showCancelButton: true,
      confirmButtonText: textoConfirmar,
      cancelButtonText: 'Cancelar',
      reverseButtons: true,
      focusCancel: true,
      buttonsStyling: false,
      customClass: {
        ...CLASES,
        confirmButton: `${BOTON} bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-500`,
      },
    });
    return r.isConfirmed;
  }

  /**
   * Aviso amarillo «!»: algo que el usuario tiene que corregir antes de seguir
   * (período que no empezó, faltan datos...). `items` se muestra como lista.
   */
  async mostrarAviso(titulo: string, mensaje = '', items: string[] = []): Promise<void> {
    const texto = mensaje ? `<p class="m-0">${esc(mensaje)}</p>` : '';
    const lista = items.length
      ? `<ul class="m-0 mt-3 list-disc space-y-1 pl-5 text-left">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`
      : '';
    await Swal.fire({
      icon: 'warning',
      iconColor: '#b7791f',
      title: titulo,
      html: texto + lista || undefined,
      confirmButtonText: 'Entendido',
      buttonsStyling: false,
      customClass: CLASES,
    });
  }

  /**
   * Pregunta con dos salidas además de «Volver»: la recomendada (botón azul) y
   * otra (botón gris). Devuelve 'recomendada', 'otra' o null si se volvió.
   */
  async elegir(
    titulo: string,
    mensaje: string,
    recomendada: string,
    otra: string,
    items: string[] = [],
  ): Promise<'recomendada' | 'otra' | null> {
    const lista = items.length
      ? `<ul class="m-0 mt-3 list-disc space-y-1 pl-5 text-left">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`
      : '';
    const r = await Swal.fire({
      icon: 'question',
      iconColor: '#b7791f',
      title: titulo,
      html: `<p class="m-0">${esc(mensaje)}</p>${lista}`,
      showDenyButton: true,
      showCancelButton: true,
      confirmButtonText: recomendada,
      denyButtonText: otra,
      cancelButtonText: 'Volver',
      buttonsStyling: false,
      customClass: { ...CLASES, denyButton: CLASES?.cancelButton },
    });
    return r.isConfirmed ? 'recomendada' : r.isDenied ? 'otra' : null;
  }

  /**
   * Revisar antes de seguir: muestra la tabla de lo que se va a hacer y solo deja
   * confirmar si se tilda «Las revisé». Las `alertas` van arriba, en rojo.
   */
  async revisarAntesDeSeguir(opciones: {
    titulo: string;
    mensaje: string;
    alertas?: string[];
    columnas: string[];
    /** Una fila por renglón; las columnas numéricas van alineadas a la derecha. */
    filas: (string | number)[][];
    tilde: string;
    textoConfirmar: string;
  }): Promise<boolean> {
    const alertas = (opciones.alertas ?? []).length
      ? `<div class="mb-3 rounded-lg border border-red-200 bg-red-50 p-2.5 text-left text-red-800">${opciones.alertas!
          .map((a) => `<p class="m-0">⚠ ${esc(a)}</p>`)
          .join('')}</div>`
      : '';
    const numero = (v: string | number) => typeof v === 'number';
    const plata = (v: string | number) =>
      typeof v === 'number' ? v.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : esc(v);
    const cab = opciones.columnas.map((c) => `<th class="border-b border-slate-200 px-2 py-1 text-left font-bold">${esc(c)}</th>`).join('');
    const cuerpo = opciones.filas
      .map((f) => `<tr>${f.map((v) => `<td class="border-b border-slate-100 px-2 py-1 ${numero(v) ? 'text-right tabular-nums' : ''}">${plata(v)}</td>`).join('')}</tr>`)
      .join('');
    const tabla = `<div class="mt-3 max-h-72 overflow-auto rounded-lg border border-slate-200 text-left text-xs"><table class="w-full border-collapse"><thead class="sticky top-0 bg-slate-50">${cab}</thead><tbody>${cuerpo}</tbody></table></div>`;
    const r = await Swal.fire({
      icon: 'warning',
      iconColor: '#b7791f',
      title: opciones.titulo,
      width: 'min(52rem, calc(100vw - 2rem))',
      html: `${alertas}<p class="m-0">${esc(opciones.mensaje)}</p>${tabla}`,
      input: 'checkbox',
      inputValue: 0,
      inputPlaceholder: opciones.tilde,
      inputValidator: (v) => (v ? null : 'Tildá que las revisaste para seguir.'),
      showCancelButton: true,
      confirmButtonText: opciones.textoConfirmar,
      cancelButtonText: 'Volver',
      reverseButtons: true,
      buttonsStyling: false,
      customClass: CLASES,
    });
    return r.isConfirmed;
  }

  /** Pregunta antes de seguir con algo que no es peligroso pero conviene revisar. */
  async confirmar(titulo: string, mensaje: string, textoConfirmar = 'Seguir igual', items: string[] = []): Promise<boolean> {
    const lista = items.length
      ? `<ul class="m-0 mt-3 list-disc space-y-1 pl-5 text-left">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`
      : '';
    const r = await Swal.fire({
      icon: 'question',
      iconColor: '#2563eb',
      title: titulo,
      html: `<p class="m-0">${esc(mensaje)}</p>${lista}`,
      showCancelButton: true,
      confirmButtonText: textoConfirmar,
      cancelButtonText: 'Volver',
      reverseButtons: true,
      buttonsStyling: false,
      customClass: CLASES,
    });
    return r.isConfirmed;
  }
}
