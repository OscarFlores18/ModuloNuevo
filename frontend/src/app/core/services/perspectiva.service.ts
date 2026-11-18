import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { escribirTexto, leerTexto } from '../almacen';
import { SesionService } from './sesion.service';

/**
 * Con qué perspectiva se mira la app, sin usuario ni contraseña:
 *
 * - `empleado`: no ve nada de Nuevo control (ni la pestaña ni los botones que
 *   llevan ahí). El resto lo ve con las acciones bloqueadas; sí puede descargar todo.
 * - `total`: puede usar todo.
 *
 * La perspectiva solo puede **bajar** permisos: quien entra desde ContadorWeb
 * como empleado queda siempre en `empleado` y no ve la pantalla de elección.
 * Un supervisor (o el modo local) elige; la elección vive en sessionStorage,
 * así que dura lo que dura la pestaña.
 */
export type Perspectiva = 'empleado' | 'total';

export const NOMBRE_PERSPECTIVA: Record<Perspectiva, string> = {
  empleado: 'Empleado',
  total: 'Acceso Total',
};

const CLAVE = 'control-fct.perspectiva';

function guardada(): Perspectiva | null {
  const v = leerTexto('session', CLAVE);
  return v === 'empleado' || v === 'total' ? v : null;
}

@Injectable({ providedIn: 'root' })
export class PerspectivaService {
  private readonly sesion = inject(SesionService);

  /** Lo que se eligió en esta pestaña (null = todavía no se eligió). */
  private readonly elegida = signal<Perspectiva | null>(guardada());

  /** Los roles reales permiten «Acceso Total» (supervisor de ContadorWeb o modo local). */
  readonly puedeAccesoTotal = computed(() => this.sesion.esSupervisor());

  /** La perspectiva con la que se pinta todo. */
  readonly perspectiva = computed<Perspectiva | null>(() => (this.puedeAccesoTotal() ? this.elegida() : 'empleado'));

  /** Hay que mostrar la pantalla «¿Cómo deseas ingresar?». */
  readonly necesitaElegir = computed(() => this.perspectiva() === null);

  /** Correr controles, cargar en Xubio, editar clientes… (descargar no lo necesita). */
  readonly canEdit = computed(() => this.perspectiva() === 'total');

  /** Para el `title` de un botón bloqueado: explica por qué no responde. */
  readonly motivoBloqueo = computed(() => (this.canEdit() ? null : 'Bloqueado en la vista Empleado'));

  /** Editar clientes sigue pidiendo el rol de supervisor, y además la vista de Acceso Total. */
  readonly editaClientes = computed(() => this.sesion.esSupervisor() && this.canEdit());

  readonly nombre = computed(() => {
    const p = this.perspectiva();
    return p ? NOMBRE_PERSPECTIVA[p] : '';
  });

  elegir(p: Perspectiva): void {
    if (p === 'total' && !this.puedeAccesoTotal()) return;
    this.elegida.set(p);
    escribirTexto('session', CLAVE, p);
  }

  /** «Cambiar perfil»: borra la elección y vuelve a la pantalla inicial. */
  cambiar(): void {
    this.elegida.set(null);
    escribirTexto('session', CLAVE, null);
  }
}

/** Nuevo control no existe para el empleado: va a Ejecuciones. */
export const soloAccesoTotal: CanActivateFn = () => inject(PerspectivaService).canEdit() || inject(Router).parseUrl('/ejecuciones');

/** La pantalla de inicio según la perspectiva. */
export function inicioSegunPerspectiva(): string {
  return inject(PerspectivaService).canEdit() ? 'control' : 'ejecuciones';
}
