"""Arma un Tesseract mínimo para meter en el instalador (lo usa build_app.ps1).

El Tesseract de UB Mannheim instalado pesa ~240 MB; copiado tal cual dejaba el
instalador en 158 MB, por encima del límite de 100 MB por archivo de GitHub.
Acá se copia solo lo que el OCR usa:

- tesseract.exe y las DLL que importa (de forma recursiva, leyendo la tabla de
  importaciones). Las de pango, cairo, ICU… son de las herramientas de
  entrenamiento y quedan afuera.
- Los idiomas español e inglés (no `osd`: la app no detecta la orientación).
- Sin la información de depuración (.debug_*) de MinGW, como `strip`:
  libtesseract-5.dll baja de ~100 MB a ~3 MB. Windows nunca carga esas secciones.

Uso: python preparar_tesseract.py <carpeta de Tesseract> <destino>
"""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

IDIOMAS = ("spa", "eng")


# --- Tabla de importaciones ----------------------------------------------------------


def _secciones(d: bytes, pe: int) -> list[tuple[int, int, int, int]]:
    n = struct.unpack_from("<H", d, pe + 6)[0]
    tam_opt = struct.unpack_from("<H", d, pe + 20)[0]
    tabla = pe + 24 + tam_opt
    return [struct.unpack_from("<IIII", d, tabla + i * 40 + 8) for i in range(n)]


def importaciones(ruta: Path) -> list[str]:
    """Las DLL que importa un PE (en minúscula)."""
    d = ruta.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    directorios = opt + (112 if magic == 0x20B else 96)
    rva = struct.unpack_from("<I", d, directorios + 8)[0]
    secciones = _secciones(d, pe)

    def offset(r: int) -> int | None:
        for vsize, va, rsize, rptr in secciones:
            if va <= r < va + max(vsize, rsize):
                return r - va + rptr
        return None

    salida: list[str] = []
    o = offset(rva) if rva else None
    while o is not None:
        nombre = struct.unpack_from("<I", d, o + 12)[0]
        if not nombre:
            break
        k = offset(nombre)
        salida.append(d[k:d.index(b"\0", k)].decode("latin-1").lower())
        o += 20
    return salida


def dlls_necesarias(carpeta: Path, exe: str = "tesseract.exe") -> list[Path]:
    locales = {p.name.lower(): p for p in carpeta.glob("*.dll")}
    vistas: set[str] = set()
    pendientes = [carpeta / exe]
    while pendientes:
        ruta = pendientes.pop()
        for nombre in importaciones(ruta):
            if nombre in locales and nombre not in vistas:
                vistas.add(nombre)
                pendientes.append(locales[nombre])
    return sorted(locales[n] for n in vistas)


# --- Sacar la depuración ---------------------------------------------------------------


def _alinear(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def quitar_depuracion(ruta: Path) -> int:
    """Bytes ahorrados (0 si no había nada que sacar o no se pudo con seguridad)."""
    d = bytearray(ruta.read_bytes())
    if d[:2] != b"MZ":
        return 0
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        return 0
    coff = pe + 4
    n_secciones = struct.unpack_from("<H", d, coff + 2)[0]
    p_simbolos, n_simbolos = struct.unpack_from("<II", d, coff + 8)
    tam_opt = struct.unpack_from("<H", d, coff + 16)[0]
    opt = coff + 20
    alin_seccion = struct.unpack_from("<I", d, opt + 32)[0]
    tabla = opt + tam_opt
    strtab = p_simbolos + n_simbolos * 18 if p_simbolos else 0

    secciones = []
    for i in range(n_secciones):
        o = tabla + i * 40
        nombre = bytes(d[o:o + 8]).rstrip(b"\0").decode("latin-1")
        if nombre.startswith("/") and strtab:
            k = strtab + int(nombre[1:])
            nombre = bytes(d[k:d.index(b"\0", k)]).decode("latin-1")
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", d, o + 8)
        secciones.append((nombre, vsize, va, rsize, rptr))

    quedan = [s for s in secciones if not s[0].startswith(".debug_")]
    if len(quedan) == len(secciones):
        return 0
    # Las de depuración tienen que ser las últimas en la tabla…
    if any(s[0].startswith(".debug_") for s in secciones[: len(quedan)]):
        return 0
    fin_datos = max(s[4] + s[3] for s in quedan)
    # …y estar después de todo lo demás en el archivo.
    if any(s[4] < fin_datos for s in secciones[len(quedan):] if s[3]):
        return 0

    struct.pack_into("<H", d, coff + 2, len(quedan))
    struct.pack_into("<II", d, coff + 8, 0, 0)  # sin tabla de símbolos COFF
    ultima = max(quedan, key=lambda s: s[2])
    struct.pack_into("<I", d, opt + 56, _alinear(ultima[2] + max(ultima[1], ultima[3]), alin_seccion))  # SizeOfImage
    struct.pack_into("<I", d, opt + 64, 0)  # CheckSum (no se verifica en DLL comunes)
    for i in range(len(quedan), n_secciones):
        o = tabla + i * 40
        d[o:o + 40] = b"\0" * 40
    ruta.write_bytes(bytes(d[:fin_datos]))
    return len(d) - fin_datos


# --- Armar la carpeta --------------------------------------------------------------------


def preparar(origen: Path, destino: Path) -> None:
    if destino.exists():
        shutil.rmtree(destino)
    (destino / "tessdata").mkdir(parents=True)
    archivos = [origen / "tesseract.exe", *dlls_necesarias(origen)]
    for a in archivos:
        shutil.copy2(a, destino / a.name)
    idiomas = []
    for idioma in IDIOMAS:
        datos = origen / "tessdata" / f"{idioma}.traineddata"
        if datos.exists():
            shutil.copy2(datos, destino / "tessdata" / datos.name)
            idiomas.append(idioma)
    ahorro = sum(quitar_depuracion(destino / a.name) for a in archivos)
    total = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())
    print(f"Tesseract: {len(archivos) - 1} DLL, idiomas {', '.join(idiomas) or 'ninguno'}, "
          f"{total / 1e6:.0f} MB (sin depuración: {ahorro / 1e6:.0f} MB menos)")
    if "spa" not in idiomas:
        print("AVISO: sin spa.traineddata (español): el OCR lee peor los acentos.")


if __name__ == "__main__":
    preparar(Path(sys.argv[1]), Path(sys.argv[2]))
