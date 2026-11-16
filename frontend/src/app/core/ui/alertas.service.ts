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
}
