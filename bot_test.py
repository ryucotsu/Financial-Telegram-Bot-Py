import os
import logging
import datetime  # Para la fecha
import re  # <-- AGREGAR ESTO
from typing import Set  # <-- AGREGAR ESTO
import gspread  # Para Google Sheets
from oauth2client.service_account import ServiceAccountCredentials  # Para Google Sheets
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# --- ¡Pega tu Token aquí! ---
TU_TOKEN_DE_BOTFATHER = os.getenv("TU_TOKEN_DE_BOTFATHER")

# --- 1. TU LISTA BLANCA (WHITELIST) ---
# Reemplaza estos números con los IDs reales que obtuviste.
# Ejemplo: [123456789, 987654321]
USUARIOS_AUTORIZADOS = list(map(int, os.getenv("USUARIOS_AUTORIZADOS", "").split(",")))
# ---------------------------------------

# --- 3. CONFIGURACIÓN DE GOOGLE SHEETS ---
NOMBRE_DE_TU_ARCHIVO = os.getenv("NOMBRE_DE_TU_ARCHIVO")
PESTANA_DE_GASTOS = os.getenv("PESTANA_DE_GASTOS")
PESTANA_DE_CATEGORIAS = os.getenv("PESTANA_DE_CATEGORIAS")
JSON_CREDENCIALES = os.getenv("JSON_CREDENCIALES")

# --- 4. VARIABLES GLOBALES ---
# Usamos un "set" para búsquedas súper rápidas y en minúsculas
CATEGORIAS_VALIDAS: Set[str] = set() # <-- NUEVO: Se llenará al iniciar
sheet_gastos = None # <-- NUEVO: Hacemos la hoja de gastos global
# ------------------------------------

# Configura el logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - ID: %(user_id)s (%(user_name)s) - Mensaje: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_gastos.log"),  # <-- GUARDAR EN ARCHIVO
        logging.StreamHandler()  # <-- MOSTRAR EN CONSOLA
    ]
)
logger = logging.getLogger(__name__)

# <-- NUEVO: Función para cargar categorías al inicio -->
def cargar_configuracion_inicial():
    """Conecta con GSheets y carga las categorías en la memoria."""
    global sheet_gastos, CATEGORIAS_VALIDAS
    try:
        scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_CREDENCIALES, scope)
        client = gspread.authorize(creds)
        
        # 1. Abrir el libro de trabajo
        workbook = client.open(NOMBRE_DE_TU_ARCHIVO)
        
        # 2. Seleccionar la pestaña de Gastos
        sheet_gastos = workbook.worksheet(PESTANA_DE_GASTOS)
        
        # 3. Seleccionar la pestaña de Categorías y leerlas
        sheet_categorias = workbook.worksheet(PESTANA_DE_CATEGORIAS)
        
        # Obtenemos todos los valores de la Columna A, excepto el encabezado (fila 1)
        lista_de_la_hoja = sheet_categorias.col_values(1)[1:] 
        
        # Limpiamos (quitamos vacíos) y convertimos a minúsculas
        CATEGORIAS_VALIDAS = {cat.lower() for cat in lista_de_la_hoja if cat} 
        
        if not CATEGORIAS_VALIDAS:
            logger.warning("No se cargó ninguna categoría de la hoja 'Categorias'.")
        else:
            logger.info(f"Categorías cargadas exitosamente: {CATEGORIAS_VALIDAS}")
            
        print("✅ Conexión con Google Sheets y categorías cargadas.")
        
    except Exception as e:
        print(f"❌ ERROR FATAL al cargar configuración: {e}")
        exit()
# ---------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde /start SÓLO a usuarios autorizados."""
    user = update.effective_user
    user_log_data = {'user_id': user.id, 'user_name': user.first_name}

    if user.id not in USUARIOS_AUTORIZADOS:
        logger.warning(f"Acceso DENEGADO para /start", extra=user_log_data)
        return
    
    logger.info("Usuario autorizado presionó /start", extra=user_log_data)
    await update.message.reply_html(
        f"¡Hola {user.first_name}!\n\n"
        "Estoy listo para registrar tus gastos. Tienes 2 opciones:\n\n"
        "<b>1. Formato estándar:</b>\n"
        "<code>gasto [monto] [categoria]</code>\n\n"
        "<b>2. Atajo (shortcut):</b>\n"
        "<code>[categoria] [monto]</code>\n\n"
        "Usa /categorias para ver la lista de categorías válidas."
    )

# <-- NUEVO: Comando para listar categorías -->
async def comando_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la lista de categorías válidas cargadas."""
    user = update.effective_user
    user_log_data = {'user_id': user.id, 'user_name': user.first_name}

    if user.id not in USUARIOS_AUTORIZADOS:
        logger.warning(f"Acceso DENEGADO para /categorias", extra=user_log_data)
        return
        
    if not CATEGORIAS_VALIDAS:
        await update.message.reply_text("Aún no se han configurado categorías.")
        return
        
    # Formateamos la lista para mostrarla bonita (con mayúscula inicial)
    lista_formateada = [cat.capitalize() for cat in sorted(CATEGORIAS_VALIDAS)]
    
    respuesta = "<b>Estas son tus categorías válidas:</b>\n\n"
    respuesta += "\n".join(lista_formateada)
    
    await update.message.reply_html(respuesta)
# -------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parsea el gasto (con descripción opcional) y lo escribe en Google Sheets."""
    user = update.effective_user
    text_original = update.message.text # Guardamos el original para logs
    user_log_data = {'user_id': user.id, 'user_name': user.first_name}

    if user.id not in USUARIOS_AUTORIZADOS:
        logger.warning(f"Acceso DENEGADO para mensaje: '{text_original}'", extra=user_log_data)
        return

    # --- 1. EXTRACCIÓN DE DESCRIPCIÓN (¡NUEVO!) ---
    text = update.message.text
    descripcion = "" # Por defecto vacía
    
    # Usamos regex para buscar texto entre comillas simples (ej. 'comida del perro')
    match_desc = re.search(r"'(.*?)'", text)
    if match_desc:
        descripcion = match_desc.group(1).strip() # Captura el texto
        # Quitamos la descripción del texto principal para no confundir al parser
        text = text[:match_desc.start()] + text[match_desc.end():]
    
    text_lower = text.lower().strip() # .strip() para quitar espacios extra
    # -------------------------------------------

    # --- 2. LÓGICA DE ATAJOS (Shortcuts) ---
    # (Esta parte funciona igual que antes, pero ahora sobre el texto "limpio")
    parts = text.split(maxsplit=1)
    first_word = parts[0].lower()
    
    if first_word in CATEGORIAS_VALIDAS:
        if len(parts) == 2:
            # Lo "traducimos" al formato estándar: "gasto [monto] [categoria]"
            text = f"gasto {parts[1]} {first_word}"
            text_lower = text.lower()
        else:
            await update.message.reply_html(
                f"Formato de atajo incorrecto para <b>{first_word.capitalize()}</b>. 😕\n"
                "Intenta con: <code>[categoria] [monto]</code>"
            )
            return
    # -----------------------------------

    # --- 3. LÓGICA DE REGISTRO (Principal) ---
    if text_lower.startswith("gasto "):
        try:
            parts_gasto = text.split()
            
            if len(parts_gasto) < 3:
                await update.message.reply_text("Formato incorrecto. 😕\nIntenta con: gasto [monto] [categoria]")
                return

            monto_str = parts_gasto[1]
            categoria = " ".join(parts_gasto[2:])
            monto = float(monto_str)

            # Escribir en la hoja
            try:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                user_name = update.effective_user.first_name
                
                # --- ¡FILA ACTUALIZADA! ---
                # Asume que la Columna E es para la Descripción
                row = [now, monto, categoria.capitalize(), user_name, descripcion]
                
                sheet_gastos.append_row(row)
                
                logger.info(f"REGISTRO EXITOSO EN SHEETS: {row}", extra=user_log_data)
                
                # --- ¡RESPUESTA ACTUALIZADA! ---
                respuesta = (
                    f"✅ ¡Registrado en '{PESTANA_DE_GASTOS}'!\n"
                    f"Monto: ${monto:,.2f}\n"
                    f"Categoría: {categoria.capitalize()}"
                )
                if descripcion: # Añadir solo si hay descripción
                    respuesta += f"\nDescripción: {descripcion}"
                
                await update.message.reply_text(respuesta)
            
            except Exception as e:
                logger.error(f"Error al escribir en Google Sheets: {e}", extra=user_log_data)
                await update.message.reply_text("¡Ups! Error al guardar en Google Sheets.")

        except ValueError:
            logger.warning(f"Error de formato (ValueError): '{text_original}'", extra=user_log_data)
            await update.message.reply_text(f"El monto '{monto_str}' no es un número válido. 😕")
        except Exception as e:
            logger.error(f"Error inesperado procesando: '{text_original}'. Error: {e}", extra=user_log_data)
            await update.message.reply_text("¡Ups! Ocurrió un error inesperado.")
    
    else:
        logger.info(f"Mensaje no reconocido: '{text_original}'", extra=user_log_data)
        await update.message.reply_html(
            "No entendí ese comando. 🤷\nUsa /start para ver las opciones."
        )
        
def main() -> None:
    """Valida, carga configuración e inicia el bot."""
    
    if "TU_TOKEN_REAL_AQUI" in TU_TOKEN_DE_BOTFATHER or 123456789 in USUARIOS_AUTORIZADOS:
        print("¡ERROR! Reemplaza 'TU_TOKEN_DE_BOTFATHER' y 'USUARIOS_AUTORIZADOS' con tus datos reales.")
        return
    
    # --- CARGA INICIAL ---
    # Conecta a Google y lee las categorías ANTES de encender el bot
    cargar_configuracion_inicial() 
    # ---------------------

    application = Application.builder().token(TU_TOKEN_DE_BOTFATHER).build()

    # Registrar todos los comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("categorias", comando_categorias)) # <-- NUEVO
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 El bot de gastos (con categorías dinámicas) está iniciando...")
    print("Presiona Ctrl+C para detenerlo.")
    application.run_polling()

if __name__ == "__main__":
    main()