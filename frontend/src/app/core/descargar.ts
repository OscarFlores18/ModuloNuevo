/**
 * Todas las descargas de la app pasan por acá: descarga común del navegador, el
 * archivo va a Descargas. En la app de escritorio (WebView2) se ve en el panel
 * de descargas de arriba a la derecha.
 */
export function descargarBlob(nombre: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
