/**
 * Descarga común del navegador: el archivo aparece en la barra de descargas y va
 * a la carpeta de descargas (o el navegador pregunta dónde, según su configuración).
 *
 * Antes se usaba el diálogo «Guardar como» de la File System Access API, pero el
 * archivo se escribía en silencio (sin descarga visible) y en algunos navegadores
 * el diálogo no aparecía: parecía que no bajaba nada.
 */
export function descargarArchivo(nombre: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
