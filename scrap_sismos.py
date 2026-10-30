import requests
import boto3
import uuid
from datetime import datetime, timedelta, timezone

# La página https://ultimosismo.igp.gob.pe/productos/reportes-sismicos es una
# app en JavaScript: la tabla NO viene en el HTML, el navegador la arma con los
# datos que pide a este endpoint (se ve en F12 > Network > Fetch/XHR).
# Por eso hacemos el scraping directamente sobre esa fuente de datos.
API_URL = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{anio}"
TABLE_NAME = "TablaSismosIGP"
HEADERS = {"User-Agent": "Mozilla/5.0"}
N_SISMOS = 10


def obtener_sismos(anio):
    url = API_URL.format(anio=anio)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.exceptions.SSLError:
        # El certificado del IGP a veces falla la verificación; reintentamos sin verificar
        resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
    resp.raise_for_status()
    return resp.json()


def clave_orden(sismo):
    # fecha_local puede venir como "2026-10-29" o "2026-10-29T00:00:00.000Z"
    fecha = str(sismo.get("fecha_local", ""))[:10]
    hora = str(sismo.get("hora_local", ""))[:8]
    return fecha + " " + hora


def lambda_handler(event, context):
    # Año actual en hora de Perú (UTC-5)
    anio = datetime.now(timezone(timedelta(hours=-5))).year

    try:
        sismos = obtener_sismos(anio)
        # A inicios de año puede haber menos de 10: completamos con el año anterior
        if len(sismos) < N_SISMOS:
            sismos += obtener_sismos(anio - 1)
    except Exception as e:
        return {
            'statusCode': 502,
            'body': 'Error al acceder a la web del IGP: ' + str(e)
        }

    if not sismos:
        return {
            'statusCode': 404,
            'body': 'No se encontraron sismos en la web del IGP'
        }

    # Ordenar del más reciente al más antiguo y quedarnos con los 10 últimos
    sismos.sort(key=clave_orden, reverse=True)
    ultimos = sismos[:N_SISMOS]

    # Armar las filas (DynamoDB no acepta float, así que guardamos todo como texto)
    rows = []
    for i, s in enumerate(ultimos, start=1):
        row = {k: str(v) for k, v in s.items() if v is not None and str(v) != ""}
        row['#'] = i
        row['id'] = str(uuid.uuid4())
        rows.append(row)

    # Guardar en DynamoDB
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(TABLE_NAME)

    # Eliminar los elementos anteriores para dejar solo los 10 últimos
    scan = table.scan()
    with table.batch_writer() as batch:
        for each in scan['Items']:
            batch.delete_item(Key={'id': each['id']})

    # Insertar los nuevos
    with table.batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=row)

    return {
        'statusCode': 200,
        'body': rows
    }
