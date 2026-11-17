import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { carpetaDelPeriodo, duracion, tamanoLegible } from '../../core/fechas';
import { ArchivoGenerado, ClienteEjecucion, PasoEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { descargarBlob } from '../../core/descargar';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { Alerta } from '../../core/ui/alerta';
import { AlertasService } from '../../core/ui/alertas.service';
import { ToastService } from '../../core/ui/toast';
import { contarErrores, mostrarVentanaDeErrores } from './ventana-errores';
import { ETIQUETA_ARCHIVO, Etapa, EstadoEtapa, INSIGNIA_CLIENTE, INSIGNIA_EJECUCION, Insignia, TEXTO_ETAPA, avanceDe, etapasDe } from './ejecucion.reglas';

/**
 * Aviso de errores de sintaxis: se abre solo hasta que el usuario lo cierra.
 * Cuenta como visto recién al apretar «Entendido»: si la página se recarga con
 * el aviso abierto, vuelve a aparecer.
 */
const CLAVE_VISTOS = 'cf-errores-sintaxis-vistos';
/** Abiertos en esta carga de la página (para no abrirlo dos veces a la vez). */
const abiertos = new Set<string>();

function leerVistos(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(CLAVE_VISTOS) ?? '[]') as string[];
  } catch {
    return [];
  }
}

function yaVisto(id: string): boolean {
  return abiertos.has(id) || leerVistos().includes(id);
}

function marcarVisto(id: string): void {
  try {
    sessionStorage.setItem(CLAVE_VISTOS, JSON.stringify([...new Set([...leerVistos(), id])]));
  } catch {
    // Sin sessionStorage alcanza con el Set en memoria.
  }
}

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
 * línea de tiempo (ARCA -> Xubio -> Auditoría).
 * El registro técnico de los bots no se muestra: queda en debug_log.txt.
 *
 * Los datos llegan del `EjecucionStore`, que consulta al backend cada poco más
 * de un segundo mientras el control está activo. Sin id en la dirección se
 * muestra la ejecución en curso o, si no hay, la última.
 */
@Component({
  selector: 'app-ejecucion',
  imports: [RouterLink, Alerta],
  templateUrl: './ejecucion.html',
})
export class EjecucionPantalla {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  protected readonly store = inject(EjecucionStore);

  /** De la ruta /ejecuciones/:id. */
  readonly id = input<string>();

  protected readonly insigniaEjecucion = INSIGNIA_EJECUCION;
  protected readonly etiquetaArchivo = ETIQUETA_ARCHIVO;
  protected readonly textoEtapa = TEXTO_ETAPA;
  /** Verde terminado, azul (con latido) procesando, gris pendiente. */
  protected readonly claseEtapa: Record<EstadoEtapa, string> = {
    ok: 'mf-badge mf-badge-ok',
    procesando: 'mf-badge mf-badge-acento',
    pendiente: 'mf-badge mf-badge-tenue',
    omitido: 'mf-badge mf-badge-tenue',
    aviso: 'mf-badge mf-badge-aviso',
    error: 'mf-badge mf-badge-error',
  };
  protected readonly tamanoLegible = tamanoLegible;

  protected readonly descargando = signal<string | null>(null);
  /** true: backend en esta PC, se guarda con ventanas de Windows. false (servidor): por el navegador. */
  protected readonly dialogosLocales = signal(false);
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
      archivos: ordenarArchivos(c.archivos),
      origen: c,
    })),
  );

  // --- Pausa entre clientes ----------------------------------------------------------

  protected readonly segundosPausa = computed(() => {
    const p = this.ej()?.pausa;
    return p ? Math.max(0, Math.ceil(p.vence - this.ahora() / 1000)) : 0;
  });
  // --- Ventana de errores (lo que encontró la auditoría y los pasos que fallaron) -----

  protected readonly conteoErrores = computed(() => contarErrores(this.ej()?.advertencias ?? []));

  protected verErrores(): void {
    const a = this.ej()?.advertencias;
    if (a?.length) void mostrarVentanaDeErrores(a, this.enlacesXubio());
  }

  /** Por cliente, el link de Xubio en su empresa (para «Corregir en Xubio»). */
  private enlacesXubio(): Record<string, string> {
    const enlaces: Record<string, string> = {};
    for (const c of this.ej()?.detalle_clientes ?? []) if (c.xubio_url) enlaces[c.nombre] = c.xubio_url;
    return enlaces;
  }

  protected readonly siguienteCliente = computed(() => {
    const e = this.ej();
    if (!e?.pausa) return null;
    const i = e.detalle_clientes.findIndex((c) => c.nombre === e.pausa!.cliente);
    return e.detalle_clientes[i + 1]?.nombre ?? null;
  });

  constructor() {
    this.api.ajustes().then((a) => this.dialogosLocales.set(a.dialogos_locales), () => this.dialogosLocales.set(false));
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

    // Al terminar un control con errores de sintaxis, el aviso se abre solo.
    effect(() => {
      const e = this.ej();
      if (!e || this.activa() || !e.advertencias?.length || yaVisto(e.id)) return;
      abiertos.add(e.id);
      untracked(() => void mostrarVentanaDeErrores(e.advertencias!, this.enlacesXubio()).then(() => marcarVisto(e.id)));
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

  // --- Detener ----------------------------------------------------------------------

  /** Pide confirmación (SweetAlert2) y, si se confirma, frena el control. */
  protected async detener(): Promise<void> {
    const ok = await this.alertas.confirmarAccionPeligrosa(
      '¿Detener el bot?',
      'El paso que se está haciendo termina igual; después se frena y los clientes que faltan quedan sin hacer.',
    );
    if (ok) await this.cancelar();
  }

  private async cancelar(): Promise<void> {
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

  /** El servidor (esta misma PC) abre «Guardar como» y copia ahí el archivo. */
  protected async descargar(c: ClienteVista, archivo: ArchivoGenerado): Promise<void> {
    const e = this.ej();
    if (!e) return;
    this.descargando.set(`${c.indice}/${archivo.nombre}`);
    this.errorDescarga.set(null);
    try {
      if (!this.dialogosLocales()) {
        descargarBlob(archivo.nombre, await this.api.archivo(e.id, c.indice, archivo.nombre));
        this.toast.ok('Archivo descargado', archivo.nombre);
        return;
      }
      const r = await this.api.guardarArchivo(e.id, c.indice, archivo.nombre);
      if (!r.cancelado) this.toast.ok('Archivo guardado', r.ruta);
    } catch (err) {
      this.errorDescarga.set(mensajeDeError(err, `No se pudo guardar «${archivo.nombre}».`));
    } finally {
      this.descargando.set(null);
    }
  }

  /**
   * Modo servidor: un .zip con Cliente/mmaaaa/Control de facturas/N° control. Modo local:
   * el servidor (esta misma PC) abre la ventana para elegir carpeta. Elegida: adentro van
   * mmaaaa con los archivos. Cerrada sin elegir: se crea Descargas/Cliente/mmaaaa.
   */
  protected async descargarTodos(c: ClienteVista): Promise<void> {
    const e = this.ej();
    if (!e) return;
    this.descargando.set(`${c.indice}/*`);
    this.errorDescarga.set(null);
    try {
      if (!this.dialogosLocales()) {
        // Mismo nombre que arma el backend: sin caracteres inválidos ni punto final («S.A.»).
        const cliente = c.nombre.replace(/[\\/*?:"<>|]/g, '').trim().replace(/[. ]+$/, '') || 'Cliente';
        descargarBlob(`${cliente} - ${carpetaDelPeriodo(e.parametros.desde)}.zip`, await this.api.zip(e.id, c.indice));
        this.toast.ok('Archivos descargados', 'Un .zip con las carpetas del cliente y del período.');
        return;
      }
      const r = await this.api.exportar(e.id, c.indice);
      const donde = r.elegida ? r.carpeta : `${r.carpeta} (no elegiste carpeta: se creó en Descargas)`;
      if (r.copiados.length) this.toast.ok('Archivos guardados', `${r.copiados.length} ${r.copiados.length === 1 ? 'archivo' : 'archivos'} en ${donde}`);
      if (r.fallas.length) this.errorDescarga.set(r.fallas.map((f) => `No se pudo guardar «${f.nombre}»: ${f.motivo}`).join(' '));
    } catch (err) {
      this.errorDescarga.set(mensajeDeError(err, `No se pudieron guardar los archivos de ${c.nombre}.`));
    } finally {
      this.descargando.set(null);
    }
  }
}

const ORDEN_ARCHIVO: Record<string, number> = { arca: 0, xubio: 1, auditoria: 2, otro: 3 };

/** En el orden en que se generan: ARCA, Xubio y al final la auditoría. */
function ordenarArchivos(archivos: ArchivoGenerado[]): ArchivoGenerado[] {
  return [...archivos].sort((a, b) => (ORDEN_ARCHIVO[a.tipo] ?? 9) - (ORDEN_ARCHIVO[b.tipo] ?? 9) || a.nombre.localeCompare(b.nombre, 'es'));
}
