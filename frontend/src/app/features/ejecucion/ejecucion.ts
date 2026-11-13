import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { duracion, tamanoLegible } from '../../core/fechas';
import { ArchivoGenerado, ClienteEjecucion, PasoEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { descargarArchivo } from '../../core/guardar-archivo';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { Alerta } from '../../core/ui/alerta';
import { ConfirmModal } from '../../core/ui/confirm-modal';
import { ToastService } from '../../core/ui/toast';
import { ETIQUETA_ARCHIVO, Etapa, INSIGNIA_CLIENTE, INSIGNIA_EJECUCION, Insignia, TEXTO_ETAPA, avanceDe, etapasDe } from './ejecucion.reglas';

/** Un paso que terminó mal o con avisos: lo único que se muestra del detalle si algo falló. */
interface Problema {
  nivel: 'error' | 'aviso';
  titulo: string;
  mensaje: string;
}

/** Lo que pinta cada acordeón: el cliente ya resumido en etapas, avance y problemas. */
interface ClienteVista {
  indice: number;
  nombre: string;
  insignia: Insignia;
  etapas: Etapa[];
  avance: number;
  conError: boolean;
  problemas: Problema[];
  carpeta: string;
  archivos: ArchivoGenerado[];
  origen: ClienteEjecucion;
}

function problemasDe(pasos: readonly PasoEjecucion[]): Problema[] {
  return pasos
    .filter((p) => p.estado === 'error' || (p.estado === 'aviso' && !!p.mensaje))
    .map((p) => ({
      nivel: p.estado === 'error' ? ('error' as const) : ('aviso' as const),
      titulo: `${p.etiqueta}: ${p.estado === 'error' ? 'error' : 'terminó con avisos'}.`,
      mensaje: p.mensaje || 'El bot no informó el motivo.',
    }));
}

/**
 * El monitoreo de un control: estado global, un acordeón por cliente con su
 * línea de tiempo (ARCA -> Xubio -> Auditoría) y, plegado al final, el historial.
 * El registro técnico de los bots no se muestra: queda en debug_log.txt.
 *
 * Los datos llegan del `EjecucionStore`, que consulta al backend cada poco más
 * de un segundo mientras el control está activo. Sin id en la dirección se
 * muestra la ejecución en curso o, si no hay, la última.
 */
@Component({
  selector: 'app-ejecucion',
  imports: [RouterLink, DatePipe, Alerta, ConfirmModal],
  templateUrl: './ejecucion.html',
})
export class EjecucionPantalla {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  protected readonly store = inject(EjecucionStore);

  /** De la ruta /ejecuciones/:id. */
  readonly id = input<string>();

  protected readonly insigniaEjecucion = INSIGNIA_EJECUCION;
  protected readonly etiquetaArchivo = ETIQUETA_ARCHIVO;
  protected readonly textoEtapa = TEXTO_ETAPA;
  protected readonly tamanoLegible = tamanoLegible;

  protected readonly confirmarCancelar = signal(false);
  protected readonly descargando = signal<string | null>(null);
  protected readonly errorDescarga = signal<string | null>(null);
  /** Clientes con la lista de archivos desplegada (por nombre). */
  private readonly carpetasAbiertas = signal<ReadonlySet<string>>(new Set());

  protected readonly ej = this.store.actual;
  protected readonly activa = computed(() => estaActiva(this.ej()?.estado));

  // --- Cronómetro --------------------------------------------------------------------

  /** Avanza una vez por segundo solo mientras el control está activo. */
  private readonly ahora = signal(Date.now());

  /** Desde `creada` (backend) hasta `terminada` o, si sigue corriendo, hasta ahora. */
  protected readonly duracion = computed(() => {
    const e = this.ej();
    return e ? duracion(e.creada, e.terminada, this.ahora()) : '';
  });

  // --- KPIs --------------------------------------------------------------------------

  /** Los que llegaron al final (los cancelados no cuentan). */
  protected readonly procesados = computed(() => this.ej()?.detalle_clientes.filter((c) => c.estado === 'ok' || c.estado === 'con_errores').length ?? 0);

  /** Pasos que fallaron, sumando todos los clientes. */
  protected readonly errores = computed(
    () => this.ej()?.detalle_clientes.reduce((n, c) => n + c.pasos.filter((p) => p.estado === 'error').length, 0) ?? 0,
  );

  protected readonly progreso = computed(() => avanceDe(this.ej()?.detalle_clientes.flatMap((c) => c.pasos) ?? []));

  protected readonly clientes = computed<ClienteVista[]>(() =>
    (this.ej()?.detalle_clientes ?? []).map((c, indice) => ({
      indice,
      nombre: c.nombre,
      insignia: INSIGNIA_CLIENTE[c.estado],
      etapas: etapasDe(c.pasos),
      avance: avanceDe(c.pasos),
      conError: c.estado === 'con_errores',
      problemas: problemasDe(c.pasos),
      carpeta: c.carpeta,
      archivos: c.archivos,
      origen: c,
    })),
  );

  // --- Pausa entre clientes ----------------------------------------------------------

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

  constructor() {
    void this.store.cargarHistorial();

    // El reloj corre solo con el control activo: al finalizar, el cronómetro queda quieto.
    effect((alLimpiar) => {
      if (!this.activa()) return;
      this.ahora.set(Date.now());
      const reloj = setInterval(() => this.ahora.set(Date.now()), 1000);
      alLimpiar(() => clearInterval(reloj));
    });

    effect(() => {
      const id = this.id();
      untracked(() => void this.abrir(id));
    });
  }

  private async abrir(id: string | undefined): Promise<void> {
    this.carpetasAbiertas.set(new Set());
    this.errorDescarga.set(null);
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

  // --- Cancelar ----------------------------------------------------------------------

  /** Se llama recién después de confirmar en el diálogo. */
  protected async cancelar(): Promise<void> {
    await this.store.cancelar();
    if (this.ej()?.cancelar_pedido) {
      this.toast.aviso('Cancelación pedida', 'El paso en curso termina y después el control se frena.');
    } else {
      this.toast.error('No se pudo cancelar el control', this.store.error() ?? undefined);
    }
  }

  // --- Archivos ----------------------------------------------------------------------

  protected carpetaAbierta(nombre: string): boolean {
    return this.carpetasAbiertas().has(nombre);
  }

  protected alternarCarpeta(nombre: string): void {
    this.carpetasAbiertas.update((abiertas) => {
      const s = new Set(abiertas);
      if (!s.delete(nombre)) s.add(nombre);
      return s;
    });
  }

  /** Baja el archivo del servidor y lo entrega como descarga del navegador. */
  protected async descargar(c: ClienteVista, archivo: ArchivoGenerado): Promise<void> {
    const e = this.ej();
    if (!e) return;
    this.descargando.set(`${c.indice}/${archivo.nombre}`);
    this.errorDescarga.set(null);
    try {
      descargarArchivo(archivo.nombre, await this.api.archivo(e.id, c.indice, archivo.nombre));
      this.toast.ok('Archivo descargado', archivo.nombre);
    } catch (err) {
      this.errorDescarga.set(mensajeDeError(err, `No se pudo descargar ${archivo.nombre} de ${c.nombre}.`));
    } finally {
      this.descargando.set(null);
    }
  }

  /** Todos los archivos del cliente, uno detrás del otro. */
  protected async descargarTodos(c: ClienteVista): Promise<void> {
    const e = this.ej();
    if (!e) return;
    this.descargando.set(`${c.indice}/*`);
    this.errorDescarga.set(null);
    try {
      for (const a of c.archivos) descargarArchivo(a.nombre, await this.api.archivo(e.id, c.indice, a.nombre));
      this.toast.ok('Archivos descargados', `${c.archivos.length} archivo(s) de ${c.nombre}`);
    } catch (err) {
      this.errorDescarga.set(mensajeDeError(err, `No se pudieron descargar los archivos de ${c.nombre}.`));
    } finally {
      this.descargando.set(null);
    }
  }
}
