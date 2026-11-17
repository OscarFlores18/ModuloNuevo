import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Encabezado } from '../../core/ui/encabezado';

/**
 * Manual de uso: completo pero directo, para quien conoce ARCA, Xubio y el
 * circuito de IVA. Explica cómo se usa y con qué criterio cruza. Solo texto.
 */
@Component({
  selector: 'app-ayuda',
  imports: [RouterLink, Encabezado],
  styles: `
    :host { display: flex; flex-direction: column; gap: 14px; font-size: 15px; line-height: 1.55; }
    section { scroll-margin-top: 140px; }
    h3 { margin: 0 0 8px; font-size: 1.15rem; font-weight: 800; text-transform: none; letter-spacing: normal; color: var(--color-texto); }
    h4 { margin: 12px 0 4px; font-size: 1rem; font-weight: 700; }
    p { margin: 0 0 6px; }
    ol, ul { margin: 0 0 6px; padding-left: 22px; }
    ol { list-style: decimal; }
    ul { list-style: disc; }
    li { margin: 3px 0; }
    li::marker { font-weight: 700; color: var(--color-acento); }
    .indice { display: flex; flex-wrap: wrap; gap: 6px; }
    .boton { display: inline-block; padding: 0 8px; border-radius: 6px; background: #eef2f7; border: 1px solid #d6dde8; font-weight: 700; font-size: .92em; white-space: nowrap; }
    .nota { margin-top: 8px; padding: 8px 12px; border-radius: 8px; background: #eff6ff; border: 1px solid #bfdbfe; }
    .tabla { width: 100%; border-collapse: collapse; font-size: 14px; }
    .tabla th, .tabla td { padding: 7px 9px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; }
    .tabla th { font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: #64748b; white-space: nowrap; }
    .tabla td:first-child { font-weight: 700; }
    .gravedad { display: inline-block; padding: 0 8px; border-radius: 4px; font-size: .74rem; font-weight: 800; text-transform: uppercase; white-space: nowrap; }
    .grave { background: #fde2e2; color: #9b2c2c; }
    .corregir { background: #fdf0d9; color: #7a4f0f; }
    .info { background: #e8eef9; color: #1d4ed8; }
    .color { display: inline-block; width: 26px; height: 16px; border-radius: 3px; border: 1px solid rgba(0,0,0,.15); vertical-align: middle; }
    code { font-family: Consolas, 'Cascadia Mono', monospace; font-size: .92em; background: #f1f5f9; padding: 0 4px; border-radius: 3px; }
    details { border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 12px; background: #fff; }
    details + details { margin-top: 6px; }
    summary { cursor: pointer; font-weight: 700; }
    details[open] summary { margin-bottom: 4px; }
  `,
  template: `
    <app-encabezado titulo="Manual de uso" bajada="Cómo se usa Control de Facturas y con qué criterio cruza ARCA contra Xubio." />

    <nav class="indice" aria-label="Contenido del manual">
      @for (s of secciones; track s.id) {
        <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="ir(s.id)">{{ s.texto }}</button>
      }
    </nav>

    <section id="circuito" class="mf-panel">
      <h3>1. Circuito</h3>
      <p>Por cada cliente y operación (Ventas / Compras) el sistema hace tres pasos, sin intervención:</p>
      <ol>
        <li><strong>ARCA:</strong> entra con la clave fiscal y baja <em>Mis Comprobantes</em> (emitidos o recibidos) del período.</li>
        <li><strong>Xubio:</strong> entra a la empresa del cliente y baja el <em>Libro IVA</em> correspondiente.</li>
        <li><strong>Auditoría (FO 04-01):</strong> cruza las dos planillas, arma el Excel de auditoría y la lista de hallazgos.</li>
      </ol>
      <p class="nota">Es solo lectura: no carga, modifica ni borra nada en ARCA ni en Xubio. Las correcciones se hacen a mano en Xubio.</p>
    </section>

    <section id="clientes" class="mf-panel">
      <h3>2. Datos del cliente</h3>
      <p>En <a routerLink="/clientes"><span class="boton">Clientes</span></a> (pide una contraseña propia de la sección):</p>
      <ul>
        <li><strong>Nombre:</strong> tiene que ser igual al de la empresa en Xubio; con ese nombre la elige el bot. También es el nombre de la carpeta del cliente.</li>
        <li><strong>CUIT de ingreso + clave fiscal:</strong> con los que se entra a ARCA (puede ser la del contador).</li>
        <li><strong>CUIT representado:</strong> la empresa cuyos comprobantes se consultan. Si está mal, ARCA devuelve vacío.</li>
      </ul>
      <p>
        Solo un <strong>supervisor</strong> da de alta, edita o borra clientes, y puede usar <span class="boton">Precargar clientes del estudio</span>.
        La clave fiscal nunca se muestra: al editar, dejarla vacía mantiene la guardada.
        Un cliente con <span class="gravedad corregir">Faltan datos</span> no puede correr completo.
      </p>
    </section>

    <section id="control" class="mf-panel">
      <h3>3. Lanzar un control</h3>
      <ol>
        <li>
          En <a routerLink="/control"><span class="boton">Nuevo control</span></a> sumá clientes a «A procesar» (clic, o
          <span class="boton">Sumar todos</span> / <span class="boton">Sumar encontrados</span> con la búsqueda). Se procesan en
          ese orden; <span class="boton">▴</span> <span class="boton">▾</span> lo cambian.
        </li>
        <li><strong>Período:</strong> mes, año o rango desde/hasta.</li>
        <li><strong>Operaciones:</strong> Ventas, Compras o Las dos.</li>
        <li><strong>Xubio:</strong> usuario y contraseña. «Recordar cuenta» la guarda en el servidor.</li>
        <li><span class="boton">Ejecutar control</span>. Solo corre un control a la vez.</li>
      </ol>
      <p class="nota">
        Al lado de cada cliente aparece qué número de control va a ser para ese período (<code>1er control</code>,
        <code>2do control</code>…). En amarillo indica que el período ya se controló; pasando el mouse se ve cuándo fue el último.
        Volver a correrlo no pisa el anterior: genera una carpeta nueva.
      </p>
    </section>

    <section id="ejecucion" class="mf-panel">
      <h3>4. Durante la ejecución</h3>
      <p>
        En <a routerLink="/ejecuciones"><span class="boton">Ejecuciones</span></a>: estado general, progreso, errores y duración;
        y por cliente, cada paso con su estado (pendiente, procesando, ok, aviso, error u omitido).
      </p>
      <ul>
        <li>
          Entre clientes hay una pausa con cuenta regresiva: <span class="boton">Continuar ahora</span>,
          <span class="boton">No preguntar más</span> (sigue sin pausas) o <span class="boton">Cancelar todo</span>. Si no se responde, continúa.
        </li>
        <li><span class="boton">Detener bot</span> corta al terminar la acción en curso. Lo ya generado queda disponible.</li>
        <li>Si falla un paso de un cliente, el resto de los clientes sigue igual.</li>
        <li>Los bots usan un Chrome en la computadora del servidor: no hay que cerrarlo ni usarlo mientras corre.</li>
      </ul>
    </section>

    <section id="criterio" class="mf-panel">
      <h3>5. Criterio de cruce</h3>
      <p>
        Cada comprobante se identifica como <code>Letra-PtoVta-Número</code> (ej. <code>A-00002-00000156</code>).
        En <strong>Compras</strong> se compara además por <strong>CUIT</strong> y por clase (<strong>FC / NC / ND</strong>),
        porque dos proveedores pueden tener el mismo número, y una factura y su nota de crédito también.
      </p>
      <p>Antes de informar faltantes, el cruce separa lo que tiene explicación:</p>
      <ul>
        <li>Mismo CUIT, punto de venta y número con otra letra → <strong>letra distinta</strong> (no se cuenta como faltante en ningún lado).</li>
        <li>Mismo CUIT y número con otra clase → <strong>tipo distinto</strong>.</li>
        <li>Liquidaciones de ARCA cuyo banco está en Xubio como <em>Extracto bancario</em> → informativo.</li>
        <li>Factura + NC del mismo CUIT y mismo importe, ambas sin cargar → se anulan, informativo.</li>
        <li>CUIT genérico del exterior (<code>50-</code>, <code>51-</code>, <code>55-</code>) y tiques / extractos → ARCA no los informa: van con las partidas sin ARCA, marcados como «Normal».</li>
      </ul>
      <p>Lo que queda sin par es faltante de un lado o del otro. Si un lado vino vacío, no se listan faltantes: se informa como grave.</p>
    </section>

    <section id="hallazgos" class="mf-panel">
      <h3>6. Hallazgos</h3>
      <p>
        Se abren al terminar (o con <span class="boton">Ver errores</span>), agrupados por cliente y ordenados por gravedad.
        Cada uno muestra las filas tal como están en ARCA y en Xubio, con el dato en error remarcado. Arriba se filtra por
        «corregir» o «info». Si hay muchos, se muestran los primeros y el resto está en la hoja indicada del Excel.
      </p>
      <div class="mf-scroll">
        <table class="tabla">
          <thead>
            <tr><th>Hallazgo</th><th>Gravedad</th><th>Qué hacer</th><th>Color Excel</th></tr>
          </thead>
          <tbody>
            <tr><td>ARCA no devolvió comprobantes</td><td><span class="gravedad grave">Grave</span></td><td>Revisar el CUIT representado y volver a correr.</td><td>—</td></tr>
            <tr><td>Xubio no devolvió el Libro IVA</td><td><span class="gravedad grave">Grave</span></td><td>El nombre del cliente no coincide con la empresa en Xubio.</td><td>—</td></tr>
            <tr><td>Un paso falló (ARCA / Xubio)</td><td><span class="gravedad grave">Grave</span></td><td>El hallazgo indica la causa (clave, CUIT, sesión). Corregir y volver a correr ese cliente.</td><td>—</td></tr>
            <tr><td>Letra distinta</td><td><span class="gravedad corregir">Corregir</span></td><td>Cambiar la letra en Xubio; el recuadro amarillo indica de cuál a cuál.</td><td><span class="color" style="background:#D9C3F0"></span></td></tr>
            <tr><td>Tipo distinto</td><td><span class="gravedad corregir">Corregir</span></td><td>Corregir el tipo (FC / NC / ND) en Xubio.</td><td><span class="color" style="background:#BDD7EE"></span></td></tr>
            <tr><td>En ARCA y no en el Libro IVA</td><td><span class="gravedad corregir">Corregir</span></td><td>Cargar en Xubio o consultar al cliente.</td><td><span class="color" style="background:#FF9999"></span></td></tr>
            <tr><td>Partidas del Libro IVA no encontradas en ARCA</td><td><span class="gravedad corregir">Corregir</span></td><td>Revisar fecha, CUIT o número cargados; si están bien, consultar al cliente. Incluye los tiques, extractos y proveedores del exterior, marcados como «Normal» (sin acción). Si solo hay de esos, queda como Info.</td><td><span class="color" style="background:#FFD966"></span></td></tr>
            <tr><td>Cargado dos veces</td><td><span class="gravedad corregir">Corregir</span></td><td>Mismo CUIT, número y tipo repetido. Eliminar el duplicado.</td><td><span class="color" style="background:#F4B084"></span></td></tr>
            <tr><td>Liquidación bancaria como extracto</td><td><span class="gravedad info">Info</span></td><td>Verificar que el importe esté en el extracto.</td><td><span class="color" style="background:#C6E0B4"></span></td></tr>
            <tr><td>Factura anulada con NC</td><td><span class="gravedad info">Info</span></td><td>Sin acción.</td><td><span class="color" style="background:#D9D9D9"></span></td></tr>
            <tr><td>Proveedor del exterior (dentro de las partidas sin ARCA)</td><td><span class="gravedad info">Normal</span></td><td>Sin acción.</td><td><span class="color" style="background:#9BD3D0"></span></td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section id="excel" class="mf-panel">
      <h3>7. Excel de auditoría y carpetas</h3>
      <h4>Hojas</h4>
      <ul>
        <li><strong>Mis Comprobantes:</strong> lo bajado de ARCA. Al final se agregan las partidas del Libro IVA sin par en ARCA.</li>
        <li><strong>Libro Iva:</strong> lo bajado de Xubio. La columna <em>Estado conciliación</em> marca «Letra distinta», «Tipo distinto» o «Duplicado».</li>
        <li><strong>Faltantes en Libro IVA (Ventas / Compras):</strong> lo de ARCA que no está en Xubio.</li>
        <li><strong>Referencias:</strong> qué significa cada color.</li>
      </ul>
      <p>Las filas se pintan con el color del hallazgo (tabla de arriba); las columnas de totales quedan como estaban.</p>
      <h4>Descarga</h4>
      <p>
        En Ejecuciones, por cliente, <span class="boton">Ver archivos</span>: planillas de ARCA, Xubio y auditoría.
        <span class="boton">Descargar todos</span> las ordena así:
      </p>
      <p><code>Cliente / mmaaaa / Control de facturas / 1er control / …</code> (Ventas y Compras en la misma carpeta del control).</p>
    </section>

    <section id="problemas" class="mf-panel">
      <h3>8. Problemas frecuentes</h3>
      <details>
        <summary>Muchos faltantes de los dos lados en el mismo cliente.</summary>
        <p>Suele ser un error de carga masivo (CUIT o punto de venta mal en Xubio) o un período distinto en las dos fuentes. Mirá si los números coinciden con otra letra o fecha antes de cargar nada.</p>
      </details>
      <details>
        <summary>Un comprobante está bien cargado pero aparece como faltante.</summary>
        <p>En Compras se compara CUIT + número + clase: revisá que el CUIT del proveedor y el tipo en Xubio sean los de ARCA. Una diferencia de fecha que lo saca del período también lo hace aparecer.</p>
      </details>
      <details>
        <summary>Falla el paso de ARCA.</summary>
        <p>Clave fiscal vencida o cambiada, CUIT de ingreso mal, o falta la relación con el CUIT representado. Corregir en Clientes.</p>
      </details>
      <details>
        <summary>Falla el paso de Xubio.</summary>
        <p>Usuario o contraseña de Xubio incorrectos, o el nombre del cliente no coincide con ninguna empresa de esa cuenta.</p>
      </details>
      <details>
        <summary>«No hay una sesión de ContadorWeb».</summary>
        <p>La página se abre desde ContadorWeb. Volvé a entrar desde ahí.</p>
      </details>
    </section>
  `,
})
export class AyudaPantalla {
  protected readonly secciones = [
    { id: 'circuito', texto: 'Circuito' },
    { id: 'clientes', texto: 'Datos del cliente' },
    { id: 'control', texto: 'Lanzar un control' },
    { id: 'ejecucion', texto: 'Durante la ejecución' },
    { id: 'criterio', texto: 'Criterio de cruce' },
    { id: 'hallazgos', texto: 'Hallazgos' },
    { id: 'excel', texto: 'Excel y carpetas' },
    { id: 'problemas', texto: 'Problemas frecuentes' },
  ];

  /** El índice lleva a cada sección (sin tocar la dirección de la página). */
  protected ir(id: string): void {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}
