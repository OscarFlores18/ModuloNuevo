import { Component, computed, effect, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { CuitPipe } from '../../core/cuit.pipe';
import { carpetaDelPeriodo, isoADmy, mesAnterior, nombreDelMes, rangoDelMes } from '../../core/fechas';
import { Ajustes, Cliente, PasoId, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { SesionService } from '../../core/services/sesion.service';
import { Alerta } from '../../core/ui/alerta';
import { ControlSegmentado, OpcionSegmentada } from '../../core/ui/control-segmentado';
import { Encabezado } from '../../core/ui/encabezado';
import { Plegable } from '../../core/ui/plegable';

type ModoPeriodo = 'mes' | 'rango';

/**
 * Lo que se recuerda del formulario en este navegador. Ni la contraseña ni la
 * carpeta: la carpeta siempre arranca en la del servidor (CARPETA_SALIDA), así
 * otro servidor u otra configuración nunca escriben en una carpeta vieja.
 */
interface Preferencias {
  ventas: boolean;
  compras: boolean;
  pasos: Record<PasoId, boolean>;
  seleccion: string[];
}

const CLAVE_PREFS = 'control-fct.formulario';

function leerPreferencias(): Partial<Preferencias> {
  try {
    return JSON.parse(localStorage.getItem(CLAVE_PREFS) ?? '{}') as Partial<Preferencias>;
  } catch {
    return {};
  }
}

export interface PasoInfo {
  id: PasoId;
  numero: number;
  titulo: string;
  detalle: string;
}

export const PASOS: PasoInfo[] = [
  { id: 'arca', numero: 1, titulo: 'ARCA', detalle: 'Baja «Mis Comprobantes» (emitidos y/o recibidos) en Excel.' },
  { id: 'xubio', numero: 2, titulo: 'Xubio', detalle: 'Baja el Libro IVA Ventas y/o Compras de la empresa.' },
  { id: 'auditoria', numero: 3, titulo: 'Auditoría', detalle: 'Cruza ARCA contra Xubio y arma el Excel de control.' },
];

@Component({
  selector: 'app-nuevo-control',
  imports: [RouterLink, CuitPipe, Alerta, ControlSegmentado, Encabezado, Plegable],
  templateUrl: './control.html',
})
export class NuevoControl {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly clientes = inject(ClientesStore);
  protected readonly ejecucion = inject(EjecucionStore);
  protected readonly sesion = inject(SesionService);
  protected readonly pasosInfo = PASOS;

  private readonly prefs = leerPreferencias();

  // --- 1. Clientes -------------------------------------------------------------
  protected readonly busqueda = signal('');
  /** En el orden en que se van a procesar (el orden en que se eligieron). */
  protected readonly seleccion = signal<string[]>(this.prefs.seleccion ?? []);

  protected readonly disponibles = computed(() => {
    const elegidos = new Set(this.seleccion());
    const filtro = this.busqueda().trim().toLocaleLowerCase('es');
    const digitos = filtro.replace(/\D/g, '');
    // Se busca por nombre o por CUIT (con o sin guiones).
    const coincide = (c: Cliente) =>
      !filtro || c.nombre.toLocaleLowerCase('es').includes(filtro) || (digitos.length >= 3 && c.arca.cuit_representado.includes(digitos));
    return this.clientes.lista().filter((c) => !elegidos.has(c.nombre) && coincide(c));
  });
  protected readonly elegidos = computed(() => {
    const porNombre = new Map(this.clientes.lista().map((c) => [c.nombre, c]));
    return this.seleccion().map((n) => porNombre.get(n)).filter((c): c is Cliente => !!c);
  });

  // --- 2. Período ----------------------------------------------------------------
  protected readonly modosPeriodo: OpcionSegmentada<ModoPeriodo>[] = [
    { id: 'mes', etiqueta: 'Mes completo' },
    { id: 'rango', etiqueta: 'Otro rango de fechas' },
  ];
  protected readonly modoPeriodo = signal<ModoPeriodo>('mes');
  protected readonly mes = signal(mesAnterior());
  protected readonly desdeIso = signal('');
  protected readonly hastaIso = signal('');
  protected readonly nombreDelMes = nombreDelMes;

  protected readonly periodo = computed(() => {
    if (this.modoPeriodo() === 'mes') return rangoDelMes(this.mes());
    const desde = isoADmy(this.desdeIso());
    const hasta = isoADmy(this.hastaIso());
    return desde && hasta ? { desde, hasta } : null;
  });

  // --- 3. Qué controlar ------------------------------------------------------------
  protected readonly ventas = signal(this.prefs.ventas ?? true);
  protected readonly compras = signal(this.prefs.compras ?? false);
  protected readonly pasos = signal<Record<PasoId, boolean>>({ arca: true, xubio: true, auditoria: true, ...this.prefs.pasos });

  // --- 4. Xubio y carpeta ------------------------------------------------------------
  protected readonly ajustes = signal<Ajustes | null>(null);
  protected readonly xubioUsuario = signal('');
  protected readonly xubioClave = signal('');
  protected readonly xubioRecordar = signal(false);
  protected readonly verClave = signal(false);
  protected readonly carpeta = signal('');

  protected readonly error = signal<string | null>(null);
  /** Se marca al tocar «Ejecutar»: recién ahí se muestran los faltantes en rojo. */
  protected readonly intentado = signal(false);

  /** Lo que falta para poder ejecutar, en palabras. */
  protected readonly problemas = computed(() => {
    const p: string[] = [];
    if (!this.seleccion().length) p.push('Elegí al menos un cliente.');
    const per = this.periodo();
    if (!per) p.push('Completá el período.');
    else if (this.modoPeriodo() === 'rango' && this.desdeIso() > this.hastaIso()) p.push('La fecha «Hasta» es anterior a «Desde».');
    if (!this.ventas() && !this.compras()) p.push('Elegí Ventas, Compras o las dos.');
    const pasos = this.pasos();
    if (!pasos.arca && !pasos.xubio && !pasos.auditoria) p.push('Elegí al menos un paso.');
    if (pasos.xubio) {
      const a = this.ajustes()?.xubio;
      const usuario = this.xubioUsuario().trim();
      const hayClave = !!this.xubioClave() || (!!a?.tiene_clave && usuario === a.usuario);
      if (!usuario || !hayClave) p.push('El paso de Xubio necesita el usuario y la contraseña de Xubio.');
    }
    if (!this.carpeta().trim()) p.push('Indicá la carpeta de salida.');
    return p;
  });

  /** Clientes elegidos a los que les faltan datos para los pasos elegidos. */
  protected readonly avisosClientes = computed(() => {
    const pasos = this.pasos();
    return this.elegidos()
      .map((c) => ({
        nombre: c.nombre,
        faltan: c.faltantes.filter((f) => (f === 'URL de Xubio' ? pasos.xubio : pasos.arca)),
      }))
      .filter((c) => c.faltan.length)
      .map((c) => `${c.nombre}: falta ${c.faltan.join(', ').toLocaleLowerCase('es')}`);
  });

  protected readonly ejemploCarpeta = computed(() => {
    const base = this.carpeta().trim().replace(/[\\/]+$/, '') || '<carpeta>';
    const cliente = this.elegidos()[0]?.nombre ?? '<Cliente>';
    const sep = base.includes('/') && !base.includes('\\') ? '/' : '\\';
    return [base, cliente, carpetaDelPeriodo(this.periodo()?.desde ?? ''), this.ajustes()?.subcarpeta ?? 'Control de Facturas'].join(sep);
  });

  protected readonly resumenOperaciones = computed(() =>
    [this.ventas() && 'Ventas', this.compras() && 'Compras'].filter(Boolean).join(' y ') || '—',
  );
  protected readonly resumenPasos = computed(() => PASOS.filter((p) => this.pasos()[p.id]).map((p) => p.titulo).join(' → ') || '—');

  constructor() {
    void this.clientes.cargar(true);
    void this.cargarAjustes();
    // Lo elegido se recuerda para la próxima vez (sin contraseñas).
    effect(() => {
      const prefs: Preferencias = { ventas: this.ventas(), compras: this.compras(), pasos: this.pasos(), seleccion: this.seleccion() };
      try {
        localStorage.setItem(CLAVE_PREFS, JSON.stringify(prefs));
      } catch {
        // Sin almacenamiento: no se recuerda.
      }
    });
    // Si un cliente recordado ya no existe, se saca de la selección.
    effect(() => {
      if (!this.clientes.cargado()) return;
      const existentes = new Set(this.clientes.lista().map((c) => c.nombre));
      const sel = this.seleccion();
      if (sel.some((n) => !existentes.has(n))) this.seleccion.set(sel.filter((n) => existentes.has(n)));
    });
  }

  private async cargarAjustes(): Promise<void> {
    try {
      const a = await this.api.ajustes();
      this.ajustes.set(a);
      this.carpeta.set(a.carpeta_salida);
      this.xubioUsuario.set(a.xubio.usuario);
      this.xubioRecordar.set(a.xubio.recordar);
    } catch (e) {
      this.error.set(mensajeDeError(e, 'No se pudieron cargar los ajustes del servidor.'));
    }
  }

  // --- Acciones ----------------------------------------------------------------------

  protected agregar(nombre: string): void {
    this.seleccion.update((s) => (s.includes(nombre) ? s : [...s, nombre]));
  }

  protected quitar(nombre: string): void {
    this.seleccion.update((s) => s.filter((n) => n !== nombre));
  }

  protected agregarVisibles(): void {
    this.seleccion.update((s) => [...s, ...this.disponibles().map((c) => c.nombre)]);
    this.busqueda.set('');
  }

  protected limpiar(): void {
    this.seleccion.set([]);
  }

  /** Enter en el buscador: si queda un solo cliente, se agrega. */
  protected alEnter(): void {
    const d = this.disponibles();
    if (d.length === 1) {
      this.agregar(d[0].nombre);
      this.busqueda.set('');
    }
  }

  protected mover(indice: number, paso: -1 | 1): void {
    this.seleccion.update((s) => {
      const j = indice + paso;
      if (j < 0 || j >= s.length) return s;
      const copia = [...s];
      [copia[indice], copia[j]] = [copia[j], copia[indice]];
      return copia;
    });
  }

  protected alternarPaso(id: PasoId): void {
    this.pasos.update((p) => ({ ...p, [id]: !p[id] }));
  }

  protected async ejecutar(): Promise<void> {
    this.intentado.set(true);
    this.error.set(null);
    const periodo = this.periodo();
    if (this.problemas().length || !periodo) return;
    try {
      const ej = await this.ejecucion.iniciar({
        clientes: this.seleccion(),
        desde: periodo.desde,
        hasta: periodo.hasta,
        ventas: this.ventas(),
        compras: this.compras(),
        pasos: this.pasos(),
        carpeta: this.carpeta().trim(),
        xubio: { usuario: this.xubioUsuario().trim(), clave: this.xubioClave(), recordar: this.xubioRecordar() },
      });
      this.xubioClave.set('');
      void this.router.navigate(['/ejecuciones', ej.id]);
    } catch (e) {
      this.error.set(mensajeDeError(e, 'No se pudo iniciar el control.'));
    }
  }
}
