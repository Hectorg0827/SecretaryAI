"""
English / Spanish wording for everything Lola composes.

Only presentation lives here: no data access, no authorisation. Templates are
keyed; messages produced elsewhere (governance, drafting, memory) are
translated by exact English text with ``tr`` and fall back to English when a
translation is missing, so a new message can never break an answer.
"""
from __future__ import annotations

import re
from typing import Any

LANGS = ("en", "es")

T: dict[str, dict[str, str]] = {
    "capabilities": {
        "en": ("I can help with your customer accounts, sales, receivables, inventory, purchase orders and team "
               "information — within your permissions — and I can prepare emails, purchase orders and reports "
               "for approval. What would you like to look at?"),
        "es": ("Puedo ayudarte con tus cuentas de clientes, ventas, cuentas por cobrar, inventario, órdenes de "
               "compra e información del equipo —dentro de tus permisos— y puedo preparar correos, órdenes de "
               "compra y reportes para aprobación. ¿Qué te gustaría revisar?"),
    },
    "accounts_header": {"en": "Accounts ({n} in your scope):", "es": "Cuentas ({n} en tu alcance):"},
    "accounts_none": {"en": "No accounts in your scope matched.", "es": "Ninguna cuenta de tu alcance coincidió."},
    "balance_suffix": {"en": " — balance {money}", "es": " — saldo {money}"},
    "and_more": {"en": "• …and {n} more", "es": "• …y {n} más"},
    "sales": {"en": "Sales, last {days} days: {orders} orders, {money} ({scope} scope).",
              "es": "Ventas, últimos {days} días: {orders} pedidos, {money} (alcance: {scope})."},
    "top_accounts": {"en": "Top accounts: {items}", "es": "Principales cuentas: {items}"},
    "receivables": {
        "en": "Receivables: {open} open invoices totalling {open_money}; {overdue} overdue ({overdue_money}).",
        "es": ("Cuentas por cobrar: {open} facturas abiertas por {open_money}; {overdue} vencidas "
               "({overdue_money})."),
    },
    "inventory": {"en": "Inventory: {n} item(s); {low} at or below reorder point.",
                  "es": "Inventario: {n} artículo(s); {low} en o por debajo del punto de reorden."},
    "low_stock": {"en": "At or below reorder point: {items}", "es": "En o por debajo del punto de reorden: {items}"},
    "purchasing": {"en": "Purchasing: {n} open purchase order(s).",
                   "es": "Compras: {n} orden(es) de compra abiertas."},
    "team": {"en": "Team directory: {n} employee record(s).",
             "es": "Directorio del equipo: {n} registro(s) de empleados."},
    "english_detail": {"en": "{text}", "es": "{text} (detalle en inglés)"},
    "denied": {"en": "I can't share {label} information with your current permissions.",
               "es": "No puedo compartir información de {label} con tus permisos actuales."},
    "unavailable": {"en": "{Label} information is unavailable right now ({reason}).",
                    "es": "La información de {label} no está disponible en este momento."},
    "financial_hidden": {"en": "Financial details (balances, costs) are hidden for your role.",
                         "es": "Los detalles financieros (saldos, costos) están ocultos para tu rol."},
    "not_found": {"en": "I couldn't find “{name}” among the accounts you can access.",
                  "es": "No encontré “{name}” entre las cuentas a las que tienes acceso."},
    "memory_unavailable": {"en": "My memory is unavailable right now.",
                           "es": "Mi memoria no está disponible en este momento."},
    "remembered": {"en": "Got it — I'll remember that{private}.", "es": "Entendido — lo recordaré{private}."},
    "private": {"en": " (kept private to you)", "es": " (solo para ti)"},
    "not_saved": {"en": "I didn't save that: {reason}.", "es": "No lo guardé: {reason}."},
    "save_unavailable": {"en": "I couldn't save that right now — my memory is unavailable.",
                         "es": "No pude guardarlo ahora — mi memoria no está disponible."},
    "recall_header": {"en": "Here's what I remember:", "es": "Esto es lo que recuerdo:"},
    "recall_empty": {"en": "I don't have anything saved yet.", "es": "Todavía no tengo nada guardado."},
    "note": {"en": "Note: {text}", "es": "Nota: {text}"},
    "stale": {"en": "Some of this information comes from a stale source and may be out of date.",
              "es": "Parte de esta información proviene de una fuente desactualizada y puede no estar al día."},
    "balance_mismatch": {
        "en": ("The customer balance in QuickBooks and the open invoices Lola can see (last 90 days) differ by "
               "{money}; older invoices or unapplied payments may explain it."),
        "es": ("El saldo del cliente en QuickBooks y las facturas abiertas que Lola puede ver (últimos 90 días) "
               "difieren en {money}; facturas más antiguas o pagos sin aplicar podrían explicarlo."),
    },
}

T.update({
    "file_table": {"en": "File “{file}”: {rows} row(s); columns: {cols}.",
                   "es": "Archivo “{file}”: {rows} fila(s); columnas: {cols}."},
    "file_text": {"en": "File “{file}”: {rows} line(s) of text. It begins: “{start}”",
                  "es": "Archivo “{file}”: {rows} línea(s) de texto. Empieza así: “{start}”"},
    "file_truncated": {"en": "Only the first {n} rows were read.", "es": "Solo se leyeron las primeras {n} filas."},
    "file_totals": {"en": "Totals: {items}", "es": "Totales: {items}"},
    "inv_check": {"en": "Checked against inventory: {matched} matched, {unknown} not found in inventory.",
                  "es": "Comparado con el inventario: {matched} coinciden, {unknown} no están en el inventario."},
    "inv_short": {"en": "Not enough stock for: {items}", "es": "No hay suficiente existencia de: {items}"},
    "inv_denied": {"en": "I couldn't check it against inventory: {reason}",
                   "es": "No pude compararlo con el inventario: {reason}"},
    "cust_check": {"en": "Customers in the file that you can access: {items}",
                   "es": "Clientes del archivo a los que tienes acceso: {items}"},
    "cust_hidden": {"en": "{n} name(s) in the file aren't among the accounts you can access.",
                    "es": "{n} nombre(s) del archivo no están entre las cuentas a las que tienes acceso."},
    "file_instructions": {
        "en": "This file contains text that looks like instructions. I treated it only as data and did not act on it.",
        "es": "Este archivo contiene texto que parece instrucciones. Lo traté solo como datos y no actué sobre él.",
    },
    "file_unsupported": {
        "en": "I couldn't read “{file}”: for now I can read CSV, TSV, TXT, Markdown and JSON files.",
        "es": "No pude leer “{file}”: por ahora puedo leer archivos CSV, TSV, TXT, Markdown y JSON.",
    },
    "file_too_large": {"en": "I couldn't read “{file}”: it is larger than 200 KB of text.",
                       "es": "No pude leer “{file}”: tiene más de 200 KB de texto."},
})

DEPARTMENT_LABEL = {
    "en": {"sales": "sales", "finance": "financial", "operations": "inventory", "purchasing": "purchasing",
           "hr": "HR and payroll", "executive": "leadership", "compliance": "compliance", "general": "general"},
    "es": {"sales": "ventas", "finance": "finanzas", "operations": "inventario", "purchasing": "compras",
           "hr": "recursos humanos y nómina", "executive": "dirección", "compliance": "cumplimiento",
           "general": "general"},
}

SCOPE_LABEL = {"es": {"company": "empresa", "department": "departamento", "team": "equipo", "own": "propio",
                      "delegated": "delegado", "none": "ninguno"}}

# Messages produced by other modules, translated by exact English text.
MESSAGES_ES: dict[str, str] = {
    "Prepared and waiting for approval. Nothing has been sent.":
        "Preparado y en espera de aprobación. No se ha enviado nada.",
    "Done and verified.": "Hecho y verificado.",
    "This could not be queued. Nothing was sent.": "No se pudo poner en cola. No se envió nada.",
    "The outcome could not be confirmed and needs a human check.":
        "No se pudo confirmar el resultado y necesita revisión humana.",
    "This request was already received": "Esta solicitud ya se había recibido",
    "This action is not permitted for Lola": "Esta acción no está permitida para Lola",
    "Your role cannot request this action": "Tu rol no puede solicitar esta acción",
    "You do not have access to the data this action needs":
        "No tienes acceso a los datos que necesita esta acción",
    "Tell me which of your accounts this is for": "Dime para cuál de tus cuentas es",
    "I can't find that account among the accounts you can access":
        "No encuentro esa cuenta entre las cuentas a las que tienes acceso",
    "Action receipts are unavailable; nothing was queued":
        "Los comprobantes de acciones no están disponibles; no se puso nada en cola",
    "Your company's action policy could not be checked, so nothing was queued":
        "No se pudo verificar la política de acciones de tu empresa, así que no se puso nada en cola",
    "Blocked by your company's action policy": "Bloqueado por la política de acciones de tu empresa",
    "The action details were not valid": "Los detalles de la acción no eran válidos",
    "There is no valid email address on file for this account":
        "No hay un correo electrónico válido registrado para esta cuenta",
    "Customer records are unavailable right now": "Los registros de clientes no están disponibles ahora",
    "QuickBooks is not connected, so I can't prepare a purchase order":
        "QuickBooks no está conectado, así que no puedo preparar una orden de compra",
    "Inventory is unavailable right now": "El inventario no está disponible ahora",
    "Tell me exactly one product (by name or SKU) for this purchase order":
        "Indícame exactamente un producto (por nombre o SKU) para esta orden de compra",
    "I couldn't match a product from your inventory": "No pude encontrar el producto en tu inventario",
    "Tell me how many units to order": "Dime cuántas unidades pedir",
    "This item has no readable purchase cost in QuickBooks":
        "Este artículo no tiene un costo de compra legible en QuickBooks",
    "Nothing to remember": "No hay nada que recordar",
    "Memory is too long": "La nota es demasiado larga",
    "Lola does not store passwords, keys or other secrets":
        "Lola no guarda contraseñas, claves ni otros secretos",
    "Only leadership can save company-wide memory": "Solo la dirección puede guardar notas para toda la empresa",
    "You cannot attach memory to that account": "No puedes asociar notas a esa cuenta",
    "Lola does not store HR or personal information for your role":
        "Lola no guarda información de recursos humanos o personal para tu rol",
}

_COMPOSED_ES = [
    (re.compile(r"^I couldn't prepare this: (?P<x>.+)\. Nothing was queued\.$"),
     "No pude prepararlo: {x}. No se puso nada en cola."),
]


def t(lang: str, key: str, **kw: Any) -> str:
    entry = T[key]
    text = entry.get(lang) or entry["en"]
    if "Label" in kw or "{Label}" in text:
        kw.setdefault("Label", str(kw.get("label", "")).capitalize())
    return text.format(**kw)


def tr(lang: str, text: str) -> str:
    """Translate a message produced elsewhere; unknown messages stay in English."""
    if lang != "es" or not text:
        return text
    if text in MESSAGES_ES:
        return MESSAGES_ES[text]
    for pattern, template in _COMPOSED_ES:
        m = pattern.match(text)
        if m:
            return template.format(**{k: tr(lang, v) for k, v in m.groupdict().items()})
    return text


def department(lang: str, dept: str) -> str:
    return DEPARTMENT_LABEL.get(lang, DEPARTMENT_LABEL["en"]).get(dept, dept)


def scope(lang: str, value: str) -> str:
    return SCOPE_LABEL.get(lang, {}).get(value, value)


def money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"
