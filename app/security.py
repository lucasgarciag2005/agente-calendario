"""Chequeo de autenticidad del remitente.

Un estudiante de Work and Travel es un blanco comodo para la estafa del falso
pago de alojamiento: esta en otro pais, espera mails de pago, los montos son
grandes y no conoce a la gente que se los manda. Este modulo no decide si un
mail es bueno o malo: lee lo que los servidores de Google ya verificaron al
recibirlo y avisa cuando un mail que habla de plata no pasa esa verificacion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Veredictos
OK = "ok"
SOSPECHOSO = "sospechoso"
# El mail no trae header de autenticacion. No es lo mismo que fallar: Gmail
# estampa ese header al recibir, del lado del servidor, asi que un atacante no
# puede sacarlo. Si falta es por un caso raro nuestro, no por un ataque, y
# alertar ahi seria ruido sin nada detras.
DESCONOCIDO = "desconocido"

# Palabras que indican que el mail habla de mover plata. Es a proposito amplia:
# una palabra de esta lista por si sola no dispara nada, solo importa cuando
# ademas falla la autenticacion.
PALABRAS_DINERO = [
    "pago", "pagar", "pagá", "paga ", "pague", "abonar", "abono",
    "transferencia", "transferir", "deposito", "depósito", "depositar",
    "factura", "cuota", "alquiler", "renta", "expensas", "arancel",
    "cbu", "cvu", "alias", "iban", "swift", "routing number",
    "tarjeta de credito", "tarjeta de crédito", "cuenta bancaria",
    "payment", "pay now", "pay your", "invoice", "billing", "amount due",
    "wire transfer", "bank account", "routing", "deposit", "rent",
    "balance due", "past due", "fee", "zelle", "venmo", "western union",
]


@dataclass
class Veredicto:
    estado: str
    spf: str
    dkim: str
    dmarc: str
    habla_de_dinero: bool

    @property
    def alerta(self) -> bool:
        """Solo alertamos cuando se juntan las dos cosas."""
        return self.estado == SOSPECHOSO and self.habla_de_dinero

    def resumen(self) -> str:
        return f"SPF {self.spf or '?'} / DKIM {self.dkim or '?'} / DMARC {self.dmarc or '?'}"


def _valor(auth_results: str, campo: str) -> str:
    m = re.search(rf"\b{campo}=(\w+)", auth_results or "", re.IGNORECASE)
    return m.group(1).lower() if m else ""


def habla_de_dinero(subject: str, body: str) -> bool:
    texto = f"{subject}\n{body}".lower()
    return any(p in texto for p in PALABRAS_DINERO)


def evaluar(auth_results: str, subject: str, body: str) -> Veredicto:
    spf = _valor(auth_results, "spf")
    dkim = _valor(auth_results, "dkim")
    dmarc = _valor(auth_results, "dmarc")

    if not (spf or dkim or dmarc):
        estado = DESCONOCIDO
    elif dmarc in ("pass", "bestguesspass") or dkim == "pass":
        # DMARC es el chequeo fuerte: si pasa, el dominio del remitente es
        # real. DKIM solo tambien alcanza, porque la firma viaja con el mensaje
        # y sobrevive a los reenvios, que es donde SPF falla sin que haya nada
        # raro detras.
        estado = OK
    else:
        estado = SOSPECHOSO

    return Veredicto(
        estado=estado,
        spf=spf,
        dkim=dkim,
        dmarc=dmarc,
        habla_de_dinero=habla_de_dinero(subject, body),
    )
