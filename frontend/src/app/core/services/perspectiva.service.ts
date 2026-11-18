import { Injectable, computed, signal } from '@angular/core';

/**
 * Perspectiva de la página, elegida con el selector del encabezado:
 * - «cliente»: todo, como siempre (controles, clientes, Faltantes a Xubio).
 * - «usuario»: solo mira Ejecuciones y descarga los archivos; no puede iniciar,
 *   cancelar, borrar ni cambiar nada.
 *
 * Es una preferencia de vista (queda en este navegador), no un permiso: el
 * backend no la conoce.
 */
export type Perspectiva = 'usuario' | 'cliente';

const CLAVE = 'cf-perspectiva';

function leer(): Perspectiva {
  try {
    return localStorage.getItem(CLAVE) === 'usuario' ? 'usuario' : 'cliente';
  } catch {
    return 'cliente';
  }
}

@Injectable({ providedIn: 'root' })
export class PerspectivaService {
  readonly actual = signal<Perspectiva>(leer());
  readonly esCliente = computed(() => this.actual() === 'cliente');
  readonly soloVer = computed(() => this.actual() === 'usuario');

  elegir(p: Perspectiva): void {
    this.actual.set(p);
    try {
      localStorage.setItem(CLAVE, p);
    } catch {
      /* sin almacenamiento: dura hasta recargar */
    }
  }
}
