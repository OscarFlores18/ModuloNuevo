from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_BACKEND = Path(__file__).resolve().parent.parent.parent
# La carpeta del proyecto: ahí viven los bots (bot_arca.py, bot_xubio.py,
# auditor_fo.py) y los .json que comparte con la app de escritorio.
RAIZ_PROYECTO = RAIZ_BACKEND.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ_BACKEND / ".env", extra="ignore")

    # Cualquier puerto de localhost ya está permitido (regex en main.py);
    # acá van los orígenes que no son localhost.
    CORS_ORIGINS: list[str] = []

    # ------------------------------------------------------------------
    # Datos compartidos con la app de escritorio (main_control.py)
    # ------------------------------------------------------------------
    # Clientes con sus datos de ARCA y Xubio. Es el mismo archivo que usa la app
    # de escritorio (y DRLiqui): las claves que no se editan acá se conservan.
    CONFIG_CLIENTES: Path = RAIZ_PROYECTO / "config.json"
    # Credencial propia de Xubio cuando se tilda «Recordar».
    CREDENCIALES_XUBIO: Path = RAIZ_PROYECTO / "xubio_credenciales.json"
    # Cuenta de Xubio del servidor (opcional): si están las dos, no hace falta
    # escribirla en cada ejecución.
    XUBIO_USUARIO: str = ""
    XUBIO_CLAVE: str = ""

    # Carpeta de salida por defecto: <carpeta>/<Cliente>/<mmaaaa>/Control de Facturas
    CARPETA_SALIDA: Path = Path.home() / "Downloads"
    SUBCARPETA_CONTROL: str = "Control de Facturas"
    # Segundos que espera la pausa entre clientes antes de seguir sola.
    PAUSA_ENTRE_CLIENTES_S: int = 15

    # ------------------------------------------------------------------
    # Usuario (ver app/core/usuarios.py)
    # ------------------------------------------------------------------
    # Secreto compartido con ContadorWeb (el MISMO valor en los dos servicios,
    # mínimo 32 caracteres). Vacío = modo local (usuarios de prueba).
    CONTROL_FCT_MODULO_SECRETO: str = ""
    SESION_HORAS: float = 12
    USUARIO_POR_DEFECTO_ID: str = "local"
    USUARIO_POR_DEFECTO_NOMBRE: str = "Usuario local"
    USUARIO_POR_DEFECTO_NIVEL: str = "100"
    USUARIO_POR_DEFECTO_MODULOS: str = ""

    # Carpeta con el build del front (dist/<app>/browser). Si existe, el backend
    # sirve la app en el mismo dominio que la API.
    FRONT_DIST: Path | None = None


settings = Settings()
