import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { carpetaDelPeriodo, duracion, tamanoLegible } from '../../core/fechas';
import { ArchivoGenerado, ClienteEjecucion, PasoEjecucion, estaActiva, mensajeDeError } from '../../core/models';
import { descargarBlob } from '../../core/descargar';
import { ApiService } from '../../core/services/api.service';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { Alerta } from '../../core/ui/alerta';
import { ConfirmModal } from '../../core/ui/confirm-modal';
import { ToastService } from '../../core/ui/toast';
import { mostrarErroresDeSintaxis } from './errores-sintaxis';
import { ETIQUETA_ARCHIVO, Etapa, INSIGNIA_CLIENTE, INSIGNIA_EJECUCION, Insignia, TEXTO_ETAPA, avanceDe, etapasDe } from './ejecucion.reglas';

/** Ejecuciones cuyo aviso de errores de sintaxis ya se abrió (para no repetirlo). */
const CLAVE_AVISADOS = 'cf-errores-sintaxis-vistos';
const avisados = new Set<string>();

function yaAvisado(id: string): boolean {
  if (avisados.has(id)) return true;
  try {
    return (JSON.parse(sessionStorage.getItem(CLAVE_AVISADOS) ?? '[]') as string[]).includes(id);
  } catch {
    return false;
  }
}

function marcarAvisado(id: string): void {
  avisados.add(id);
  try {
    sessionStorage.setItem(CLAVE_AVISADOS, JSON.stringify([...avisados]));
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
  imports: [RouterLink, Alerta, ConfirmModal],
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
  // --- Errores de sintaxis (letra distinta entre ARCA y Xubio) -----------------------

  protected readonly comprobantesConError = computed(() =>
    (this.ej()?.advertencias ?? []).reduce((n, a) => n + a.comprobantes.length, 0),
  );

  protected verErroresDeSintaxis(): void {
    const a = this.ej()?.advertencias;
    if (a?.length) void mostrarErroresDeSintaxis(a);
  }

  /** La pausa por comprobantes duplicados (null si no hay o es la de entre clientes). */
  protected readonly pausaDuplicados = computed(() => {
    const p = this.ej()?.pausa;
    return p?.tipo === 'duplicados' ? p : null;
  });
  protected readonly pausaEntreClientes = computed(() => {
    const p = this.ej()?.pausa;
    return p && p.tipo !== 'duplicados' ? p : null;
  });
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

    // Al terminar un control con errores de sintaxis, el aviso se abre solo una vez.
    effect(() => {
      const e = this.ej();
      if (!e || this.activa() || !e.advertencias?.length || yaAvisado(e.id)) return;
      marcarAvisado(e.id);
      untracked(() => void mostrarErroresDeSintaxis(e.advertencias!));
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
   * Modo servidor: un .zip con Cliente/mmaaaa(/Ventas|Compras). Modo local:
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
