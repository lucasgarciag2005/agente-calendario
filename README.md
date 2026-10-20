# Agente Work and Travel

Un agente que te lee el mail, te avisa por Telegram lo que tenés que hacer y te
ordena el calendario. Pensado para el Work and Travel de diciembre, pero los
temas que vigila los configurás vos.

## Qué hace

- **Revisa el mail cada 15 minutos.** Solo mira los mails que pasan un prefiltro
  de palabras clave, y de esos decide con Claude si son del tema o no. Las
  promos, newsletters y notificaciones automáticas las descarta.
- **Detecta fechas límite.** Si un mail dice que tenés que hacer algo antes de
  una fecha, te manda un mensaje a Telegram con el resumen, la fecha, y el
  fragmento textual del mail de donde la sacó.
- **Agenda con tu confirmación.** Nada entra al calendario sin que toques
  ✅ Agendar. Si la fecha no era explícita en el mail, te lo marca.
- **Te recuerda.** 7, 3 y 1 día antes, y el día del vencimiento.
- **Ordena el calendario desde el chat.** Le escribís *"tengo clase de inglés
  los lunes y miércoles de 18 a 20 hasta el 20 de diciembre"* o *"del 5 al 20 de
  diciembre estoy de viaje"* y lo pasa a eventos, también con confirmación.

## Dos protecciones que no son obvias

**Remitentes privados.** Algunos mails traen datos que no tiene sentido
mandarle a un modelo: el alta de empleado de ADP, con legajo, dirección y datos
bancarios. Esos remitentes se listan en [`app/privacy.py`](app/privacy.py) y el
agente **solo te avisa que llegaron**, con el asunto y un link a Gmail. El
cuerpo no sale de tu máquina ni queda guardado en la base. Hay un modo
intermedio, `SUBJECT_ONLY`, que manda el asunto y nunca el cuerpo.

**Verificación del remitente.** Un estudiante de Work and Travel es un blanco
cómodo para la estafa del falso pago de alojamiento: está en otro país, espera
mails de pago, los montos son grandes. Por eso
[`app/security.py`](app/security.py) lee los resultados de SPF, DKIM y DMARC
que Google ya estampó al recibir el mail. Si un mail **habla de dinero y no
pasa esa verificación**, el agente te manda una alerta distinta, no lo analiza
y no agenda nada. Las dos condiciones tienen que darse juntas: un mail no
verificado que no hable de plata no te molesta, y uno de pago verificado sigue
su curso normal.

## Qué no puede hacer

El token de Google que usa tiene el permiso `gmail.readonly` y nada más. Con ese
permiso la API de Google **rechaza** cualquier intento de responder, reenviar,
archivar, marcar como leído, mover o borrar un mail. No es una regla escrita en
el código que se pueda saltear con un bug: es el token el que no puede.

Además, el agente arranca chequeando los permisos del token y se niega a correr
si alguien le amplió el acceso al mail por descuido
([`app/google_auth.py`](app/google_auth.py)).

En el calendario sí escribe, pero solo en calendarios propios que crea él
(`Agente - Work and Travel`, `Agente - Clases`, `Agente - Viajes`,
`Agente - General`). No toca ningún evento de tus otros calendarios.

## Cómo está armado

```
app/
  main.py            servidor FastAPI + las tareas programadas
  pipeline.py        el flujo: revisar mail -> proponer -> agendar -> recordar
  llm.py             las dos llamadas a Claude (clasificar mail, entender chat)
  gmail_client.py    lectura de Gmail (solo lectura, sin un metodo que escriba)
  calendar_client.py escritura en Google Calendar
  telegram.py        mandar y recibir mensajes
  handlers.py        que hace con cada mensaje o boton
  models.py          tablas de la base
  store.py           temas vigilados y estado chico
scripts/
  google_oauth.py    correr una vez para sacar el refresh token
  check_setup.py     chequea las cinco conexiones
tests/               tests que corren sin credenciales reales
```

El agente es un solo proceso: el servidor web atiende el webhook de Telegram y
un scheduler interno dispara la revisión del mail y los recordatorios.

---

# Setup

Son cuatro cosas: Google, Telegram, Anthropic, y el deploy. Calculá una hora.

## 0. Requisitos

Python 3.12 (ya está instalado en esta máquina, en `.venv`). Para reinstalar:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

Copiá el archivo de ejemplo y abrilo, lo vas completando en los pasos que siguen:

```bash
cp .env.example .env
```

## 1. Google (Gmail + Calendar)

1. Entrá a [console.cloud.google.com](https://console.cloud.google.com) y creá un
   proyecto nuevo.
2. **APIs y servicios → Biblioteca**: activá **Gmail API** y **Google Calendar API**.
3. **APIs y servicios → Google Auth Platform** (antes se llamaba "Pantalla de
   consentimiento de OAuth"; si dice *Google Auth platform not configured yet*,
   apretá **Get started**). Son cuatro secciones:
   - **Branding**: nombre de la app y tu mail de contacto.
   - **Audience**: tipo **Externo**. Más abajo, en **Test users**, agregá tu
     propia dirección de Gmail.
   - **Data access → Add or remove scopes**: buscá y tildá estos dos:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/calendar.app.created`

     La tabla **solo muestra permisos de APIs ya habilitadas**, así que si está
     vacía es porque te falta el paso 2. El segundo permiso es el chico: deja
     crear calendarios y manejar los eventos *solo de los que creó la app*, sin
     tocar los tuyos. Si llegara a dar problemas, cambiá
     `GOOGLE_CALENDAR_SCOPE` en el `.env` por
     `https://www.googleapis.com/auth/calendar` y volvé a correr el paso 6.
   - **Clients**: lo usamos en el paso 5.

4. **Importante — el token que caduca a los 7 días.** Esto es lo que decide si
   el agente sirve o no, así que leelo antes de seguir.

   Mientras la app esté en estado *Testing*, [Google vence el refresh token cada
   7 días](https://developers.google.com/identity/protocols/oauth2) y el agente
   deja de leer el mail hasta que vuelvas a autorizarlo a mano. Para un agente
   que tiene que correr solo, eso no alcanza.

   La salida es **Audience → Publish app**, que lo pasa a *En producción*. Como
   `gmail.readonly` es un permiso restringido, Google te va a avisar que hace
   falta verificación. Publicá igual: [las apps de uso personal con menos de 100
   usuarios quedan exentas](https://support.google.com/cloud/answer/7454865), a
   cambio de que la primera vez veas una pantalla de *"Google no verificó esta
   app"* donde tenés que entrar en **Configuración avanzada → Ir a (tu app)**. Es
   tu propia app leyendo tu propio mail.

   **Si Google no te deja publicar**, hay tres caminos, de mejor a peor:
   - Leer el mail por **IMAP con una contraseña de aplicación** en vez de la API
     de Gmail. No caduca nunca y no necesita verificación. El costo es que el
     "no puede escribir" pasa a ser una garantía del código en vez del token.
   - Dejarla en *Testing* y volver a correr `scripts/google_oauth.py` una vez
     por semana.
   - Pedir la verificación: para permisos restringidos piden una auditoría de
     seguridad (CASA). Para un proyecto personal no vale la pena.

5. **Google Auth Platform → Clients → Create client**, tipo **Aplicación de
   escritorio**. Descargá el JSON y guardalo en la raíz del proyecto como
   `client_secret.json`.
6. Corré el script de autorización. Te abre el navegador, das permiso, y al
   final imprime las tres variables:

   ```bash
   .venv/Scripts/python scripts/google_oauth.py
   ```

   Copiá `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` y `GOOGLE_REFRESH_TOKEN` al `.env`.

## 2. Telegram

1. En Telegram, buscá **@BotFather** y mandale `/newbot`. Te pide un nombre y un
   usuario (tiene que terminar en `bot`).
2. Te devuelve un token tipo `123456789:AAE...`. Va en `TELEGRAM_BOT_TOKEN`.
3. Generá un secreto para el webhook y ponelo en `TELEGRAM_WEBHOOK_SECRET`:

   ```bash
   .venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. `TELEGRAM_CHAT_ID` dejalo vacío: se completa solo cuando le mandes `/start`
   al bot.

## 3. Gemini (gratis)

Sacá una API key en [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
y ponela en `GEMINI_API_KEY`. No pide tarjeta.

`GEMINI_MODELS` es una **cadena de respaldo**, en orden de preferencia. Esto
importa más de lo que parece: el nivel gratuito da alrededor de **20 consultas
por día y por modelo** (no las 1500 que afirman varios blogs — lo verificamos
contra el error real de la API). Cuando un modelo se queda sin saldo del día,
el agente pasa solo al siguiente, que tiene su cuota intacta. Sin esa cadena el
agente se queda mudo hasta el día siguiente.

Con cinco modelos son unas 100 consultas diarias, y el agente usa una por mail
que pasa el prefiltro, así que sobra de lejos.

Los modelos `pro` no están en la cadena porque el nivel gratuito no les da
cuota: devuelven 429 siempre.

`scripts/check_setup.py` prueba la cadena de verdad y te dice cuál respondió y
cuántos quedan de respaldo.

> **Privacidad:** en el nivel gratuito, [los términos de la API de
> Gemini](https://ai.google.dev/gemini-api/terms) permiten que Google use el
> contenido enviado para mejorar sus productos y modelos. El agente le manda el
> texto de los mails que pasan el prefiltro. Si eso no te cierra, las opciones
> son pagar el nivel de pago de Gemini, que no tiene ese uso, o volver a un
> proveedor pago como Anthropic.

## 4. Probar todo en local

```bash
.venv/Scripts/python scripts/check_setup.py
```

Tiene que dar OK en las cinco líneas. Después levantá el servidor:

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Mandale `/start` al bot. Como todavía no hay webhook público, el bot no te va a
contestar, pero una vez deployado sí. Para probar la lectura del mail sin
webhook, dejá el servidor corriendo: a los 15 minutos revisa solo.

## 5. Deploy en Railway

El agente necesita estar prendido siempre y tener una URL pública para que
Telegram le mande tus mensajes.

1. Subí el proyecto a un repo de GitHub (el `.gitignore` ya excluye `.env`,
   `client_secret.json` y la base de datos; **no los subas**).
2. En [railway.com](https://railway.com): **New Project → Deploy from GitHub
   repo** y elegí el repo. Detecta Python solo y usa el `Procfile`.
3. En **Variables**, pegá todo lo que tenés en `.env`.
4. En **Settings → Volumes**, agregá un volumen montado en `/data`, y cambiá la
   variable:

   ```
   DATABASE_URL=sqlite:////data/agent.db
   ```

   Son cuatro barras: tres del esquema más la barra de la ruta absoluta. Sin el
   volumen, cada redeploy olvida qué mails ya vio y te vuelve a notificar todo.
5. En **Settings → Networking**, generá el dominio público. Copialo y ponelo en
   la variable `PUBLIC_URL` (por ejemplo `https://agente-wt.up.railway.app`).
6. Redeploy. Al arrancar, el agente registra el webhook de Telegram solo.
7. Mandale `/start` al bot y después `/estado`. Si las cinco líneas dan OK, está
   andando.

### Si el hosting se duerme

Los planes gratuitos suelen suspender el servicio por inactividad, y con el
proceso dormido no hay revisión de mails. Dos salidas:

- Un cron externo gratis ([cron-job.org](https://cron-job.org)) que pegue cada
  15 minutos a `https://tu-url/health`. Eso lo mantiene despierto.
- O que el cron externo dispare la revisión directamente, con un POST a
  `https://tu-url/admin/poll` y el header `X-Admin-Secret` igual a tu
  `TELEGRAM_WEBHOOK_SECRET`.

---

# Usarlo

## Comandos

| Comando | Qué hace |
|---|---|
| `/start` | Conecta este chat con el agente |
| `/agenda` | Qué tenés los próximos 7 días |
| `/pendientes` | Vencimientos sin hacer |
| `/revisar` | Revisa el mail ahora, sin esperar los 15 minutos |
| `/temas` | Los temas que vigila en el mail |
| `/estado` | Chequea que Gmail, Calendar, Claude y el bot estén conectados |

## Escribirle normal

Para agendar:

> del 5 al 20 de diciembre estoy de viaje en Miami

> tengo clase de inglés los lunes y miércoles de 18 a 20 hasta el 20 de diciembre

> el 3 de noviembre tengo la entrevista en el consulado a las 9:15

> acordate que tengo que pagar el seguro antes del 10

Te muestra lo que entendió y vos confirmás. Si le falta la fecha, te la pregunta
en vez de inventarla.

Para agregar un tema nuevo que vigile en el mail:

> de ahora en más seguí también los mails de la facultad sobre finales e inscripciones

## Qué recibís cuando encuentra algo

```
📌 work-and-travel
CIEE <noreply@ciee.org>

CIEE te pide pagar la tasa SEVIS antes de la fecha indicada.

Hay que: Pagar la tasa SEVIS
🗓 Vence domingo 1 nov 2026 (en 12 dias)
📎 "You must pay the SEVIS fee before November 1"

¿Lo agendo?
   [ ✅ Agendar ]  [ ❌ Ignorar ]
```

La línea del 📎 es el fragmento textual del mail de donde salió la fecha, para
que puedas verificarla de un vistazo. Si la fecha no estaba escrita explícita
sino deducida, te lo avisa en el mensaje.

---

# Mantenimiento

## Tests

Corren sin credenciales reales: usan stubs para Gmail, Calendar, Claude y Telegram.

```bash
.venv/Scripts/python tests/test_timezone.py
.venv/Scripts/python tests/test_flow.py
.venv/Scripts/python tests/test_server.py
```

`test_flow.py` recorre todo el camino: prefiltro de Gmail, el candado de
solo-lectura, mail → propuesta, que no procese dos veces el mismo mail, aprobar
y que se cree el evento, los recordatorios sin repetirse, y un evento semanal
pedido por chat.

## Ajustes que te pueden interesar

| Variable / lugar | Para qué |
|---|---|
| `POLL_INTERVAL_MINUTES` | Cada cuánto revisa el mail |
| `DAILY_DIGEST_HOUR` | A qué hora manda los recordatorios |
| `INITIAL_LOOKBACK_DAYS` | Cuántos días hacia atrás mira la primera vez |
| `ANTHROPIC_MODEL` | Calidad vs. costo |
| `REMINDER_OFFSETS` en `app/pipeline.py` | Los días de aviso previo (hoy 7, 3, 1, 0) |
| `DEADLINE_REMINDERS` en `app/calendar_client.py` | Las notificaciones propias de Google Calendar |
| Las palabras clave del tema | `/temas` te las muestra; se editan en `app/store.py` o agregando un tema por chat |

## Si algo no anda

- **El bot no contesta.** Fijate que `PUBLIC_URL` sea el dominio real y que el
  deploy esté despierto. Pegale a `https://tu-url/health`.
- **No llega ningún mail.** Mandá `/revisar` y mirá los logs del deploy: ahí
  queda escrita la consulta exacta que le hace a Gmail. Lo más común es que las
  palabras clave del tema no coincidan con cómo están escritos tus mails.
- **"invalid_grant" en los logs.** Se venció el refresh token: es el problema
  del paso 1.4. Volvé a correr `scripts/google_oauth.py` y actualizá
  `GOOGLE_REFRESH_TOKEN` en Railway.
- **Te notifica cosas que no te importan.** Afiná la descripción del tema, que
  es lo que lee Claude para decidir. Cuanto más concreta, menos falsos positivos.
