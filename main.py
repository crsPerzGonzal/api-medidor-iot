from contextlib import asynccontextmanager
from datetime import datetime
from os import environ
from typing import AsyncIterator

import asyncpg
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field


class LecturaEnergia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_dispositivo: int = Field(..., gt=0)
    voltaje_v: float
    corriente_a: float
    potencia_w: float


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    database_url = environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("La variable de entorno DATABASE_URL es obligatoria")

    application.state.pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )
    try:
        yield
    finally:
        await application.state.pool.close()


app = FastAPI(
    title="API de Energia",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/api/lecturas", status_code=status.HTTP_201_CREATED)
async def crear_lectura(lectura: LecturaEnergia, request: Request) -> dict[str, str]:
    # Ajustado a 'fecha' para alinearse estrictamente con la Hypertable de la base de datos
    fecha = datetime.now().astimezone()
    query = """
        INSERT INTO lecturas_energia (
            fecha,
            id_dispositivo,
            voltaje_v,
            corriente_a,
            potencia_w
        )
        VALUES ($1, $2, $3, $4, $5)
    """

    try:
        async with request.app.state.pool.acquire() as connection:
            await connection.execute(
                query,
                fecha,
                lectura.id_dispositivo,
                lectura.voltaje_v,
                lectura.corriente_a,
                lectura.potencia_w,
            )
    except asyncpg.PostgresError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible guardar la lectura",
        ) from error

    return {"mensaje": "Lectura registrada correctamente"}


@app.get("/api/analitica/promedio-hora/{id_dispositivo}")
async def promedio_ultima_hora(
    id_dispositivo: int, request: Request
) -> dict[str, float | None]:
    query = """
        SELECT
            AVG(voltaje_v) AS promedio_voltaje_v,
            AVG(corriente_a) AS promedio_corriente_a,
            AVG(potencia_w) AS promedio_potencia_w
        FROM lecturas_energia
        WHERE id_dispositivo = $1
          AND fecha >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {
        "promedio_voltaje_v": round(float(row["promedio_voltaje_v"]), 1) if row["promedio_voltaje_v"] is not None else 0.0,
        "promedio_corriente_a": round(float(row["promedio_corriente_a"]), 2) if row["promedio_corriente_a"] is not None else 0.0,
        "promedio_potencia_w": round(float(row["promedio_potencia_w"]), 1) if row["promedio_potencia_w"] is not None else 0.0,
    }


@app.get("/api/analitica/max-min/{id_dispositivo}")
async def maximo_minimo_potencia(
    id_dispositivo: int, request: Request
) -> dict[str, float | None]:
    query = """
        SELECT
            MAX(potencia_w) AS potencia_maxima_w,
            MIN(potencia_w) AS potencia_minima_w
        FROM lecturas_energia
        WHERE id_dispositivo = $1
          AND fecha::date = CURRENT_DATE
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {
        "potencia_maxima_w": round(float(row["potencia_maxima_w"]), 1) if row["potencia_maxima_w"] is not None else 0.0,
        "potencia_minima_w": round(float(row["potencia_minima_w"]), 1) if row["potencia_minima_w"] is not None else 0.0,
    }



@app.get("/api/analitica/tendencia/{id_dispositivo}")
async def desviacion_y_tendencia(
    id_dispositivo: int, request: Request
) -> dict[str, float | str]:
    query = """
        SELECT 
            STDDEV(potencia_w) AS desv_est,
            REGR_SLOPE(potencia_w, EXTRACT(EPOCH FROM fecha)) AS pendiente
        FROM lecturas_energia
        WHERE id_dispositivo = $1 
          AND fecha >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
    """
    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)
        
    desv = round(float(row["desv_est"]), 1) if row["desv_est"] is not None else 0.0
    pendiente = float(row["pendiente"]) if row["pendiente"] is not None else 0.0
    
    # Determinar la tendencia del consumo en base a la pendiente analítica
    tendencia_str = "Sube" if pendiente > 0.0001 else ("Baja" if pendiente < -0.0001 else "Estable")
    
    return {
        "desv_est": desv,
        "tendencia": tendencia_str
    }



@app.get("/api/analitica/outliers/{id_dispositivo}")
async def deteccion_outliers_hoy(
    id_dispositivo: int, request: Request
) -> dict[str, int]:
    query = """
        WITH estadisticas AS (
            SELECT 
                AVG(potencia_w) AS media, 
                STDDEV(potencia_w) AS desv 
            FROM lecturas_energia 
            WHERE id_dispositivo = $1
        )
        SELECT COUNT(*) AS total_outliers 
        FROM lecturas_energia, estadisticas
        WHERE id_dispositivo = $1 
          AND fecha::date = CURRENT_DATE
          AND desv > 0 -- Evita divisiones por cero o cálculos inválidos si hay un solo dato
          AND (potencia_w > (media + 3 * desv) OR potencia_w < (media - 3 * desv));
    """
    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)
        
    return {"outliers_hoy": row["total_outliers"] if row["total_outliers"] is not None else 0}


@app.get("/api/analitica/alertas-conteo/{id_dispositivo}")
async def conteo_alertas(id_dispositivo: int, request: Request) -> dict[str, int]:
    query = """
        SELECT COUNT(*) AS total_alertas
        FROM alertas_generadas
        WHERE id_dispositivo = $1
          AND fecha::date = CURRENT_DATE
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {"total_alertas": row["total_alertas"] if row["total_alertas"] is not None else 0}
