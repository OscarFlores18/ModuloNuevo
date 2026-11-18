import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { carpetaDelPeriodo, periodoLegible, periodoLegibleDe, tamanoLegible } from '../../core/fechas';
import { Advertencia, ArchivoGenerado, ClienteEjecucion, Corregido, FilaFaltanteExcel, PasoEjecucion, ResumenEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { descargarBlob } from '../../core/descargar';
import { reintentoDe } from '../../core/reintento';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { PerspectivaService } from '../../core/services/perspectiva.service';
import { auxDe } from '../../core/services/subida-xubio.service';
import { Alerta } from '../../core/ui/alerta';
import { AlertasService } from '../../core/ui/alertas.service';
import { ToastService } from '../../core/ui/toast';
import {
  FaltantesDelExcel,
  HojasExcel,
  FilaFaltante,
  CambioPar,
  cambiosDisponibles,
  claveExcel,
  contarErrores,
  esPrioridad,
  faltanteDesdeExcel,
  faltantesParaCargar,
  filasDelExcel,
  mostrarVentanaDeErrores,
} from './ventana-errores';
import { CorreccionElegida, elegirParaAplicar, estaIncluida, excluidasDe, guardarPrecarga, incluir, paraAplicar } from './seleccion';
import { GRUPOS, Tarea, guardarHechas, leerHechas, tareasDe } from './tareas';
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
  /** Partidas de ARCA no encontradas en el Libro IVA: van arriba y abren el acordeón. */
  prioritarias: Advertencia[];
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
  imports: [RouterLink, Alerta],
  templateUrl: './ejecucion.html',
})
export class EjecucionPantalla {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  protected readonly store = inject(EjecucionStore);
  /** Empleado: sin nada de Nuevo control (reintentar, «Volver a correr»); cancelar y responder bloqueados; descargar sí. */
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

  /** «Iniciado el 17/11/26 18:05 · Ventas y Compras». */
  protected readonly bajada = computed(() => {
    const e = this.ej();
    if (!e) return '';
    const ops = [e.parametros.ventas && 'Ventas', e.parametros.compras && 'Compras'].filter(Boolean).join(' y ');
    return [`Iniciado el ${this.fechaCorta(e.creada)}`, ops].filter(Boolean).join(' · ');
  });

  /** Comprobantes de ARCA que pasaron por el control (todas las operaciones de todos los clientes). */
  protected readonly comprobantesControlados = computed(() => (this.ej()?.resumenes ?? []).reduce((n, r) => n + r.arca, 0));

  protected readonly periodoLegibleDe = periodoLegibleDe;

  // --- Qué hay que hacer: los hallazgos como tareas para tildar --------------------------

  protected readonly tareas = computed(() => (this.activa() ? [] : tareasDe(this.ej()?.advertencias ?? [])));
  /** Tildadas por el usuario (se guardan en este navegador, por control). */
  protected readonly hechas = signal<ReadonlySet<string>>(new Set());
  protected readonly pendientes = computed(() => this.tareas().filter((t) => !this.estaHecha(t)).length);
  protected readonly gruposTareas = computed(() =>
    GRUPOS.map((g) => ({ ...g, tareas: this.tareas().filter((t) => t.grupo === g.id) })).filter((g) => g.tareas.length),
  );

  /**
   * Hecha a mano (tilde). Las correcciones (letra, tipo, importe, CUIT) no: su checkbox
   * las elige para «Aplicar cambios» y desaparecen recién cuando el re-chequeo las da por buenas.
   */
  protected estaHecha(t: Tarea): boolean {
    return !this.cambiosDe(t.advertencia).length && this.hechas().has(t.clave);
  }

  // --- Aplicar cambios: elegir con checkbox, corregir de verdad con el botón ----------

  /** Los pares de «Error» que se pueden corregir con el dato de ARCA. */
  protected readonly cambios = computed(() => (this.activa() ? [] : cambiosDisponibles(this.ej()?.advertencias ?? [])));
  /** Los tildados (se guardan en este navegador, por control). */
  protected readonly seleccionados = computed(() => {
    const id = this.ej()?.id;
    return this.cambios().filter((c) => paraAplicar(id, c.clave));
  });
  /** Esperando al servidor: los checkboxes y el botón quedan bloqueados. */
  protected readonly aplicando = signal(false);

  protected cambiosDe(a: Advertencia): CambioPar[] {
    return this.cambios().filter((c) => c.datos.cliente === a.cliente && c.datos.operacion === a.operacion && TIPO_CAMPO[a.tipo] === c.datos.campo);
  }

  protected elegidos(a: Advertencia): number {
    const id = this.ej()?.id;
    return this.cambiosDe(a).filter((c) => paraAplicar(id, c.clave)).length;
  }

  protected alternarCambios(a: Advertencia, ev: Event): void {
    if (!this.perspectiva.canEdit() || this.aplicando()) return;
    elegirParaAplicar(this.ej()?.id, this.cambiosDe(a).map((c) => c.clave), (ev.target as HTMLInputElement).checked);
  }

  /** Lo que ya dejó «Aplicar cambios»: el Excel nuevo de cada cliente y operación. */
  protected readonly corregidos = computed(() =>
    (this.ej()?.detalle_clientes ?? []).flatMap((c, indice) =>
      (['Compras', 'Ventas'] as const)
        .map((op) => ({ indice, cliente: c.nombre, operacion: op, dato: c.corregidos?.[op] }))
        .filter((x): x is { indice: number; cliente: string; operacion: 'Compras' | 'Ventas'; dato: Corregido } => !!x.dato),
    ),
  );

  /**
   * Manda los tildados al servidor: corrige el Libro IVA, vuelve a cruzar y devuelve el
   * control nuevo. Recién con esa respuesta cambia la pantalla.
   */
  protected async aplicarCambios(): Promise<void> {
    const e = this.ej();
    const elegidos = this.seleccionados();
    if (!e || !elegidos.length || this.aplicando() || !this.perspectiva.canEdit()) return;
    this.aplicando.set(true);
    try {
      const r = await this.api.aplicarCambios(e.id, elegidos.map((c) => c.datos));
      elegirParaAplicar(e.id, elegidos.map((c) => c.clave), false);
      this.store.reemplazar(r.ejecucion);
      void this.cargarHojas(e.id);
      const aplicados = r.resultados.reduce((n, x) => n + x.aplicados, 0);
      const fallidos = r.resultados.flatMap((x) => x.no_aplicados);
      this.toast.ok('Excel actualizado', `${aplicados} ${aplicados === 1 ? 'cambio aplicado' : 'cambios aplicados'} y control re-chequeado.`);
      if (fallidos.length) {
        this.toast.aviso(
          `${fallidos.length} ${fallidos.length === 1 ? 'cambio no se aplicó' : 'cambios no se aplicaron'}`,
          fallidos.map((f) => `${f.comprobante || f.numero}: ${f.motivo}`).join(' '),
        );
      }
    } catch (err) {
      void this.alertas.mostrarErrorHumano('No se pudieron aplicar los cambios', '', mensajeDeError(err));
    } finally {
      this.aplicando.set(false);
    }
  }

  /** «Descargar nuevo Excel»: el control re-chequeado. */
  protected descargarNuevo(x: { indice: number; dato: Corregido }): void {
    const c = this.clientes().find((v) => v.indice === x.indice);
    const archivo = c?.archivos.find((a) => a.nombre === x.dato.aux);
    if (c && archivo) void this.descargar(c, archivo);
  }

  /** Para la ventana de hallazgos: cuántos hay tildados y el botón. */
  private readonly aplicarDesdeVentana = {
    contar: () => this.seleccionados().length,
    alAplicar: () => void this.aplicarCambios(),
  };

  protected alternarHecha(t: Tarea): void {
    const id = this.ej()?.id;
    if (!id) return;
    const nuevas = new Set(this.hechas());
    if (nuevas.has(t.clave)) nuevas.delete(t.clave);
    else nuevas.add(t.clave);
    this.hechas.set(nuevas);
    guardarHechas(id, nuevas);
  }

  /** El destino del botón de la tarea; el empleado no ve los que arman un control nuevo. */
  protected destinoVisible(t: Tarea): Tarea['destino'] {
    const d = t.destino;
    return d && ('ruta' in d || this.perspectiva.canEdit()) ? d : undefined;
  }

  /** El botón de la tarea: una pantalla de la app o un control nuevo solo con ese cliente. */
  protected irA(t: Tarea): void {
    const d = this.destinoVisible(t);
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

  /** Se muestra «Subir a Xubio»: control terminado y está el AUX (deshabilitado si no hay partidas o es Empleado). */
  protected puedeSubir(a: Advertencia): boolean {
    const c = this.clientes().find((x) => x.nombre === a.cliente);
    return a.tipo === 'faltante_en_xubio' && !!a.operacion && !this.activa() && !!c && !!auxDe(c.origen, a.operacion);
  }

  /**
   * Lleva a «Faltantes a Xubio» con el paso 2 ya armado: el AUX del control sin las
   * filas destildadas y las correcciones elegidas en «Error». Sin arrastrar archivos.
   */
  protected subirAXubio(a: Advertencia): void {
    const e = this.ej();
    const c = this.clientes().find((x) => x.nombre === a.cliente);
    if (!this.perspectiva.canEdit() || !e || !c || !a.operacion) return;
    const aux = auxDe(c.origen, a.operacion);
    if (!aux) {
      this.toast.error('No se encontró el AUX de este control.', `${c.nombre} · ${a.operacion}: usá «Faltantes a Xubio» con el archivo.`);
      return;
    }
    const filas = this.faltantesDe(a);
    const partidas = filas.filter((f) => this.incluida(a, f));
    if (!partidas.length) {
      this.toast.aviso('No hay comprobantes tildados.', 'Tildá al menos uno para subirlo a Xubio.');
      return;
    }
    guardarPrecarga({
      ejecucionId: e.id,
      indice: c.indice,
      cliente: c.nombre,
      operacion: a.operacion,
      aux,
      excluir: excluidasDe(e.id, c.nombre, a.operacion),
      partidas: partidas.map(({ fecha, tipo, comprobante, cuit, razon, total }) => ({ fecha, tipo, comprobante, cuit, razon, total })),
      total: filas.length,
      correcciones: correccionesHechas(c.origen.corregidos?.[a.operacion]),
    });
    void this.router.navigate(['/convertir-aux'], { queryParams: { desde: 'control' } });
  }

  // --- Qué se sube: checkbox por comprobante (la misma elección que en la ventana) -----

  protected incluida(a: Advertencia, f: FilaFaltante): boolean {
    return estaIncluida(this.ej()?.id, a.cliente, a.operacion ?? '', f.clave);
  }

  protected tildadas(a: Advertencia, filas: readonly FilaFaltante[]): number {
    return filas.filter((f) => this.incluida(a, f)).length;
  }

  protected alternarFila(a: Advertencia, f: FilaFaltante, ev: Event): void {
    if (!this.perspectiva.canEdit()) return;
    incluir(this.ej()?.id, a.cliente, a.operacion ?? '', f.clave, (ev.target as HTMLInputElement).checked);
  }

  protected alternarTodas(a: Advertencia, filas: readonly FilaFaltante[], ev: Event): void {
    if (!this.perspectiva.canEdit()) return;
    const si = (ev.target as HTMLInputElement).checked;
    for (const f of filas) incluir(this.ej()?.id, a.cliente, a.operacion ?? '', f.clave, si);
  }

  /** Para la ventana de hallazgos (función suelta: se pasa como callback). */
  private readonly alSubir = (a: Advertencia) => this.subirAXubio(a);

  /** La ventana de hallazgos con una advertencia (las prioritarias del acordeón del cliente). */
  protected verAdvertencia(a: Advertencia): void {
    const reales = a.comprobantes.length || a.tipo !== 'faltante_en_xubio' ? [a] : [];
    void mostrarVentanaDeErrores(reales, [], this.perspectiva.canEdit(), this.alSubir, this.ej()?.id, this.hojaDe(a), this.aplicarDesdeVentana);
  }

  /** La ventana de hallazgos solo con esa tarea: los comprobantes tal cual están en el Excel. */
  protected verDetalle(t: Tarea): void {
    void mostrarVentanaDeErrores([t.advertencia], [], this.perspectiva.canEdit(), this.alSubir, this.ej()?.id, this.hojaDe(t.advertencia), this.aplicarDesdeVentana);
  }

  // --- Lo que no cruzó, tal como está en los AUX ---------------------------------------

  /** Por «cliente|operación»: partidas de ARCA sin Libro IVA y del Libro IVA sin ARCA, leídas del Excel. */
  private readonly hojas = signal<HojasExcel>({ sinLibro: new Map(), sinArca: new Map() });

  /** Solo la parte del Excel de esa advertencia (para abrir la ventana con un solo hallazgo). */
  private hojaDe(a: Advertencia): HojasExcel | undefined {
    if (!a.operacion || (a.tipo !== 'faltante_en_xubio' && a.tipo !== 'faltante_en_arca')) return undefined;
    const clave = claveExcel(a.cliente, a.operacion);
    const lado = a.tipo === 'faltante_en_xubio' ? 'sinLibro' : 'sinArca';
    const filas = this.hojas()[lado].get(clave);
    if (!filas) return undefined;
    const vacio: FaltantesDelExcel = new Map();
    return { sinLibro: vacio, sinArca: vacio, [lado]: new Map([[clave, filas]]) };
  }

  /** Para «Qué hay que hacer» y el acordeón: la hoja del Excel o, si no se pudo leer, lo del hallazgo. */
  protected faltantesDe(a: Advertencia): FilaFaltante[] {
    const filas = a.operacion ? this.hojas().sinLibro.get(claveExcel(a.cliente, a.operacion)) : undefined;
    return filas && a.operacion ? filasDelExcel(filas, this.ej()?.advertencias ?? [], a.cliente, a.operacion) : faltantesParaCargar(a);
  }

  /** Lee las hojas cuando el control terminó (una vez por control). */
  private readonly claveHojas = computed(() => {
    const e = this.ej();
    return e && !estaActiva(e.estado) ? e.id : null;
  });

  private async cargarHojas(id: string): Promise<void> {
    const e = this.ej();
    if (!e) return;
    const sinLibro = new Map<string, FilaFaltanteExcel[]>();
    const sinArca = new Map<string, FilaFaltanteExcel[]>();
    await Promise.all(
      e.detalle_clientes.map(async (c, indice) => {
        if (!c.archivos.some((a) => /^AUX - Control de facturas/i.test(a.nombre))) return;
        try {
          const leido = await this.api.faltantes(id, indice);
          for (const op of ['Ventas', 'Compras'] as const) {
            if (leido.sin_libro?.[op]) sinLibro.set(claveExcel(c.nombre, op), leido.sin_libro[op]);
            if (leido.sin_arca?.[op]) sinArca.set(claveExcel(c.nombre, op), leido.sin_arca[op]);
          }
        } catch {
          // Sin el Excel se muestra lo del hallazgo.
        }
      }),
    );
    if (this.claveHojas() === id) this.hojas.set({ sinLibro, sinArca });
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
    if (!this.perspectiva.canEdit()) return;
    const r = this.reintento();
    if (r) void this.router.navigate(['/control'], { state: { reintento: r } });
  }

  private readonly formatoFecha = new Intl.DateTimeFormat('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

  /** «17/11/26 18:05». */
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

  // --- Reloj (cuenta regresiva de la pausa entre clientes) ---------------------------

  /** Avanza una vez por segundo solo mientras el control está activo. */
  private readonly ahora = signal(Date.now());


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
      prioritarias: this.prioritariasDe(c.nombre),
      carpeta: c.carpeta,
      archivos: ordenarArchivos(c.archivos),
      origen: c,
    })),
  );

  /**
   * Faltantes en IVA del cliente: los hallazgos «faltante_en_xubio» y, si la hoja del
   * Excel tiene partidas de una operación sin hallazgo (el control las pasó a «Error»),
   * uno armado desde la hoja, para que se vean igual que en el Excel.
   */
  private prioritariasDe(cliente: string): Advertencia[] {
    const reales = (this.ej()?.advertencias ?? []).filter((a) => a.cliente === cliente && esPrioridad(a));
    const desdeHoja = (['Ventas', 'Compras'] as const)
      .filter((op) => !reales.some((a) => a.operacion === op))
      .map((op) => [op, this.hojas().sinLibro.get(claveExcel(cliente, op))?.length ?? 0] as const)
      .filter(([, n]) => n > 0)
      .map(([op, n]) => faltanteDesdeExcel(cliente, op, n));
    return [...reales, ...desdeHoja];
  }

  // --- Pausa entre clientes ----------------------------------------------------------

  protected readonly segundosPausa = computed(() => {
    const p = this.ej()?.pausa;
    return p ? Math.max(0, Math.ceil(p.vence - this.ahora() / 1000)) : 0;
  });
  // --- Ventana de errores (lo que encontró la auditoría y los pasos que fallaron) -----

  protected readonly conteoErrores = computed(() => contarErrores(this.ej()?.advertencias ?? []));

  /** Cuántas filas de faltantes se muestran en la tarjeta (el resto, en «Ver comprobantes»). */
  protected readonly maxFaltantes = 8;

  protected verErrores(): void {
    const a = this.ej()?.advertencias;
    if (a?.length || this.hojas().sinLibro.size || this.hojas().sinArca.size)
      void mostrarVentanaDeErrores(a ?? [], this.ej()?.resumenes ?? [], this.perspectiva.canEdit(), this.alSubir, this.ej()?.id, this.hojas(), this.aplicarDesdeVentana);
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

    // El reloj corre solo con el control activo.
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

    // Al terminar (o al abrir uno terminado), se leen las hojas «Faltantes en Libro IVA».
    effect(() => {
      const id = this.claveHojas();
      untracked(() => {
        this.hojas.set({ sinLibro: new Map(), sinArca: new Map() });
        if (id) void this.cargarHojas(id);
      });
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
    if (!this.perspectiva.canEdit()) return;
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

/** Qué campo corrige cada tipo de error (para encontrar los pares de una tarea). */
const TIPO_CAMPO: Partial<Record<Advertencia['tipo'], string>> = {
  letra_distinta: 'letra',
  tipo_distinto: 'tipo',
  importe_distinto: 'importe',
  cuit_distinto: 'cuit',
};

const NOMBRE_CAMPO: Record<string, string> = { letra: 'Letra', tipo: 'Tipo', importe: 'Importe', cuit: 'CUIT' };
const PESOS = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS' });

/** Lo ya corregido en el Excel («Aplicar cambios»): falta hacerlo también en Xubio. */
function correccionesHechas(c: Corregido | undefined): CorreccionElegida[] {
  return (c?.cambios ?? []).map((x) => {
    const valor = x.campo === 'importe' && typeof x.valor_arca === 'number' ? PESOS.format(x.valor_arca) : String(x.valor_arca);
    return {
      comprobante: x.comprobante || `${x.pv}-${x.numero}`,
      razon: '',
      texto: `${NOMBRE_CAMPO[x.campo] ?? x.campo}: ${String(x.antes ?? '?')} → ${valor} (ya corregido en el Excel; corregilo también en Xubio)`,
    };
  });
}

/** Primero el AUX (el que se abre para revisar) y después lo que bajaron ARCA y Xubio. */
function ordenarArchivos(archivos: ArchivoGenerado[]): ArchivoGenerado[] {
  return [...archivos].sort((a, b) => (ORDEN_ARCHIVO[a.tipo] ?? 9) - (ORDEN_ARCHIVO[b.tipo] ?? 9) || a.nombre.localeCompare(b.nombre, 'es'));
}
