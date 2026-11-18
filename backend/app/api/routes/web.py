"""API que consume el frontend Angular.

Mantiene el contrato del módulo web (clientes sin clave, ajustes, ejecuciones con
pasos por cliente y pausa, descarga por archivo) sobre nuestro backend: los bots
y las sesiones son los mismos de `backend/app`, y los datos siguen en
`config.json` / `xubio_credenciales.json`.

Las rutas `/api/jobs` siguen existiendo para uso programático.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ...auditor.csv_arca import convertir_auxiliares, filas_para_mostrar, libro_sin_arca
from ...core import acceso_clientes, config, dialogos
from ...core.controles import resumen_controles
from ...core.storage import ClientesRepository, CredencialesXubioRepository
from ...core.usuarios import Usuario, usuario_actual, usuario_por_defecto
from ...services import ejecuciones as ejecuciones_srv
from ...services import importacion_xubio as importacion_srv
from ...services import precarga as precarga_srv
from ..deps import exigir_acceso_clientes, get_clientes, get_credenciales_xubio

router = APIRouter(tags=["web"])


# --- Clientes -----------------------------------------------------------------


class DatosArca(BaseModel):
    model_config = ConfigDict(extra="allow")

    cuit_login: str = ""
    clave: str = ""
    cuit_representado: str = ""


class ClienteEntrada(BaseModel):
    model_config = ConfigDict(extra="allow")

    nombre: str = ""
    arca: DatosArca = Field(default_factory=DatosArca)


class RenombrarCliente(BaseModel):
    nuevo: str = ""


def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _faltantes(datos: dict) -> list[str]:
    arca = datos.get("arca") or {}
    falta = []
    if not str(arca.get("cuit_login") or "").strip():
        falta.append("CUIT de ingreso a ARCA")
    if not arca.get("tiene_clave"):
        falta.append("Clave fiscal")
    if not str(arca.get("cuit_representado") or "").strip():
        falta.append("CUIT representado")
    return falta


def _publico(nombre: str, datos: dict) -> dict:
    """Lo que ve el front: nunca la clave fiscal, solo el indicador."""
    arca = datos.get("arca") or {}
    salida = {
        "nombre": nombre,
        "arca": {
            "cuit_login": str(arca.get("cuit_login") or "").strip(),
            "cuit_representado": str(arca.get("cuit_representado") or "").strip(),
            "tiene_clave": bool(arca.get("tiene_clave")),
        },
        "faltantes": _faltantes(datos),
    }
    # Se conservan claves extra (AGIP, ARBA, categoría...) para la app de escritorio.
    for clave, valor in datos.items():
        if clave not in ("arca", "xubio", "nombre"):
            salida[clave] = valor
    return salida


def _combinar(previo: dict, body: ClienteEntrada) -> dict:
    arca_previo = previo.get("arca") or {}

    arca = {
        # `previo` es la vista pública: no trae `clave` ni `tiene_clave`.
        **{k: v for k, v in arca_previo.items() if k != "tiene_clave"},
        "cuit_login": _solo_digitos(body.arca.cuit_login)
        or str(arca_previo.get("cuit_login") or ""),
        "cuit_representado": _solo_digitos(body.arca.cuit_representado)
        or str(arca_previo.get("cuit_representado") or ""),
        # Una clave vacía al editar deja la que había (lo resuelve el repositorio).
        "clave": (body.arca.clave or "").strip(),
    }
    # Xubio no necesita datos por cliente: la empresa se elige por el nombre. Si hay
    # una dirección vieja en config.json (app de escritorio, DRLiqui) se deja como está.
    datos = dict(previo)
    datos.pop("nombre", None)
    datos.pop("faltantes", None)
    datos["arca"] = arca
    for clave, valor in (body.model_extra or {}).items():
        if clave != "xubio":
            datos[clave] = valor
    return datos


def _validar_nombre(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if not nombre:
        raise HTTPException(
            status_code=422, detail={"mensaje": "Poné el nombre del cliente."}
        )
    if re.search(r'[\\/*?:"<>|]', nombre):
        raise HTTPException(
            status_code=422,
            detail={
                "mensaje": 'El nombre no puede tener \\ / * ? : " < > | '
                "(se usa como nombre de carpeta)."
            },
        )
    return nombre


# --- Contraseña de la sección Clientes ---------------------------------------------


class EntrarClientes(BaseModel):
    clave: str = ""


@router.get("/clientes/acceso")
def estado_acceso_clientes(
    x_clientes_token: str | None = Header(default=None),
) -> dict:
    return {
        "configurada": acceso_clientes.configurada(),
        "desbloqueado": acceso_clientes.autorizado(x_clientes_token),
    }


@router.post("/clientes/acceso")
def entrar_clientes(body: EntrarClientes) -> dict:
    try:
        token, vence, creada = acceso_clientes.entrar(body.clave)
    except acceso_clientes.AccesoDenegado as exc:
        raise HTTPException(
            status_code=exc.estado, detail={"mensaje": exc.mensaje}
        ) from None
    return {"token": token, "expira": int(vence), "creada": creada}


@router.delete("/clientes/acceso", status_code=status.HTTP_204_NO_CONTENT)
def salir_clientes(x_clientes_token: str | None = Header(default=None)) -> None:
    acceso_clientes.salir(x_clientes_token)


@router.post("/clientes/precargar", dependencies=[Depends(exigir_acceso_clientes)])
def precargar_clientes(
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    """Agrega los clientes del estudio que falten (ver services/precarga.py)."""
    resultado = precarga_srv.precargar(repo).a_dict()
    resultado["clientes"] = [
        _publico(nombre, datos) for nombre, datos in repo.listar_publico().items()
    ]
    return resultado


@router.get("/clientes")
def listar_clientes(
    repo: ClientesRepository = Depends(get_clientes),
) -> list[dict]:
    return [
        _publico(nombre, datos)
        for nombre, datos in repo.listar_publico().items()
    ]


@router.post(
    "/clientes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exigir_acceso_clientes)],
)
def crear_cliente(
    body: ClienteEntrada,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    nombre = _validar_nombre(body.nombre)
    if repo.obtener_publico(nombre) is not None:
        raise HTTPException(
            status_code=409, detail={"mensaje": f"Ya hay un cliente llamado «{nombre}»."}
        )
    datos = _combinar({}, body)
    repo.guardar(nombre, datos)
    return _publico(nombre, repo.obtener_publico(nombre) or {})


@router.get("/clientes/{nombre}")
def obtener_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> dict:
    datos = repo.obtener_publico(nombre)
    if datos is None:
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )
    return _publico(nombre, datos)


@router.put("/clientes/{nombre}", dependencies=[Depends(exigir_acceso_clientes)])
def editar_cliente(
    nombre: str,
    body: ClienteEntrada,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    previo = repo.obtener_publico(nombre)
    if previo is None:
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )
    nuevo = _validar_nombre(body.nombre or nombre)
    datos = _combinar(previo, body)
    if nuevo != nombre:
        if repo.obtener_publico(nuevo) is not None:
            raise HTTPException(
                status_code=409,
                detail={"mensaje": f"Ya hay un cliente llamado «{nuevo}»."},
            )
        repo.renombrar(nombre, nuevo)
    repo.guardar(nuevo, datos)
    return _publico(nuevo, repo.obtener_publico(nuevo) or {})


@router.post(
    "/clientes/{nombre}/renombrar", dependencies=[Depends(exigir_acceso_clientes)]
)
def renombrar_cliente(
    nombre: str,
    body: RenombrarCliente,
    repo: ClientesRepository = Depends(get_clientes),
) -> dict:
    nuevo = _validar_nombre(body.nuevo)
    try:
        repo.renombrar(nombre, nuevo)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"mensaje": str(exc)}) from exc
    return _publico(nuevo, repo.obtener_publico(nuevo) or {})


@router.delete(
    "/clientes/{nombre}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(exigir_acceso_clientes)],
)
def borrar_cliente(
    nombre: str, repo: ClientesRepository = Depends(get_clientes)
) -> None:
    if not repo.eliminar(nombre):
        raise HTTPException(
            status_code=404, detail={"mensaje": f"No existe el cliente «{nombre}»."}
        )


# --- Controles (1er / 2do control) ------------------------------------------------


class ControlesRequest(BaseModel):
    clientes: list[str] = Field(default_factory=list)
    desde: str = ""  # DD/MM/AAAA


@router.post("/controles")
def controles(
    body: ControlesRequest,
    clientes: ClientesRepository = Depends(get_clientes),
) -> dict:
    """Por cliente y operación: cuántos controles hubo en el período y cuál sigue."""
    try:
        datetime.strptime(body.desde, "%d/%m/%Y")
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"mensaje": "Fecha «desde» inválida (DD/MM/AAAA)."}
        ) from exc
    existentes = clientes.listar_publico()
    salida: dict[str, dict] = {}
    for nombre in dict.fromkeys(body.clientes):
        if nombre not in existentes:
            continue
        salida[nombre] = {
            op.lower(): resumen_controles(config.OUTPUT_DIR, nombre, body.desde, op)
            for op in ("Ventas", "Compras")
        }
    return salida


# --- Convertir AUX ------------------------------------------------------------
#
# Pestaña aparte de los controles: se suben los AUX y vuelve, por cliente y
# período, un .zip con los CSV de Mis Comprobantes. No se guarda nada en disco.

MAX_AUX = 60
MAX_BYTES_AUX = 25 * 1024 * 1024


class ArchivoSubido(BaseModel):
    nombre: str = Field(min_length=1, max_length=260)
    contenido: str  # base64
    # Claves «CUIT|comprobante» que no se suben (destildadas en la pantalla del control).
    excluir: list[str] = Field(default_factory=list, max_length=5000)


class ConvertirAuxRequest(BaseModel):
    archivos: list[ArchivoSubido] = Field(min_length=1, max_length=MAX_AUX)


@router.post("/convertir-aux")
def convertir_aux(body: ConvertirAuxRequest) -> dict:
    """Pasa la hoja «Faltantes en Libro IVA» de cada AUX al CSV de Mis Comprobantes."""
    archivos: list[tuple[str, bytes]] = []
    excluir: dict[str, set[str]] = {}
    for a in body.archivos:
        if a.excluir:
            excluir[Path(a.nombre).name] = set(a.excluir)
        try:
            datos = base64.b64decode(a.contenido, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"mensaje": f"No se pudo leer «{a.nombre}»."}) from exc
        if len(datos) > MAX_BYTES_AUX:
            raise HTTPException(status_code=413, detail={"mensaje": f"«{a.nombre}» es demasiado grande."})
        archivos.append((a.nombre, datos))
    resultados = convertir_auxiliares(archivos, excluir)
    def b64(archivo: dict) -> dict:
        return {"nombre": archivo["nombre"], "contenido": base64.b64encode(archivo["contenido"]).decode("ascii")}

    for r in resultados:
        r["zips"] = {op: b64(z) for op, z in r["zips"].items()}
        if r.get("excel"):
            r["excel"] = b64(r["excel"])
    return {"resultados": resultados}


class CargarEnXubioRequest(BaseModel):
    cliente: str = Field(min_length=1)
    operacion: str
    zip: str  # base64 del .zip que devolvió /convertir-aux


@router.post("/convertir-aux/xubio")
def cargar_en_xubio(
    body: CargarEnXubioRequest,
    clientes: ClientesRepository = Depends(get_clientes),
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> dict:
    """Prueba: abre Xubio, va a «Importar desde Mis Comprobantes (ARCA)» de la
    operación y carga el archivo. No aprieta «Importar»."""
    try:
        datos = base64.b64decode(body.zip, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail={"mensaje": "No se pudo leer el .zip."}) from exc
    try:
        return importacion_srv.preparar(body.cliente, body.operacion, datos, clientes, creds)
    except importacion_srv.ErrorImportacion as exc:
        raise HTTPException(status_code=exc.status, detail={"mensaje": exc.mensaje}) from exc


# --- Ajustes -------------------------------------------------------------------


@router.get("/ajustes")
def ajustes(
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> dict:
    datos = creds.obtener()
    return {
        "carpeta_salida": str(config.OUTPUT_DIR),
        "pausa_segundos": config.PAUSA_ENTRE_CLIENTES_S,
        "dialogos_locales": config.DIALOGOS_LOCALES,
        "xubio": {
            "usuario": datos["user"],
            "tiene_clave": datos["tiene_pass"],
            "recordar": datos["recordar"],
            "del_servidor": False,
        },
    }


@router.delete(
    "/ajustes/xubio",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(exigir_acceso_clientes)],
)
def olvidar_xubio(
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> None:
    creds.guardar("", "", False)


# --- Ejecuciones ---------------------------------------------------------------


class Pasos(BaseModel):
    arca: bool = True
    xubio: bool = True
    auditoria: bool = True


class CredencialXubio(BaseModel):
    usuario: str = ""
    clave: str = ""
    recordar: bool = False


class PedidoEjecucion(BaseModel):
    clientes: list[str]
    desde: str
    hasta: str
    ventas: bool = True
    compras: bool = False
    pasos: Pasos = Field(default_factory=Pasos)
    carpeta: str = ""
    xubio: CredencialXubio = Field(default_factory=CredencialXubio)


class Respuesta(BaseModel):
    accion: str


def _http_error(exc: ejecuciones_srv.ErrorEjecucion) -> HTTPException:
    return HTTPException(status_code=exc.estado, detail={"mensaje": exc.mensaje})


@router.get("/ejecuciones")
def listar_ejecuciones() -> list[dict]:
    return ejecuciones_srv.listar()


@router.get("/ejecuciones/activa")
def ejecucion_activa() -> dict | None:
    ej = ejecuciones_srv.activa()
    return ej.resumen() if ej else None


@router.post("/ejecuciones", status_code=status.HTTP_201_CREATED)
def iniciar_ejecucion(
    pedido: PedidoEjecucion,
    usuario: Usuario = Depends(usuario_actual),
    clientes: ClientesRepository = Depends(get_clientes),
    creds: CredencialesXubioRepository = Depends(get_credenciales_xubio),
) -> dict:
    try:
        ej = ejecuciones_srv.iniciar(
            usuario.nombre, pedido.model_dump(), clientes, creds
        )
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.detalle()


@router.get("/ejecuciones/{ejecucion_id}")
def ver_ejecucion(ejecucion_id: str, log_desde: int = 0) -> dict:
    try:
        return ejecuciones_srv.obtener(ejecucion_id).detalle(log_desde)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc


@router.post("/ejecuciones/{ejecucion_id}/respuesta")
def responder_ejecucion(ejecucion_id: str, respuesta: Respuesta) -> dict:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.responder_pausa(ej, respuesta.accion)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.resumen()


@router.post("/ejecuciones/{ejecucion_id}/cancelar")
def cancelar_ejecucion(ejecucion_id: str) -> dict:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.cancelar(ej)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return ej.resumen()


@router.get("/ejecuciones/{ejecucion_id}/clientes/{indice}/archivos/{nombre}")
def descargar_archivo_ejecucion(
    ejecucion_id: str, indice: int, nombre: str
) -> FileResponse:
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        archivo = ejecuciones_srv.ruta_archivo(ej, indice, nombre)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return FileResponse(archivo, filename=archivo.name)


_AUX_CONTROL = re.compile(r"^AUX - Control de facturas (Ventas|Compras)\b.*\.xlsx?$", re.IGNORECASE)


def _aux_activos(nombres: list[str]) -> dict[str, str]:
    """Por operación, el AUX que vale: el corregido («Aplicar cambios») si lo hay."""
    activos: dict[str, str] = {}
    for nombre in nombres:
        m = _AUX_CONTROL.match(nombre)
        if not m:
            continue
        op = m.group(1).capitalize()
        if op not in activos or ejecuciones_srv.es_corregido(nombre):
            activos[op] = nombre
    return activos


@router.get("/ejecuciones/{ejecucion_id}/clientes/{indice}/faltantes")
def faltantes_ejecucion(ejecucion_id: str, indice: int) -> dict:
    """Lo que no cruzó, tal como está en cada AUX del cliente:
    - «sin_libro»: partidas de ARCA no encontradas en el Libro IVA (hoja «Faltantes en
      Libro IVA …»; es lo mismo que sube «Subir a Xubio»).
    - «sin_arca»: partidas del Libro IVA no encontradas en ARCA (bloque al pie de la
      primera hoja).
    Cada uno: {«Compras»: [filas], …}."""
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        if not 0 <= indice < len(ej.clientes):
            raise ejecuciones_srv.ErrorEjecucion("Ese cliente no es de esta ejecución.", 404)
        salida: dict[str, dict[str, list[dict]]] = {"sin_libro": {}, "sin_arca": {}}
        for op, nombre in _aux_activos([a["nombre"] for a in ej.clientes[indice].archivos]).items():
            ruta = ejecuciones_srv.ruta_archivo(ej, indice, nombre)
            # Un AUX (o un bloque) ilegible no corta lo demás.
            try:
                salida["sin_libro"][op] = filas_para_mostrar(ruta).get(op, [])
            except Exception:
                pass
            try:
                salida["sin_arca"][op] = libro_sin_arca(ruta, op)
            except Exception:
                pass
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return salida


class CambioElegido(BaseModel):
    """Un dato de ARCA para escribir en el Libro IVA (un par de «Error»)."""

    model_config = ConfigDict(populate_by_name=True)

    cliente: str = Field(min_length=1, max_length=200)
    operacion: str = Field(pattern="^(Ventas|Compras)$")
    campo: str = Field(pattern="^(letra|tipo|importe|cuit)$")
    # La CUIT y el comprobante como están en el Libro IVA (la fila a corregir).
    cuit: str = Field(min_length=1, max_length=20)
    pv: int = Field(ge=0, le=99999)
    numero: int = Field(ge=0, le=99999999)
    comprobante: str = Field(default="", max_length=40)
    valor_arca: str | float = Field(alias="valorArca")


class AplicarCambiosRequest(BaseModel):
    cambios: list[CambioElegido] = Field(min_length=1, max_length=2000)


@router.post("/ejecuciones/{ejecucion_id}/aplicar-cambios")
def aplicar_cambios_ejecucion(
    ejecucion_id: str,
    pedido: AplicarCambiosRequest,
    _usuario: Usuario = Depends(usuario_actual),
) -> dict:
    """Escribe los datos de ARCA elegidos en una copia del Libro IVA («… - corregido»),
    vuelve a cruzar con ARCA y devuelve el control con los hallazgos nuevos."""
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        return ejecuciones_srv.aplicar_cambios(ej, [c.model_dump() for c in pedido.cambios])
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc


def _solo_local() -> None:
    """Las ventanas de Windows abren en la PC del backend: en un servidor no."""
    if not config.DIALOGOS_LOCALES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"mensaje": "Guardar con ventana de Windows solo funciona con el backend en esta PC."},
        )


@router.get("/ejecuciones/{ejecucion_id}/clientes/{indice}/archivos.zip")
def descargar_zip_ejecucion(ejecucion_id: str, indice: int) -> Response:
    """Todos los archivos del cliente en un .zip con <Cliente>/<mmaaaa>/(Ventas|Compras)/."""
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        nombre, datos = ejecuciones_srv.zip_de_archivos(ej, indice)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc
    return Response(
        content=datos,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(nombre)}"},
    )


@router.post("/ejecuciones/{ejecucion_id}/clientes/{indice}/archivos/{nombre}/guardar")
def guardar_archivo_ejecucion(ejecucion_id: str, indice: int, nombre: str) -> dict:
    """Abre en la PC del servidor «Guardar como» y copia ahí ese archivo."""
    _solo_local()
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.ruta_archivo(ej, indice, nombre)  # que exista antes de abrir la ventana
        destino = dialogos.guardar_como(nombre)
        if destino is None:
            return {"cancelado": True}
        return ejecuciones_srv.guardar_archivo(ej, indice, nombre, destino)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc


@router.post("/ejecuciones/{ejecucion_id}/clientes/{indice}/exportar")
def exportar_archivos_ejecucion(ejecucion_id: str, indice: int) -> dict:
    """Abre en la PC del servidor la ventana para elegir carpeta. Con una carpeta
    elegida, los archivos van a `<elegida>/<mmaaaa>/`; si se cierra sin elegir,
    a `<Descargas>/<Cliente>/<mmaaaa>/`."""
    _solo_local()
    try:
        ej = ejecuciones_srv.obtener(ejecucion_id)
        ejecuciones_srv.archivos_del_cliente(ej, indice)  # que haya algo antes de abrir la ventana
        destino = dialogos.elegir_carpeta(
            "Elegí la carpeta del cliente (o cerrá para crearla en Descargas)"
        )
        return ejecuciones_srv.exportar_archivos(ej, indice, destino)
    except ejecuciones_srv.ErrorEjecucion as exc:
        raise _http_error(exc) from exc


# --- Identidad -----------------------------------------------------------------


class EntrarRequest(BaseModel):
    pase: str = ""


@router.get("/yo")
def yo(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    return usuario


@router.post("/sesion/entrar")
def sesion_entrar(body: EntrarRequest) -> dict:
    """Modo local: acepta el pase y devuelve una sesión de este backend.

    Cuando se conecte ContadorWeb, acá se valida la firma del pase.
    """
    if not (body.pase or "").strip():
        raise HTTPException(
            status_code=422, detail={"mensaje": "Falta el pase de ContadorWeb."}
        )
    usuario = usuario_por_defecto()
    return {
        "token": body.pase,
        "expira": int(time.time()) + 12 * 3600,
        "usuario": usuario.model_dump(),
    }
