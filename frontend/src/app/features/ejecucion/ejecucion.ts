import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { carpetaDelPeriodo, duracion, periodoLegible, periodoLegibleDe, tamanoLegible } from '../../core/fechas';
import { ArchivoGenerado, ClienteEjecucion, PasoEjecucion, ResumenEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { descargarBlob } from '../../core/descargar';
import { reintentoDe } from '../../core/reintento';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { Alerta } from '../../core/ui/alerta';
import { AlertasService } from '../../core/ui/alertas.service';
import { Ayuda } from '../../core/ui/ayuda';
import { ToastService } from '../../core/ui/toast';
import { ContextoVentana, contarErrores, mostrarVentanaDeErrores } from './ventana-errores';
import { GRUPOS, Tarea, guardarHechas, leerHechas, tareasDe } from './tareas';
import { ClientesStore } from '../../core/services/clientes.store';
import { PerspectivaService } from '../../core/services/perspectiva.service';
import { ETIQUETA_ARCHIVO, Etapa, EstadoEtapa, INSIGNIA_CLIENTE, INSIGNIA_EJECUCION, Insignia, TEXTO_ETAPA, avanceDe, etapasDe } from './ejecucion.reglas';

/** Un paso que terminó mal o con avisos: lo único que se muestra del detalle si algo falló. */
interface Problema {
  nivel: 'error' | 'aviso';
  /** El paso («ARCA · Compras»): para encontrar su tarea. */
  etiqueta: string;
  titulo: string;
  mensaje: string;
  /** Qué hacer y adónde ir (de la lista de tareas), cuando el control ya terminó. */
  tarea?: Tarea;
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
  /** Hallazgos de la auditoría y pasos fallidos de este cliente (grave + a corregir). */
  hallazgos: number;
  carpeta: string;
  archivos: ArchivoGenerado[];
  origen: ClienteEjecucion;
}

function problemasDe(pasos: readonly PasoEjecucion[]): Problema[] {
  return pasos
    .filter((p) => p.estado === 'error' || (p.estado === 'aviso' && !!p.mensaje))
    .map((p) => ({
      nivel: p.estado === 'error' ? ('error' as const) : ('aviso' as const),
      etiqueta: p.etiqueta,
      titulo: `${p.etiqueta} ${p.estado === 'error' ? 'falló' : 'terminó con avisos'}`,
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
  imports: [RouterLink, Alerta, Ayuda],
  templateUrl: './ejecucion.html',
})
export class EjecucionPantalla {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  protected readonly store = inject(EjecucionStore);
  private readonly clientesStore = inject(ClientesStore);
  /** «Usuario»: solo mira y descarga. */
  protected readonly perspectiva = inject(PerspectivaService);

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
  /** Clientes con la lista de archivos plegada (por nombre): de entrada se ven todos. */
  private readonly carpetasPlegadas = signal<ReadonlySet<string>>(new Set());

  protected readonly ej = this.store.actual;
  protected readonly activa = computed(() => estaActiva(this.ej()?.estado));

  // --- Encabezado ---------------------------------------------------------------------

  /** «Control de octubre 2026». */
  protected readonly titulo = computed(() => {
    const p = this.ej()?.parametros;
    return p ? `Control de ${periodoLegible(p.desde, p.hasta)}` : '';
  });

  /** «Iniciado el 17/11/2026 18:05 · Ana · Ventas y Compras». */
  protected readonly bajada = computed(() => {
    const e = this.ej();
    if (!e) return '';
    const ops = [e.parametros.ventas && 'Ventas', e.parametros.compras && 'Compras'].filter(Boolean).join(' y ');
    return [`Iniciado el ${this.fechaCorta(e.creada)}`, e.usuario, ops].filter(Boolean).join(' · ');
  });

  protected readonly periodoLegibleDe = periodoLegibleDe;

  // --- Qué hay que hacer: los hallazgos como tareas para tildar --------------------------

  protected readonly tareas = computed(() => (this.activa() ? [] : tareasDe(this.ej()?.advertencias ?? [])));
  /** Tildadas por el usuario (se guardan en este navegador, por control). */
  protected readonly hechas = signal<ReadonlySet<string>>(new Set());
  protected readonly pendientes = computed(() => this.tareas().filter((t) => !this.hechas().has(t.clave)).length);
  protected readonly gruposTareas = computed(() =>
    GRUPOS.map((g) => ({ ...g, tareas: this.tareas().filter((t) => t.grupo === g.id) })).filter((g) => g.tareas.length),
  );

  protected alternarHecha(t: Tarea): void {
    const id = this.ej()?.id;
    if (!id) return;
    const nuevas = new Set(this.hechas());
    if (nuevas.has(t.clave)) nuevas.delete(t.clave);
    else nuevas.add(t.clave);
    this.hechas.set(nuevas);
    guardarHechas(id, nuevas);
  }

  /** El botón de la tarea: una pantalla de la app o un control nuevo solo con ese cliente. */
  protected irA(t: Tarea): void {
    const d = t.destino;
    if (!d) return;
    if ('ruta' in d) {
      void this.router.navigate([d.ruta]);
      return;
    }
    const e = this.ej();
    if (!e) return;
    const p = e.parametros;
    const reintento = { clientes: [t.cliente], desde: p.desde, hasta: p.hasta, ventas: p.ventas, compras: p.compras, creada: e.creada };
    void this.router.navigate(['/control'], { state: { reintento } });
  }

  /** La ventana de hallazgos solo con esa tarea: los comprobantes tal cual están en el Excel. */
  protected verDetalle(t: Tarea): void {
    void mostrarVentanaDeErrores([t.advertencia], [], this.contextoVentana());
  }

  protected irATareas(): void {
    document.getElementById('t-tareas')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // --- Reintentar e historial ----------------------------------------------------------

  /** Terminado y con clientes que fallaron o no llegaron a correr: se pueden reintentar. */
  protected readonly reintento = computed(() => {
    const e = this.ej();
    return e && !this.activa() ? reintentoDe(e) : null;
  });

  /** Abre Nuevo control con esos clientes, el mismo período y las mismas operaciones. */
  protected reintentar(): void {
    const r = this.reintento();
    if (r) void this.router.navigate(['/control'], { state: { reintento: r } });
  }

  private readonly formatoFecha = new Intl.DateTimeFormat('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

  /** «17/11/26 18:05». */
  /** Saca un control del historial. Los Excel y las carpetas del cliente quedan. */
  protected async borrarDelHistorial(h: ResumenEjecucion): Promise<void> {
    const periodo = this.periodoLegibleDe(h.periodo);
    const ok = await this.alertas.confirmarAccionPeligrosa(
      '¿Borrar este control del historial?',
      `${periodo.charAt(0).toUpperCase() + periodo.slice(1)} ·${this.fechaCorta(h.creada)} · ${h.clientes} ${h.clientes === 1 ? 'cliente' : 'clientes'}. Solo se saca de esta lista: los Excel que generó quedan en las carpetas del cliente.`,
      'Sí, borrar',
    );
    if (!ok) return;
    const mirando = this.ej()?.id === h.id;
    try {
      await this.store.eliminar(h.id);
      this.toast.ok('Borrado', 'El control se sacó del historial.');
      if (mirando) void this.router.navigate(['/ejecuciones'], { replaceUrl: true });
    } catch (e) {
      void this.alertas.mostrarErrorHumano('No se pudo borrar', '', mensajeDeError(e));
    }
  }

  protected fechaCorta(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : this.formatoFecha.format(d).replace(',', '');
  }

  /** Una línea por control para la tabla del historial. */
  protected resultadoDe(h: ResumenEjecucion): string {
    const t = h.totales;
    const partes = [t.ok && `${t.ok} OK`, t.con_errores && `${t.con_errores} con errores`, t.pendientes && `${t.pendientes} sin hacer`].filter(Boolean);
    return partes.join(' · ') || '—';
  }

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
      problemas: problemasDe(c.pasos).map((p) => ({
        ...p,
        tarea: this.tareas().find(
          (t) => t.cliente === c.nombre && t.advertencia.tipo === 'paso_error' && t.advertencia.titulo.startsWith(p.etiqueta + ':'),
        ),
      })),
      hallazgos: (this.ej()?.advertencias ?? []).filter((a) => a.cliente === c.nombre && a.gravedad !== 'info').length,
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

  /** Mails de los clientes y período, para «Consultar al cliente». */
  private contextoVentana(): ContextoVentana {
    const emails: Record<string, string> = {};
    for (const c of this.clientesStore.lista()) if (c.email) emails[c.nombre] = c.email;
    return { emails, periodo: this.ej()?.periodo ?? '', soloVer: this.perspectiva.soloVer() };
  }

  protected verErrores(): void {
    const a = this.ej()?.advertencias;
    if (a?.length) void mostrarVentanaDeErrores(a, this.ej()?.resumenes ?? [], this.contextoVentana());
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
    void this.clientesStore.cargar();

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

    // Las tildes de cada control se recuperan al abrirlo.
    effect(() => {
      const id = this.ej()?.id;
      untracked(() => this.hechas.set(id ? leerHechas(id) : new Set()));
    });
  }

  private async abrir(id: string | undefined): Promise<void> {
    this.carpetasPlegadas.set(new Set());
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
      void this.alertas.mostrarErrorHumano('No se pudo cancelar el control', '', this.store.error() ?? '');
    }
  }

  // --- Archivos ----------------------------------------------------------------------

  protected carpetaAbierta(nombre: string): boolean {
    return !this.carpetasPlegadas().has(nombre);
  }

  protected alternarCarpeta(nombre: string): void {
    this.carpetasPlegadas.update((plegadas) => {
      const s = new Set(plegadas);
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

const ORDEN_ARCHIVO: Record<string, number> = { auditoria: 0, arca: 1, xubio: 2, otro: 3 };

/** Primero el AUX (el que se abre para revisar) y después lo que bajaron ARCA y Xubio. */
function ordenarArchivos(archivos: ArchivoGenerado[]): ArchivoGenerado[] {
  return [...archivos].sort((a, b) => (ORDEN_ARCHIVO[a.tipo] ?? 9) - (ORDEN_ARCHIVO[b.tipo] ?? 9) || a.nombre.localeCompare(b.nombre, 'es'));
}
