import { Component, DestroyRef, ElementRef, computed, effect, inject, input, signal, untracked, viewChild } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { duracion, tamanoLegible } from '../../core/fechas';
import { ArchivoGenerado, ClienteEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { Alerta } from '../../core/ui/alerta';
import { ConfirmModal } from '../../core/ui/confirm-modal';
import { Encabezado } from '../../core/ui/encabezado';
import { ESTILO_PASO, ETIQUETA_ARCHIVO, INSIGNIA_CLIENTE, INSIGNIA_EJECUCION } from './ejecucion.reglas';

/**
 * El progreso de un control: la pausa entre clientes, cada cliente con sus pasos
 * y sus archivos, y el registro de lo que van diciendo los bots.
 *
 * Sin id en la dirección muestra la ejecución en curso (o la última) y el historial.
 */
@Component({
  selector: 'app-ejecucion',
  imports: [RouterLink, DatePipe, Alerta, ConfirmModal, Encabezado],
  templateUrl: './ejecucion.html',
})
export class EjecucionPantalla {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly store = inject(EjecucionStore);

  /** De la ruta /ejecuciones/:id. */
  readonly id = input<string>();

  protected readonly insigniaEjecucion = INSIGNIA_EJECUCION;
  protected readonly insigniaCliente = INSIGNIA_CLIENTE;
  protected readonly estiloPaso = ESTILO_PASO;
  protected readonly etiquetaArchivo = ETIQUETA_ARCHIVO;
  protected readonly tamanoLegible = tamanoLegible;

  protected readonly confirmarCancelar = signal(false);
  protected readonly soloProblemas = signal(false);
  protected readonly descargando = signal<string | null>(null);
  protected readonly errorDescarga = signal<string | null>(null);

  /** Reloj de la pantalla: la cuenta regresiva de la pausa y las duraciones en vivo. */
  protected readonly ahora = signal(Date.now());

  protected readonly ej = this.store.actual;
  protected readonly activa = computed(() => estaActiva(this.ej()?.estado));

  protected readonly segundosPausa = computed(() => {
    const p = this.ej()?.pausa;
    return p ? Math.max(0, Math.ceil(p.vence - this.ahora() / 1000)) : 0;
  });
  protected readonly siguienteCliente = computed(() => {
    const e = this.ej();
    if (!e?.pausa) return null;
    const i = e.detalle_clientes.findIndex((c) => c.nombre === e.pausa!.cliente);
    return e.detalle_clientes[i + 1]?.nombre ?? null;
  });

  /** Los que llegaron al final (los cancelados no cuentan). */
  protected readonly procesados = computed(() => this.ej()?.detalle_clientes.filter((c) => c.estado === 'ok' || c.estado === 'con_errores').length ?? 0);
  protected readonly progreso = computed(() => {
    const e = this.ej();
    if (!e) return 0;
    const pasos = e.detalle_clientes.flatMap((c) => c.pasos);
    if (!pasos.length) return 0;
    const hechos = pasos.filter((p) => p.estado !== 'pendiente' && p.estado !== 'en_curso').length;
    const enCurso = pasos.some((p) => p.estado === 'en_curso') ? 0.5 : 0;
    return Math.round(((hechos + enCurso) / pasos.length) * 100);
  });

  protected readonly archivosTotales = computed(() => this.ej()?.detalle_clientes.reduce((n, c) => n + c.archivos.length, 0) ?? 0);

  protected readonly lineas = computed(() => {
    const log = this.store.log();
    return this.soloProblemas() ? log.filter((l) => l.tipo === 'error' || l.tipo === 'aviso') : log;
  });

  private readonly consola = viewChild<ElementRef<HTMLElement>>('consola');
  /** El registro baja solo mientras se está mirando el final. */
  private pegadoAbajo = true;

  constructor() {
    const reloj = setInterval(() => this.ahora.set(Date.now()), 1000);
    inject(DestroyRef).onDestroy(() => clearInterval(reloj));

    void this.store.cargarHistorial();

    effect(() => {
      const id = this.id();
      untracked(() => void this.abrir(id));
    });

    effect(() => {
      this.lineas();
      const el = this.consola()?.nativeElement;
      if (el && this.pegadoAbajo) queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });
  }

  private async abrir(id: string | undefined): Promise<void> {
    if (id) {
      this.store.seguir(id);
      return;
    }
    // Sin id: la que está corriendo o, si no, la última.
    try {
      const activa = await this.api.activa();
      const elegida = activa?.id ?? (await this.api.ejecuciones())[0]?.id;
      if (elegida) void this.router.navigate(['/ejecuciones', elegida], { replaceUrl: true });
      else this.store.actual.set(null);
    } catch (e) {
      this.store.error.set(mensajeDeError(e));
    }
  }

  protected alDesplazar(evento: Event): void {
    const el = evento.target as HTMLElement;
    this.pegadoAbajo = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }

  protected duracionPaso(inicio: string | null, fin: string | null): string {
    return duracion(inicio, fin, this.ahora());
  }

  protected duracionTotal(): string {
    const e = this.ej();
    return e ? duracion(e.creada, e.terminada, this.ahora()) : '';
  }

  protected hora(iso: string): string {
    return iso.slice(11, 19);
  }

  protected async descargar(indice: number, cliente: ClienteEjecucion, archivo: ArchivoGenerado): Promise<void> {
    const e = this.ej();
    if (!e) return;
    const clave = `${indice}/${archivo.nombre}`;
    this.descargando.set(clave);
    this.errorDescarga.set(null);
    try {
      const blob = await this.api.archivo(e.id, indice, archivo.nombre);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = archivo.nombre;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      this.errorDescarga.set(mensajeDeError(err, `No se pudo bajar ${archivo.nombre} de ${cliente.nombre}.`));
    } finally {
      this.descargando.set(null);
    }
  }

  protected async copiar(texto: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(texto);
    } catch {
      // Sin permiso de portapapeles: no pasa nada.
    }
  }
}
