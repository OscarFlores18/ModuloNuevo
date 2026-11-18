import { Component, DestroyRef, HostListener, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { AbstractControl, FormBuilder, ReactiveFormsModule, ValidationErrors, ValidatorFn, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { startWith } from 'rxjs';
import { CuitPipe } from '../../core/cuit.pipe';
import {
  aniosRecientes,
  isoADmy,
  nombreDelMes,
  rangoDelAnio,
  rangoDelMes,
} from '../../core/fechas';
import { Ajustes, Cliente, ControlesPorCliente, ErrorApi, PasoId, ResumenControl, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { EjecucionStore } from '../../core/services/ejecucion.store';
import { PerspectivaService } from '../../core/services/perspectiva.service';
import { SesionService } from '../../core/services/sesion.service';
import { Reintento, leerReintento } from '../../core/reintento';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { AlertasService } from '../../core/ui/alertas.service';
import { Encabezado } from '../../core/ui/encabezado';
import { ToastService } from '../../core/ui/toast';

type ModoPeriodo = 'mes' | 'anio' | 'rango';
type Operacion = 'ventas' | 'compras' | 'ambas';

/** Los tres pasos (ARCA -> Xubio -> Auditoría) se hacen siempre: no se eligen ni se muestran. */
const TODOS_LOS_PASOS: Record<PasoId, boolean> = { arca: true, xubio: true, auditoria: true };

/** Nada viene precargado: clientes, período y operaciones se eligen cada vez. */
// --- Validadores -------------------------------------------------------------------

/** Al menos un control del grupo en true (operaciones). */
const alMenosUno: ValidatorFn = (g: AbstractControl): ValidationErrors | null =>
  Object.values(g.value ?? {}).some(Boolean) ? null : { ninguno: true };

const listaNoVacia: ValidatorFn = (c: AbstractControl): ValidationErrors | null =>
  Array.isArray(c.value) && c.value.length ? null : { required: true };

/** En rango, «Hasta» no puede ser anterior a «Desde». Los ISO se comparan como texto. */
const rangoOrdenado: ValidatorFn = (g: AbstractControl): ValidationErrors | null => {
  const { modo, desde, hasta } = g.getRawValue() as { modo: ModoPeriodo; desde: string; hasta: string };
  return modo === 'rango' && desde && hasta && hasta < desde ? { rangoInvertido: true } : null;
};

/**
 * Un período que todavía no empezó no tiene información en ARCA ni en Xubio.
 * El tope del calendario no alcanza: escribiendo el mes a mano se podía pedir
 * diciembre en noviembre. Los ISO se comparan como texto.
 */
const periodoNoFuturo: ValidatorFn = (g: AbstractControl): ValidationErrors | null => {
  const { modo, mes, desde } = g.getRawValue() as { modo: ModoPeriodo; mes: string; desde: string };
  const hoy = new Date();
  const hoyIso = `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, '0')}-${String(hoy.getDate()).padStart(2, '0')}`;
  if (modo === 'mes' && mes && mes > hoyIso.slice(0, 7)) return { periodoFuturo: true };
  if (modo === 'rango' && desde && desde > hoyIso) return { periodoFuturo: true };
  return null;
};

const dos = (n: number) => String(n).padStart(2, '0');
const aIso = (f: Date) => `${f.getFullYear()}-${dos(f.getMonth() + 1)}-${dos(f.getDate())}`;
const fechaDmy = (f: Date) => `${dos(f.getDate())}/${dos(f.getMonth() + 1)}/${f.getFullYear()}`;
/** '05/10/2026' -> Date local (sin hora). */
/** Lo que se recuerda del último control que se ejecutó (en este navegador). */
interface UltimoControl {
  clientes: string[];
  modo: ModoPeriodo;
  mes: string;
  anio: string;
  desde: string;
  hasta: string;
  ventas: boolean;
  compras: boolean;
  /** ISO: cuándo se ejecutó. */
  fecha: string;
}

const CLAVE_ULTIMO = 'cf-ultimo-control';

function leerUltimo(): UltimoControl | null {
  try {
    const datos = JSON.parse(localStorage.getItem(CLAVE_ULTIMO) ?? 'null');
    return datos && Array.isArray(datos.clientes) ? (datos as UltimoControl) : null;
  } catch {
    return null;
  }
}

function guardarUltimo(u: UltimoControl): void {
  try {
    localStorage.setItem(CLAVE_ULTIMO, JSON.stringify(u));
  } catch {
    /* sin almacenamiento: no se recuerda */
  }
}

function dmyAFecha(dmy: string): Date {
  const [d, m, a] = dmy.split('/').map(Number);
  return new Date(a, m - 1, d);
}

@Component({
  selector: 'app-nuevo-control',
  imports: [ReactiveFormsModule, RouterLink, CuitPipe, Encabezado, Alerta, Ayuda],
  templateUrl: './control.html',
})
export class NuevoControl {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly clientes = inject(ClientesStore);
  protected readonly ejecucion = inject(EjecucionStore);
  protected readonly sesion = inject(SesionService);
  /** Empleado: ve todo el formulario, pero «Ejecutar control» queda bloqueado. */
  protected readonly perspectiva = inject(PerspectivaService);

  protected readonly anios = aniosRecientes();
  protected readonly tiposPeriodo: { id: ModoPeriodo; texto: string }[] = [
    { id: 'mes', texto: 'Mes completo' },
    { id: 'anio', texto: 'Año completo' },
    { id: 'rango', texto: 'Elegir fecha' },
  ];

  /** La credencial de Xubio recordada en el servidor: si coincide el usuario, la clave puede ir vacía. */
  private readonly ajustes = signal<Ajustes | null>(null);

  /** Xubio pide usuario y, salvo que el servidor la tenga guardada para ese usuario, la clave. */
  private readonly xubioCompleto: ValidatorFn = (g: AbstractControl): ValidationErrors | null => {
    const { usuario, clave } = g.getRawValue() as { usuario: string; clave: string };
    const guardada = this.ajustes()?.xubio;
    const hayClave = !!clave || (!!guardada?.tiene_clave && usuario.trim() === guardada.usuario);
    return usuario.trim() && hayClave ? null : { credencial: true };
  };

  // --- Formulario --------------------------------------------------------------------

  protected readonly form = this.fb.nonNullable.group({
    /** Nombres en el orden en que se procesan. */
    clientes: this.fb.nonNullable.control<string[]>([], listaNoVacia),
    periodo: this.fb.nonNullable.group(
      {
        modo: this.fb.nonNullable.control<ModoPeriodo>('mes'),
        mes: ['', Validators.required],
        anio: [{ value: '', disabled: true }, Validators.required],
        desde: [{ value: '', disabled: true }, Validators.required],
        hasta: [{ value: '', disabled: true }, Validators.required],
      },
      { validators: [rangoOrdenado, periodoNoFuturo] },
    ),
    operaciones: this.fb.nonNullable.group(
      { ventas: false, compras: false },
      { validators: alMenosUno },
    ),
    xubio: this.fb.nonNullable.group({ usuario: '', clave: '', recordar: false }, { validators: this.xubioCompleto }),
  });

  /** Buscador de clientes: fuera del form, no viaja al backend. */
  protected readonly busqueda = this.fb.nonNullable.control('');

  // --- Estado derivado (signals sobre valueChanges) ----------------------------------

  private readonly valor = toSignal(this.form.valueChanges.pipe(startWith(this.form.value)), { initialValue: this.form.value });
  private readonly textoBusqueda = toSignal(this.busqueda.valueChanges, { initialValue: '' });

  protected readonly modo = computed(() => this.valor().periodo?.modo ?? 'mes');
  protected readonly opcionesOperaciones: { id: Operacion; texto: string }[] = [
    { id: 'ventas', texto: 'Ventas' },
    { id: 'compras', texto: 'Compras' },
    { id: 'ambas', texto: 'Las dos' },
  ];

  /** Lo elegido en Operaciones, como una sola opción (null si no hay ninguna). */
  protected readonly operacion = computed<Operacion | null>(() => {
    const o = this.valor().operaciones;
    return o?.ventas && o?.compras ? 'ambas' : o?.ventas ? 'ventas' : o?.compras ? 'compras' : null;
  });
  protected readonly seleccion = computed(() => this.valor().clientes ?? []);

  protected readonly disponibles = computed(() => {
    const elegidos = new Set(this.seleccion());
    const filtro = this.textoBusqueda().trim().toLocaleLowerCase('es');
    const digitos = filtro.replace(/\D/g, '');
    const coincide = (c: Cliente) =>
      !filtro || c.nombre.toLocaleLowerCase('es').includes(filtro) || (digitos.length >= 3 && c.arca.cuit_representado.includes(digitos));
    return this.clientes.lista().filter((c) => !elegidos.has(c.nombre) && coincide(c));
  });

  protected readonly elegidos = computed(() => {
    const porNombre = new Map(this.clientes.lista().map((c) => [c.nombre, c]));
    return this.seleccion().map((n) => porNombre.get(n)).filter((c): c is Cliente => !!c);
  });

  /** El período en DD/MM/AAAA, o null si falta algo. */
  protected readonly periodo = computed(() => {
    const p = this.valor().periodo;
    if (!p) return null;
    if (p.modo === 'mes') return rangoDelMes(p.mes ?? '');
    if (p.modo === 'anio') return rangoDelAnio(p.anio ?? '');
    const desde = isoADmy(p.desde ?? '');
    const hasta = isoADmy(p.hasta ?? '');
    return desde && hasta ? { desde, hasta } : null;
  });

  /** Hoy, para que los calendarios no dejen elegir fechas futuras (ARCA no las acepta). */
  protected readonly hoyIso = aIso(new Date());
  protected readonly mesActualIso = this.hoyIso.slice(0, 7);

  /** Un clic para lo de todos los meses: el anterior (lo normal) y el que corre. */
  protected readonly mesesRapidos = (() => {
    const hoy = new Date();
    const anterior = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
    const iso = (f: Date) => `${f.getFullYear()}-${dos(f.getMonth() + 1)}`;
    const corto = (f: Date) => nombreDelMes(iso(f)).split(' ')[0];
    return [
      { valor: iso(anterior), texto: `Mes anterior (${corto(anterior)})` },
      { valor: iso(hoy), texto: `Este mes (${corto(hoy)})` },
    ];
  })();

  protected elegirMes(mes: string): void {
    const c = this.form.controls.periodo.controls.mes;
    c.setValue(mes);
    c.markAsDirty();
  }

  /**
   * Qué período se va a controlar, explicado: días, si se corta en hoy, si
   * abarca varios meses, o qué corregir. Lo ve quien arma el control antes de
   * ejecutar, así no hay sorpresas.
   */
  protected readonly explicacionPeriodo = computed<{ texto: string; avisos: string[]; error: string | null } | null>(() => {
    const p = this.valor().periodo;
    if (!p) return null;
    if (p.modo === 'mes' && p.mes && p.mes > this.mesActualIso) {
      return {
        texto: '',
        avisos: [],
        error: `${nombreDelMes(p.mes).replace(/^./, (l) => l.toUpperCase())} todavía no empezó: no hay información para controlar. Elegí ${nombreDelMes(this.mesActualIso)} o un mes anterior.`,
      };
    }
    if (p.modo === 'rango') {
      const d = p.desde ?? '';
      const h = p.hasta ?? '';
      if (!d || !h) return null;
      if (h < d) return { texto: '', avisos: [], error: '«Hasta» es anterior a «Desde»: cambiá una de las dos fechas.' };
      if (d > this.hoyIso) return { texto: '', avisos: [], error: '«Desde» todavía no llegó: elegí una fecha de hoy para atrás.' };
    }
    const per = this.periodo();
    if (!per) return null;
    const desde = dmyAFecha(per.desde);
    const hastaPedido = dmyAFecha(p.modo === 'rango' ? isoADmy(p.hasta ?? '') : per.hasta);
    const hoy = dmyAFecha(isoADmy(this.hoyIso));
    const hasta = hastaPedido > hoy ? hoy : hastaPedido;
    const dias = Math.round((hasta.getTime() - desde.getTime()) / 86_400_000) + 1;
    const avisos: string[] = [];
    const finDeMes = new Date(hasta.getFullYear(), hasta.getMonth() + 1, 0);
    if (hastaPedido > hoy || (p.modo !== 'rango' && hasta.getTime() === hoy.getTime() && hoy < finDeMes)) {
      avisos.push(`El período todavía no terminó: se controla hasta hoy (${fechaDmy(hoy)}).`);
    }
    const meses = (hasta.getFullYear() - desde.getFullYear()) * 12 + hasta.getMonth() - desde.getMonth() + 1;
    if (meses > 1) {
      avisos.push(`Abarca ${meses} meses y va todo en un mismo control. Para un control por mes, elegí «Mes completo».`);
    }
    return { texto: `Se controla del ${fechaDmy(desde)} al ${fechaDmy(hasta)} (${dias} día${dias === 1 ? '' : 's'}).`, avisos, error: null };
  });

  /** El usuario apretó «Cambiar» para escribir otra contraseña de Xubio. */
  protected readonly cambiandoClave = signal(false);

  protected usarGuardada(): void {
    this.form.controls.xubio.controls.clave.reset('');
    this.cambiandoClave.set(false);
  }

  protected readonly guardadaXubio = computed(() => {
    const a = this.ajustes()?.xubio;
    return !!a?.tiene_clave && (this.valor().xubio?.usuario ?? '').trim() === a.usuario;
  });

  /** Una línea para la barra de abajo: qué se va a ejecutar. */
  protected readonly resumen = computed(() => {
    const v = this.valor();
    const ops = [v.operaciones?.ventas && 'Ventas', v.operaciones?.compras && 'Compras'].filter(Boolean).join(' + ');
    const per = this.periodo();
    const nombres: Record<ModoPeriodo, () => string> = {
      mes: () => nombreDelMes(v.periodo?.mes ?? ''),
      anio: () => `año ${v.periodo?.anio}`,
      rango: () => (per ? `${per.desde} – ${per.hasta}` : ''),
    };
    const periodo = per ? nombres[v.periodo?.modo ?? 'mes']() : '';
    return [ops, periodo].filter(Boolean).join(' · ');
  });

  protected readonly intentado = signal(false);

  /** Lo que falta para poder ejecutar, en pocas palabras (panel de «Ejecutar»). */
  protected readonly faltan = computed(() => {
    this.valor();
    this.ajustes();
    const f = this.form.controls;
    const p: { texto: string; seccion: string }[] = [];
    if (f.clientes.invalid) p.push({ texto: 'Elegir clientes', seccion: 't-clientes' });
    if (f.periodo.invalid || !this.periodo()) p.push({ texto: 'Completar el período', seccion: 't-parametros' });
    if (f.operaciones.invalid) p.push({ texto: 'Elegir operaciones', seccion: 't-parametros' });
    if (f.xubio.invalid) p.push({ texto: 'Cuenta de Xubio', seccion: 't-xubio' });
    return p;
  });

  /** Elegidos a los que les falta algún dato: el bot los va a marcar con error. */
  protected readonly incompletos = computed(() => this.elegidos().filter((c) => c.faltantes.length));

  protected sacarIncompletos(): void {
    const fuera = new Set(this.incompletos().map((c) => c.nombre));
    this.setClientes(this.form.controls.clientes.value.filter((n) => !fuera.has(n)));
  }

  /** Lleva a la sección que falta completar y la remarca un momento. */
  protected irA(seccion: string): void {
    const caja = document.getElementById(seccion)?.closest('section');
    if (!caja) return;
    caja.scrollIntoView({ behavior: 'smooth', block: 'center' });
    caja.classList.add('nc-resaltar');
    setTimeout(() => caja.classList.remove('nc-resaltar'), 1600);
  }

  // --- 1er / 2do control ---------------------------------------------------------------

  /** Lo que respondió el servidor para la selección y el período actuales. */
  private readonly controles = signal<ControlesPorCliente>({});
  private pedidoControles = 0;

  /** Una etiqueta por operación elegida: «1er control», «2do control»… */
  protected etiquetasControl(nombre: string): { texto: string; titulo: string; repetido: boolean }[] {
    const c = this.controles()[nombre];
    const o = this.valor().operaciones;
    if (!c || !this.periodo()) return [];
    const ops = [o?.ventas && (['Ventas', c.ventas] as const), o?.compras && (['Compras', c.compras] as const)].filter(
      (x): x is readonly ['Ventas' | 'Compras', ResumenControl] => !!x,
    );
    if (!ops.length) return [];
    const detalle = ([op, r]: readonly ['Ventas' | 'Compras', ResumenControl]) =>
      r.ultimo
        ? `${op}: ya hubo ${r.realizados} control(es) en este período, el último el ${new Date(r.ultimo).toLocaleString('es-AR')}. Este será el ${r.proximo_texto}.`
        : `${op}: todavía no se controló este período.`;
    // Una sola etiqueta por cliente: si Ventas y Compras van por el mismo
    // control dice «4to control»; si no, «V: 4to · C: 2do control».
    const mismos = ops.every(([, r]) => r.proximo === ops[0][1].proximo);
    const texto = mismos
      ? ops[0][1].proximo_texto
      : ops.map(([op, r]) => `${op === 'Ventas' ? 'V' : 'C'}: ${r.proximo_texto.replace(' control', '')}`).join(' · ') + ' control';
    return [{ texto, titulo: ops.map(detalle).join('\n'), repetido: ops.some(([, r]) => r.realizados > 0) }];
  }

  private async cargarControles(clientes: string[], desde: string): Promise<void> {
    const n = ++this.pedidoControles;
    try {
      const r = await this.api.controles(clientes, desde);
      if (n === this.pedidoControles) this.controles.set(r);
    } catch {
      // Es solo informativo: si falla, no se muestran las etiquetas.
      if (n === this.pedidoControles) this.controles.set({});
    }
  }

  constructor() {
    // Al cambiar los clientes elegidos o el período, se pregunta qué control le toca a cada uno.
    effect(() => {
      const clientes = this.seleccion();
      const desde = this.periodo()?.desde;
      untracked(() => {
        if (!clientes.length || !desde) {
          this.pedidoControles++;
          this.controles.set({});
        } else void this.cargarControles(clientes, desde);
      });
    });

    void this.clientes.cargar(true);
    void this.cargarAjustes();
    this.reaccionarACambios();

    // Viene de «Reintentar los que fallaron» (Ejecuciones).
    const reintento = leerReintento(typeof history !== 'undefined' ? history.state : null);
    if (reintento) this.aplicarReintento(reintento);
    else this.aplicarUltimo();

    // Errores de conexión de la lista de clientes: toast, no un cartel en el layout.
    // Solo depende del error: el toast se dispara fuera del seguimiento del effect.
    effect(() => {
      const e = this.clientes.error();
      if (e) untracked(() => void this.alertas.mostrarErrorHumano('No se pudieron cargar los clientes', '', e));
    });

    // Si un cliente recordado ya no existe, se saca de la selección.
    effect(() => {
      if (!this.clientes.cargado()) return;
      const existentes = new Set(this.clientes.lista().map((c) => c.nombre));
      const sel = this.form.controls.clientes.value;
      if (sel.some((n) => !existentes.has(n))) untracked(() => this.form.controls.clientes.setValue(sel.filter((n) => existentes.has(n))));
    });
  }

  /** Las dependencias entre campos, con valueChanges: habilitar = mostrar y validar. */
  private reaccionarACambios(): void {
    const { periodo } = this.form.controls;

    // Cada tipo de período habilita solo sus campos (el resto ni se valida ni se usa).
    const { mes, anio, desde, hasta } = periodo.controls;
    const campos: Record<ModoPeriodo, AbstractControl[]> = { mes: [mes], anio: [anio], rango: [desde, hasta] };
    periodo.controls.modo.valueChanges.pipe(startWith(periodo.controls.modo.value), takeUntilDestroyed(this.destroyRef)).subscribe((modo) => {
      for (const [m, lista] of Object.entries(campos)) {
        for (const c of lista) m === modo ? c.enable({ emitEvent: false }) : c.disable({ emitEvent: false });
      }
      periodo.updateValueAndValidity();
    });

    // Un mes o un «Desde» que todavía no empezó (escrito a mano, el calendario no
    // lo frena): se avisa con una alerta y se vuelve a una fecha válida.
    mes.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((valor) => {
      if (valor && valor > this.mesActualIso) {
        const pedido = nombreDelMes(valor);
        mes.setValue(this.mesActualIso);
        this.avisoPeriodo.set(`${pedido.replace(/^./, (l) => l.toUpperCase())} todavía no empezó: se dejó ${nombreDelMes(this.mesActualIso)}.`);
      } else if (valor && valor !== this.mesActualIso) {
        this.avisoPeriodo.set(null);
      }
    });
    desde.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((valor) => {
      if (valor && valor > this.hoyIso) {
        desde.setValue(this.hoyIso);
        this.avisoPeriodo.set(`Esa fecha todavía no llegó: se dejó «Desde» en hoy (${isoADmy(this.hoyIso)}).`);
      } else if (valor && valor !== this.hoyIso) {
        this.avisoPeriodo.set(null);
      }
    });
  }

  /** Se corrigió una fecha futura: se avisa al lado del período, sin ventana. */
  protected readonly avisoPeriodo = signal<string | null>(null);

  /** Aviso arriba del formulario cuando se armó desde un control anterior. */
  protected readonly avisoReintento = signal<string | null>(null);

  /** «Se cargó lo del último control»: con «Empezar de cero». */
  protected readonly avisoUltimo = signal<string | null>(null);

  /** Carga clientes, período y operaciones del último control ejecutado desde acá. */
  private aplicarUltimo(): void {
    const u = leerUltimo();
    if (!u) return;
    const { periodo, operaciones, clientes } = this.form.controls;
    if (u.modo === 'mes' && u.mes && u.mes <= this.mesActualIso) {
      periodo.controls.modo.setValue('mes');
      periodo.controls.mes.setValue(u.mes);
    } else if (u.modo === 'anio' && this.anios.includes(u.anio)) {
      periodo.controls.modo.setValue('anio');
      periodo.controls.anio.setValue(u.anio);
    } else if (u.modo === 'rango' && u.desde && u.hasta && u.desde <= this.hoyIso) {
      periodo.controls.modo.setValue('rango');
      periodo.controls.desde.setValue(u.desde);
      periodo.controls.hasta.setValue(u.hasta > this.hoyIso ? this.hoyIso : u.hasta);
    }
    if (u.ventas || u.compras) operaciones.setValue({ ventas: !!u.ventas, compras: !!u.compras });
    clientes.setValue([...u.clientes]);
    const cuando = new Date(u.fecha);
    const fecha = isNaN(cuando.getTime()) ? '' : ` (${cuando.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })})`;
    const n = u.clientes.length;
    this.avisoUltimo.set(`Se cargó lo del último control${fecha}: ${n} ${n === 1 ? 'cliente' : 'clientes'}, el mismo período y las mismas operaciones. Cambiá lo que necesites.`);
  }

  /** «Empezar de cero»: el formulario como la primera vez (el mes en curso, sin clientes). */
  protected empezarDeCero(): void {
    const { periodo, operaciones, clientes } = this.form.controls;
    periodo.controls.modo.setValue('mes');
    periodo.controls.mes.setValue(this.mesActualIso);
    operaciones.setValue({ ventas: false, compras: false });
    clientes.setValue([]);
    this.avisoUltimo.set(null);
    this.intentado.set(false);
  }

  /** Ctrl + Enter (⌘ + Enter en Mac) ejecuta desde cualquier parte de la pantalla. */
  @HostListener('document:keydown', ['$event'])
  protected alAtajo(ev: KeyboardEvent): void {
    if (ev.key !== 'Enter' || !(ev.ctrlKey || ev.metaKey)) return;
    if (document.querySelector('.swal2-container, dialog[open]')) return;
    ev.preventDefault();
    if (!this.ejecucion.enviando() && !this.ejecucion.hayActiva()) void this.ejecutar();
  }

  /**
   * Carga clientes, período y operaciones del control que se reintenta. Un mes
   * (o un año) completo vuelve como «Mes completo» («Año completo»); cualquier
   * otro período, como rango de fechas.
   */
  private aplicarReintento(r: Reintento): void {
    const [dd, mm, aaaa] = r.desde.split('/');
    const [hd, hm, ha] = r.hasta.split('/');
    const { periodo, operaciones, clientes } = this.form.controls;
    const finDeAnio = (hm === '12' && hd === '31') || r.hasta === isoADmy(this.hoyIso);
    if (dd === '01' && mm === hm && aaaa === ha) {
      periodo.controls.modo.setValue('mes');
      periodo.controls.mes.setValue(`${aaaa}-${mm}`);
    } else if (dd === '01' && mm === '01' && aaaa === ha && finDeAnio && this.anios.includes(aaaa)) {
      periodo.controls.modo.setValue('anio');
      periodo.controls.anio.setValue(aaaa);
    } else {
      periodo.controls.modo.setValue('rango');
      periodo.controls.desde.setValue(`${aaaa}-${mm}-${dd}`);
      periodo.controls.hasta.setValue(`${ha}-${hm}-${hd}`);
    }
    operaciones.setValue({ ventas: !!r.ventas, compras: !!r.compras });
    clientes.setValue([...r.clientes]);
    const n = r.clientes.length;
    const cuando = new Date(r.creada);
    const fecha = isNaN(cuando.getTime()) ? '' : ` del ${cuando.toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })}`;
    this.avisoReintento.set(
      `Reintento del control${fecha}: se ${n === 1 ? 'cargó el cliente que falló' : `cargaron los ${n} clientes que fallaron`}, con el mismo período y las mismas operaciones. Revisá y ejecutá.`,
    );
    // Si se recarga la página, el formulario no se vuelve a pisar.
    try {
      history.replaceState({ ...history.state, reintento: null }, '');
    } catch {
      // Sin acceso al historial del navegador no pasa nada.
    }
  }

  private async cargarAjustes(): Promise<void> {
    try {
      const a = await this.api.ajustes();
      this.ajustes.set(a);
      this.form.patchValue({ xubio: { usuario: a.xubio.usuario, recordar: a.xubio.recordar } });
      // La clave guardada cambia lo que valida Xubio.
      this.form.controls.xubio.updateValueAndValidity();
    } catch (e) {
      void this.alertas.mostrarErrorHumano('No se pudo hablar con el servidor', '', mensajeDeError(e, 'No se cargaron los ajustes.'));
    }
  }

  protected elegirOperacion(o: Operacion): void {
    const g = this.form.controls.operaciones;
    g.setValue({ ventas: o !== 'compras', compras: o !== 'ventas' });
    g.markAsDirty();
  }

  // --- Clientes ----------------------------------------------------------------------

  private setClientes(lista: string[]): void {
    const c = this.form.controls.clientes;
    c.setValue(lista);
    c.markAsDirty();
  }

  protected agregar(nombre: string): void {
    const sel = this.form.controls.clientes.value;
    if (!sel.includes(nombre)) this.setClientes([...sel, nombre]);
  }

  protected quitar(nombre: string): void {
    this.setClientes(this.form.controls.clientes.value.filter((n) => n !== nombre));
  }

  protected agregarVisibles(): void {
    this.setClientes([...this.form.controls.clientes.value, ...this.disponibles().map((c) => c.nombre)]);
    this.busqueda.setValue('');
  }

  protected limpiar(): void {
    this.setClientes([]);
  }

  /** Enter en el buscador: si queda uno solo, se suma. */
  protected alEnter(evento: Event): void {
    evento.preventDefault();
    const d = this.disponibles();
    if (d.length === 1) {
      this.agregar(d[0].nombre);
      this.busqueda.setValue('');
    }
  }

  protected mover(i: number, paso: -1 | 1): void {
    const s = [...this.form.controls.clientes.value];
    const j = i + paso;
    if (j < 0 || j >= s.length) return;
    [s[i], s[j]] = [s[j], s[i]];
    this.setClientes(s);
  }

  // --- Ejecutar ----------------------------------------------------------------------

  protected invalido(ruta: string): boolean {
    const c = this.form.get(ruta);
    return !!c && c.enabled && c.invalid && (c.dirty || this.intentado());
  }

  protected async ejecutar(): Promise<void> {
    if (!this.perspectiva.canEdit()) return;
    this.intentado.set(true);
    const periodo = this.periodo();
    if (this.form.invalid || !periodo) {
      // Sin ventana: lo que falta está escrito al lado del botón; se lleva a la primera sección.
      this.form.markAllAsTouched();
      const primera = this.faltan()[0];
      if (primera) this.irA(primera.seccion);
      return;
    }

    // Los clientes con datos incompletos no frenan: el bot los marca con error.
    // Lo normal es sacarlos y correr el resto.
    const incompletos = this.incompletos();
    if (incompletos.length) {
      const todos = incompletos.length === this.elegidos().length;
      const r = await this.alertas.elegir(
        incompletos.length === 1 ? 'Un cliente tiene datos incompletos' : `${incompletos.length} clientes tienen datos incompletos`,
        todos
          ? 'Van a terminar con error. Completá sus datos en Clientes o ejecutá igual.'
          : 'Van a terminar con error. Podés correr el control sin ellos y completarlos después.',
        todos ? 'Ir a Clientes' : 'Ejecutar sin ellos',
        'Ejecutar igual',
        incompletos.map((c) => `${c.nombre}: falta ${c.faltantes.join(', ')}`),
      );
      if (!r) return;
      if (todos && r === 'recomendada') {
        void this.router.navigate(['/clientes']);
        return;
      }
      if (!todos && r === 'recomendada') this.sacarIncompletos();
    }

    const v = this.form.getRawValue();
    try {
      const ej = await this.ejecucion.iniciar({
        clientes: v.clientes,
        desde: periodo.desde,
        hasta: periodo.hasta,
        ventas: v.operaciones.ventas,
        compras: v.operaciones.compras,
        pasos: TODOS_LOS_PASOS,
        carpeta: '',
        xubio: { usuario: v.xubio.usuario.trim(), clave: v.xubio.clave, recordar: v.xubio.recordar },
      });
      this.form.controls.xubio.controls.clave.reset('');
      const p = v.periodo;
      guardarUltimo({
        clientes: v.clientes,
        modo: p.modo,
        mes: p.mes,
        anio: p.anio,
        desde: p.desde,
        hasta: p.hasta,
        ventas: v.operaciones.ventas,
        compras: v.operaciones.compras,
        fecha: new Date().toISOString(),
      });
      this.toast.ok('Control iniciado', `${v.clientes.length} cliente(s) · ${this.resumen()}`);
      void this.router.navigate(['/ejecuciones', ej.id]);
    } catch (e) {
      // Mensaje claro para el contador y, plegado, el detalle técnico.
      void this.alertas.mostrarErrorHumano('No se pudo iniciar el control', e instanceof ErrorApi ? e.tecnico : '', mensajeDeError(e));
    }
  }
}
