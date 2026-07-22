"""
Simulador de datos - Proyecto RefugioVivo
Gemelo digital de diseno y optimizacion de refugios modulares para
animales rescatados, construidos con materiales reciclados.

Toma un lote, el clima local, un inventario de materiales reciclados y
las especies a alojar, y calcula un diseno: dimensiones por refugio,
una curva de temperatura interior de 24 horas, un presupuesto en pesos
colombianos y un cronograma de construccion.

Publica un JSON con el diseno cada pocos segundos al topico
solarpunk/refugio-vivo/estado, y escucha solarpunk/refugio-vivo/cmd
para recalcular en vivo cuando cambio materiales, especies o el lote.

Nota de honestidad: la curva termica es un modelo simplificado (no es
una simulacion CFD real), pero sirve para comparar disenos entre si.
"""

import time
import json
import math
import copy
import threading
from datetime import date

import paho.mqtt.client as mqtt

BROKER = "broker.emqx.io"
PORT = 1883
CLIENT_ID = "refugiovivo_simulador"

TOPIC_ESTADO = "solarpunk/refugio-vivo/estado"
TOPIC_CMD = "solarpunk/refugio-vivo/cmd"

INTERVALO_SEGUNDOS = 6

# --- Clima por ciudad (datos reales aproximados de IDEAM) ---
# temp_prom C, temp_amp C (amplitud dia/noche), lluvia mm/ano,
# rad_pico W/m2 al mediodia, altitud m
CLIMAS = {
    "bogota":       {"temp_prom": 14.0, "temp_amp": 6.0, "lluvia_mm": 1037, "rad_pico": 780, "altitud": 2640, "viento": "NE"},
    "medellin":     {"temp_prom": 22.0, "temp_amp": 5.0, "lluvia_mm": 1571, "rad_pico": 820, "altitud": 1495, "viento": "N"},
    "eje_cafetero": {"temp_prom": 20.0, "temp_amp": 5.5, "lluvia_mm": 2200, "rad_pico": 800, "altitud": 1400, "viento": "SW"},
}

# --- Materiales reciclados ---
# k: conductividad termica W/mK (mas alta = peor aislante)
# albedo: reflectancia del techo (mas alta = menos calor solar absorbido)
# costo: COP por unidad; res: resistencia estructural kN/m2; unidad de medida
MATERIALES = {
    "contenedor":  {"k": 50.0, "albedo": 0.40, "costo": 4000000, "res": 10.0, "unidad": "u"},
    "madera":      {"k": 0.12, "albedo": 0.50, "costo": 300000,  "res": 2.0,  "unidad": "m3"},
    "guadua":      {"k": 0.15, "albedo": 0.50, "costo": 35000,   "res": 2.5,  "unidad": "m"},
    "llantas":     {"k": 0.25, "albedo": 0.20, "costo": 12000,   "res": 3.0,  "unidad": "u"},
    "adobe":       {"k": 0.50, "albedo": 0.60, "costo": 1500,    "res": 4.0,  "unidad": "u"},
    "tela_sombra": {"k": 0.20, "albedo": 0.70, "costo": 55000,   "res": 0.2,  "unidad": "m2"},
}

# --- Especies: m3 por animal, rango de temperatura ideal C, ACH
#     (renovaciones de aire por hora), calor metabolico W por animal, color ---
ESPECIES = {
    "gallinas": {"m3": 0.15, "t_min": 18, "t_max": 28, "ach": 6, "calor": 10, "color": "#f1c40f"},
    "cerdos":   {"m3": 2.00, "t_min": 15, "t_max": 25, "ach": 8, "calor": 90, "color": "#e8a0a0"},
    "cabras":   {"m3": 1.50, "t_min": 10, "t_max": 27, "ach": 5, "calor": 60, "color": "#d9c7a3"},
    "ovejas":   {"m3": 1.20, "t_min": 8,  "t_max": 24, "ach": 5, "calor": 70, "color": "#e8e2d0"},
}

MANO_OBRA_M2 = 90000  # COP por m2 construido
CARGA_LLUVIA = 0.5    # kN/m2 de diseno por acumulacion de lluvia

# --- Configuracion inicial (el usuario la cambia por MQTT) ---
config = {
    "ciudad": "bogota",
    "lote": {"ancho": 40, "largo": 30, "pendiente": "plana", "orientacion": "N"},
    "inventario": {"contenedor": 2, "madera": 5, "llantas": 30, "guadua": 100, "adobe": 0, "tela_sombra": 50},
    "animales": {"gallinas": 20, "cerdos": 2, "cabras": 4},
    "presupuesto_max": 15000000,
}
config_lock = threading.Lock()


def temp_exterior(hora, clima):
    """Sinusoide simple: minimo de madrugada, maximo a media tarde."""
    return clima["temp_prom"] + clima["temp_amp"] * math.cos(2 * math.pi * (hora - 14) / 24)


def radiacion(hora, clima):
    """Cero de noche, pico al mediodia entre las 6 y las 18."""
    if 6 <= hora <= 18:
        return clima["rad_pico"] * math.sin(math.pi * (hora - 6) / 12)
    return 0.0


def curva_termica(largo, ancho, alto, especie, techo, clima):
    """
    Modelo termico simplificado de 24 horas. Para cada hora estima la
    temperatura interior a partir de la exterior, la ganancia solar segun
    el material del techo, el calor metabolico de los animales y el efecto
    aislante del material (amortigua la oscilacion alrededor del promedio).
    """
    esp = ESPECIES[especie]
    mat = MATERIALES[techo]
    vol = max(1.0, largo * ancho * alto)
    dens_calor = esp["calor"] * _n_animales(especie) / vol  # W/m3
    aisla = max(0.05, min(0.95, 1 - mat["k"] / 2.0))  # 1 = muy aislante
    curva = []
    for h in range(24):
        ext = temp_exterior(h, clima)
        rad_norm = radiacion(h, clima) / clima["rad_pico"]
        solar = rad_norm * (1 - mat["albedo"]) * 7.0          # hasta +7 C con techo oscuro
        metab = min(4.0, dens_calor * 0.10)                   # calor de los animales
        crudo = ext + solar + metab
        interior = clima["temp_prom"] + (crudo - clima["temp_prom"]) * (1 - 0.5 * aisla)
        curva.append(round(interior, 1))
    return curva


_animales_actuales = {}


def _n_animales(especie):
    return _animales_actuales.get(especie, 1)


def elegir_techo(inv):
    """Prefiero el mejor aislante disponible en el inventario reciclado."""
    for m in ("guadua", "madera", "tela_sombra", "contenedor"):
        if inv.get(m, 0) > 0:
            return m
    return "madera"  # si no hay reciclado, toca comprar


def bill_of_materials(largo, ancho, alto, especie, inv, techo):
    """
    Lista de materiales del refugio con heuristica simple. Descuenta del
    inventario reciclado lo que alcance y marca si algo tuvo que comprarse.
    Devuelve (dict_materiales, costo, descripcion, falto_inventario).
    """
    area_techo = largo * ancho
    area_muros = 2 * (largo + ancho) * alto
    bom = {}
    falto = False

    # Techo
    if techo == "guadua":
        bom["guadua"] = round(area_techo * 3)
    elif techo == "madera":
        bom["madera"] = round(area_techo * 0.04, 2)
    elif techo == "tela_sombra":
        bom["tela_sombra"] = round(area_techo)
    else:
        bom["contenedor"] = 1

    # Estructura y base
    if inv.get("contenedor", 0) > 0 and especie == "cerdos":
        bom["contenedor"] = bom.get("contenedor", 0) + 1
    else:
        bom["madera"] = round(bom.get("madera", 0) + area_muros * 0.02, 2)

    if inv.get("adobe", 0) > 0:
        bom["adobe"] = round(area_techo * 8)
    else:
        bom["llantas"] = round(area_techo * 1.5)

    # Costo y descuento de inventario
    costo = 0
    for mat, qty in bom.items():
        costo += qty * MATERIALES[mat]["costo"]
        disponible = inv.get(mat, 0)
        if qty > disponible:
            falto = True
            inv[mat] = 0
        else:
            inv[mat] = disponible - qty

    desc = ", ".join(_fmt_material(m, q) for m, q in bom.items())
    return bom, int(costo), desc, falto


def _fmt_material(mat, qty):
    unidad = MATERIALES[mat]["unidad"]
    return "{} {} {}".format(qty, unidad, mat)


def resistencia_ok(largo, ancho, techo):
    """Regla simple de esfuerzo: peso propio + lluvia vs resistencia del material."""
    mat = MATERIALES[techo]
    carga = 0.4 + CARGA_LLUVIA  # kN/m2, peso propio aproximado + lluvia
    return mat["res"] >= carga


def simular(cfg):
    """Calcula el diseno completo del santuario y devuelve el JSON de estado."""
    global _animales_actuales
    clima = CLIMAS.get(cfg["ciudad"], CLIMAS["bogota"])
    lote = cfg["lote"]
    area_disponible = lote["ancho"] * lote["largo"]
    inv = copy.deepcopy(cfg["inventario"])
    _animales_actuales = {e: n for e, n in cfg["animales"].items()}

    refugios = []
    cursor_x = -12.0
    area_usada = 0.0
    costo_total = 0
    hay_rojo = False
    hay_amarillo = False

    idx = 1
    for especie, n in cfg["animales"].items():
        if especie not in ESPECIES or n <= 0:
            continue
        esp = ESPECIES[especie]
        vol_nec = n * esp["m3"] * 1.2  # 20 por ciento de circulacion
        alto = 2.2
        area = max(1.0, vol_nec / alto)
        largo = round(math.sqrt(area * 1.5), 1)
        ancho = round(area / largo, 1)
        footprint = round(largo * ancho, 1)

        techo = elegir_techo(inv)
        bom, costo_mat, desc, falto = bill_of_materials(largo, ancho, alto, especie, inv, techo)
        curva = curva_termica(largo, ancho, alto, especie, techo, clima)
        t_min, t_max = min(curva), max(curva)
        horas_fuera = sum(1 for t in curva if t < esp["t_min"] or t > esp["t_max"])
        estruct = resistencia_ok(largo, ancho, techo)
        costo = costo_mat + int(footprint * MANO_OBRA_M2)

        if not estruct:
            alerta = "rojo"
            hay_rojo = True
        elif horas_fuera > 4 or falto:
            alerta = "amarillo"
            hay_amarillo = True
        else:
            alerta = "verde"

        refugios.append({
            "id": "refugio-{:02d}".format(idx),
            "especie": especie,
            "n_animales": n,
            "color": esp["color"],
            "largo": largo, "ancho": ancho, "alto": alto,
            "pos_x": round(cursor_x, 1), "pos_z": 0.0,
            "material_techo": techo,
            "materiales_desc": desc,
            "temp_min": t_min, "temp_max": t_max,
            "temp_curva": curva,
            "rango_ideal_min": esp["t_min"], "rango_ideal_max": esp["t_max"],
            "horas_fuera_rango": horas_fuera,
            "estructura_ok": estruct,
            "costo": costo,
            "alerta": alerta,
        })

        area_usada += footprint
        costo_total += costo
        cursor_x += largo + 3.0
        idx += 1

    cabe = area_usada <= area_disponible
    dentro_presupuesto = costo_total <= cfg["presupuesto_max"]
    if hay_rojo or not cabe:
        alerta_global = "rojo"
    elif hay_amarillo or not dentro_presupuesto:
        alerta_global = "amarillo"
    else:
        alerta_global = "verde"

    m2_total = round(area_usada, 1)
    semanas = max(3, round(3 + m2_total / 15))

    return {
        "diseno_viable": bool(cabe and dentro_presupuesto and not hay_rojo),
        "ciudad": cfg["ciudad"],
        "lote_ancho": lote["ancho"], "lote_largo": lote["largo"],
        "area_usada": m2_total, "area_disponible": area_disponible,
        "especies_alojadas": [r["especie"] for r in refugios],
        "refugios": refugios,
        "costo_total": costo_total,
        "presupuesto_max": cfg["presupuesto_max"],
        "semanas_construccion": semanas,
        "alerta_global": alerta_global,
        "fecha": date.today().isoformat(),
    }


def aplicar_cmd(payload):
    """Actualiza la configuracion en vivo a partir de un comando MQTT."""
    with config_lock:
        if "ciudad" in payload and payload["ciudad"] in CLIMAS:
            config["ciudad"] = payload["ciudad"]
        if isinstance(payload.get("animales"), dict):
            for esp, n in payload["animales"].items():
                config["animales"][esp] = max(0, int(n))
        if isinstance(payload.get("inventario"), dict):
            for mat, q in payload["inventario"].items():
                config["inventario"][mat] = max(0, int(q))
        if isinstance(payload.get("lote"), dict):
            config["lote"].update(payload["lote"])
        if "presupuesto_max" in payload:
            config["presupuesto_max"] = int(payload["presupuesto_max"])
        cmd = payload.get("cmd")
        if cmd == "mas_llantas":
            config["inventario"]["llantas"] = config["inventario"].get("llantas", 0) + 20
        elif cmd == "menos_madera":
            config["inventario"]["madera"] = max(0, config["inventario"].get("madera", 0) - 2)


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(TOPIC_CMD)
    print("Conectado a {}:{}. Publico en {} y escucho {}".format(BROKER, PORT, TOPIC_ESTADO, TOPIC_CMD))


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        aplicar_cmd(payload)
        print("Comando recibido: {}".format(payload))
    except Exception as e:
        print("Comando ignorado ({}): {}".format(e, msg.payload))


def main():
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            with config_lock:
                estado = simular(config)
            client.publish(TOPIC_ESTADO, json.dumps(estado))
            viable = "viable" if estado["diseno_viable"] else "NO viable"
            print("[{}] Diseno {} ({}) | {} refugios | {} m2 | ${:,} COP | {} semanas".format(
                estado["fecha"], viable, estado["alerta_global"], len(estado["refugios"]),
                estado["area_usada"], estado["costo_total"], estado["semanas_construccion"]))
            time.sleep(INTERVALO_SEGUNDOS)
    except KeyboardInterrupt:
        print("\nSimulador detenido por el usuario.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
