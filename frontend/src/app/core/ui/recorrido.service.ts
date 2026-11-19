import { Injectable, signal } from '@angular/core';

export interface PasoRecorrido {
  /** Qué se señala: `[data-tour="…"]`. Sin elemento en pantalla, el paso se saltea. */
  objetivo: string;
  titulo: string;
  texto: string;
}

/** El recorrido de cada pantalla (por el primer tramo de la ruta). */
const RECORRIDOS: Record<string, PasoRecorrido[]> = {
  control: [
    { objetivo: 'circuito', titulo: 'Cómo se trabaja', texto: 'Estos son los pasos de todo el trabajo, de punta a punta. El que estás haciendo queda en azul; tocá cualquiera para ir a esa pantalla.' },
    { objetivo: 'clientes', titulo: '1. Elegí los clientes', texto: 'Tocá un cliente de «Disponibles» para sumarlo a «A procesar». Se controlan en ese orden. Si tiene un «!», le faltan datos.' },
    { objetivo: 'periodo', titulo: '2. Período y operaciones', texto: 'Elegí el mes (o un año o un rango de fechas) y si controlás ventas, compras o las dos. Un mes que todavía no empezó no se puede elegir.' },
    { objetivo: 'xubio', titulo: '3. Cuenta de Xubio', texto: 'El usuario con el que el bot entra a Xubio. Si guardás la contraseña, la próxima vez no hace falta escribirla.' },
    { objetivo: 'ejecutar', titulo: '4. Ejecutar', texto: 'Arranca los bots y te lleva a ver el avance. Atajo: Ctrl + Enter. La próxima vez se cargan solos los clientes y el período del último control.' },
    { objetivo: 'ayuda-recorrido', titulo: '¿Te perdiste?', texto: 'Tocá este «?» para volver a ver el recorrido de cualquier pantalla. Los «?» chiquitos al lado de cada dato explican qué es.' },
  ],
  ejecuciones: [
    { objetivo: 'estado', titulo: 'El control', texto: 'Qué mes, cuándo empezó, cuántos clientes y comprobantes. Mientras corre ves quién se está procesando y cuánto falta.' },
    { objetivo: 'tareas', titulo: 'Qué hay que hacer', texto: 'Al terminar, esta pestaña es lo primero: las tareas en orden. Lo que falta en Xubio se sube con «⬆ Subir a Xubio» (destildá lo que no va); los errores de carga se corrigen con «Aplicar cambios». El número es lo que queda por hacer.' },
    { objetivo: 'clientes-ej', titulo: 'Clientes y archivos', texto: 'Cada cliente con sus pasos (ARCA, Xubio, auditoría), el Excel del control para descargar y lo que bajaron los bots.' },
    { objetivo: 'historial', titulo: 'Historial', texto: 'Los controles anteriores, por día y con sus clientes. Buscá por cliente o mes; con el tachito lo sacás de la lista (los Excel quedan en las carpetas).' },
  ],
  'convertir-aux': [
    { objetivo: 'elegir', titulo: '1. Elegí los Excel del control', texto: 'Lo más fácil: en el control, «⬆ Subir a Xubio» trae acá solo los comprobantes que dejaste tildados. Si no, arrastrá los Excel «AUX - Control de facturas…» que dejó el control en la carpeta del cliente.' },
    { objetivo: 'resultado', titulo: 'Subilos a Xubio', texto: 'Revisá los comprobantes, confirmá la empresa y tocá «Subir a Xubio»: el bot deja el archivo listo en Xubio y vos apretás «Importar» en la ventana de Chrome. Si preferís, descargá el archivo y lo importás a mano.' },
  ],
  clientes: [
    { objetivo: 'nuevo-cliente', titulo: 'Nuevo cliente', texto: 'Cargá el nombre tal como figura en Xubio, la CUIT con la que se entra a ARCA, la clave fiscal y la CUIT de la empresa que se controla.' },
    { objetivo: 'lista-clientes', titulo: 'Listado', texto: 'Tocá un cliente para editarlo. «Faltan» quiere decir que le faltan datos para correr el control.' },
  ],
};

const PREFIJO_VISTO = 'cf-recorrido-visto:';

/**
 * Recorrido guiado: la primera vez que se entra a una pantalla señala sus
 * partes una por una. Se puede saltar y volver a ver con el «?» del encabezado.
 */
@Injectable({ providedIn: 'root' })
export class RecorridoService {
  /** El recorrido en curso: los pasos que tienen algo en pantalla y cuál se muestra. */
  readonly actual = signal<{ pantalla: string; pasos: PasoRecorrido[]; indice: number } | null>(null);

  private espera: ReturnType<typeof setTimeout> | null = null;

  pantallaDe(url: string): string {
    return url.split(/[/?#]/).filter(Boolean)[0] ?? 'control';
  }

  hayRecorrido(pantalla: string): boolean {
    return !!RECORRIDOS[pantalla]?.length;
  }

  /** Al llegar a una pantalla: si es la primera vez, arranca solo (cuando terminó de dibujarse). */
  alEntrar(url: string): void {
    if (this.espera) clearTimeout(this.espera);
    this.cerrar();
    const pantalla = this.pantallaDe(url);
    if (!this.hayRecorrido(pantalla) || this.yaVisto(pantalla)) return;
    this.espera = setTimeout(() => {
      // Si hay una ventana abierta (SweetAlert, un formulario), no se interrumpe.
      if (document.querySelector('.swal2-container, dialog[open]')) return;
      this.iniciar(pantalla, true);
    }, 900);
  }

  /** `automatico`: si no hay nada que señalar todavía (pantalla vacía), queda para la próxima. */
  iniciar(pantalla: string, automatico = false): void {
    const pasos = (RECORRIDOS[pantalla] ?? []).filter((p) => this.elemento(p));
    if (!pasos.length) return;
    if (automatico && pasos.length < Math.min(2, RECORRIDOS[pantalla].length)) return;
    this.actual.set({ pantalla, pasos, indice: 0 });
  }

  siguiente(): void {
    const a = this.actual();
    if (!a) return;
    if (a.indice + 1 >= a.pasos.length) this.terminar();
    else this.actual.set({ ...a, indice: a.indice + 1 });
  }

  anterior(): void {
    const a = this.actual();
    if (a && a.indice > 0) this.actual.set({ ...a, indice: a.indice - 1 });
  }

  /** Terminar o saltar: no vuelve a aparecer solo en esa pantalla. */
  terminar(): void {
    const a = this.actual();
    if (a) this.marcarVisto(a.pantalla);
    this.cerrar();
  }

  private cerrar(): void {
    this.actual.set(null);
  }

  elemento(p: PasoRecorrido): HTMLElement | null {
    return document.querySelector<HTMLElement>(`[data-tour="${p.objetivo}"]`);
  }

  private yaVisto(pantalla: string): boolean {
    try {
      return localStorage.getItem(PREFIJO_VISTO + pantalla) === '1';
    } catch {
      return true; // sin almacenamiento no se insiste en cada visita
    }
  }

  private marcarVisto(pantalla: string): void {
    try {
      localStorage.setItem(PREFIJO_VISTO + pantalla, '1');
    } catch {
      /* nada */
    }
  }
}
