"""Revisa si hay una hora disponible antes de una fecha límite y avisa por correo.

Todo lo personal llega por variables de entorno (secretos de GitHub).
Los registros solo dicen "hay hora" o "no hay": nada de nombres, RUT ni fechas.
"""

import hashlib
import os
import re
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import expect, sync_playwright

URL = "https://www.davila.cl/reserva-de-horas/presencial-profesional"
MESES = {m: i for i, m in enumerate(
    ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"], 1)}
ESTADO = Path(".estado/ultimo_aviso")


def env(nombre):
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        sys.exit(f"Falta la variable {nombre}")
    return valor


def buscar_proxima_hora(rut, profesional, sucursal):
    """Devuelve (fecha, hora, texto) de la próxima hora, o None si no hay."""
    with sync_playwright() as p:
        # En la prueba del Mac se ve la ventana; en GitHub corre oculto.
        browser = p.chromium.launch(headless=not os.environ.get("PRUEBA"))
        page = browser.new_page(locale="es-CL")
        page.goto(URL, wait_until="domcontentloaded")

        try:  # la página sigue cargando contadores que fallan; no esperar por ellos para siempre
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # Mismo orden que la primera prueba, que sí habilitó el botón del doctor.
        page.get_by_placeholder("del paciente").fill(rut)
        page.get_by_role("radio", name="Profesional").click()

        # El botón del doctor se habilita cuando la página acepta el RUT.
        expect(page.locator("#ddl-especialistas")).to_be_enabled(timeout=60000)

        # Desplegable de profesional: se abre, se filtra y se elige.
        # El filtro solo reacciona a teclas, por eso se escribe letra por letra.
        page.locator("#ddl-especialistas").click()
        page.get_by_placeholder("filtrar profesionales").press_sequentially(profesional, delay=60)
        page.locator("a:visible", has_text=profesional).first.click()

        # Desplegable de sucursal: las opciones son etiquetas, no enlaces.
        # Es de selección múltiple: un clic de más desmarca. Solo se toca si no está elegida.
        boton_suc = page.locator("app-ngxb-ddl-multi-sucursales-ambulatorias button").first
        if sucursal.lower() not in boton_suc.inner_text().lower():
            boton_suc.click()
            page.locator("label:visible", has_text=sucursal).first.click()
        # La lista de sucursales no se cierra con Escape: se cierra con su mismo botón.
        if page.locator("label:visible", has_text=sucursal).count():
            boton_suc.click()

        # A veces la página ya muestra el resultado sin presionar «Buscar».
        proxima = page.get_by_text("Próxima hora en").first
        try:
            proxima.wait_for(timeout=5000)
        except PWTimeout:
            # «Buscar» del formulario (hay otra lupa con el mismo nombre al pie).
            # Se activa con el teclado para que nada encima lo tape.
            buscar = page.locator("button.btn-buscar")
            expect(buscar).to_be_enabled(timeout=30000)
            buscar.focus()
            page.keyboard.press("Enter")
        try:
            page.get_by_text("Próxima hora en").first.wait_for(timeout=30000)
        except PWTimeout:
            texto = page.locator("main").inner_text()
            browser.close()
            if "Próxima hora" not in texto:
                return None
        texto = page.locator("main").inner_text()
        browser.close()

    # Solo la tarjeta del profesional elegido (las demás vienen después).
    propio = texto.split("Si lo quieres")[0]
    apellido = profesional.split()[0]
    if apellido.lower() not in propio.lower():
        raise RuntimeError("La página no mostró al profesional elegido")

    m = re.search(
        r"Próxima hora en:.*?(\d{1,2})\s+(" + "|".join(MESES) + r")\b.*?(\d{1,2}:\d{2})",
        propio, re.S | re.I)
    if not m:
        return None
    dia, mes, hora = int(m.group(1)), MESES[m.group(2).lower()], m.group(3)
    hoy = date.today()
    anio = hoy.year if (mes, dia) >= (hoy.month, hoy.day) else hoy.year + 1
    return date(anio, mes, dia), hora


def enviar_correo(asunto, cuerpo):
    usuario, clave = env("SMTP_USER"), env("SMTP_PASS")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, env("MAIL_TO")
    msg.set_content(cuerpo)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(usuario, clave)
        s.send_message(msg)


def main():
    rut, profesional, sucursal = env("RUT"), env("PROFESIONAL"), env("SUCURSAL")
    limite = date.fromisoformat(env("FECHA_LIMITE"))

    resultado = buscar_proxima_hora(rut, profesional, sucursal)
    if os.environ.get("PRUEBA"):  # solo para probar en el Mac, nunca en GitHub
        print("Resultado:", resultado)
        return
    if not resultado or resultado[0] > limite:
        print("No hay hora antes del límite")
        return

    fecha, hora = resultado
    huella = hashlib.sha256(f"{fecha} {hora}".encode()).hexdigest()
    if ESTADO.exists() and ESTADO.read_text() == huella:
        print("Hay hora, ya avisada antes")
        return

    enviar_correo(
        f"Hay hora disponible: {fecha:%d-%m-%Y} {hora}",
        f"Apareció una hora el {fecha:%d-%m-%Y} a las {hora} "
        f"({profesional}, {sucursal}).\n\nReserva en {URL}",
    )
    ESTADO.parent.mkdir(exist_ok=True)
    ESTADO.write_text(huella)
    print("Hay hora: aviso enviado")


if __name__ == "__main__":
    main()
