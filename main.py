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
    fecha_hora = datetime.now().astimezone()
    query = """
        INSERT INTO lecturas_energia (
            fecha_hora,
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
                fecha_hora,
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
          AND fecha_hora >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {
        "promedio_voltaje_v": row["promedio_voltaje_v"],
        "promedio_corriente_a": row["promedio_corriente_a"],
        "promedio_potencia_w": row["promedio_potencia_w"],
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
          AND fecha_hora::date = CURRENT_DATE
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {
        "potencia_maxima_w": row["potencia_maxima_w"],
        "potencia_minima_w": row["potencia_minima_w"],
    }


@app.get("/api/analitica/alertas-conteo/{id_dispositivo}")
async def conteo_alertas(id_dispositivo: int, request: Request) -> dict[str, int]:
    query = """
        SELECT COUNT(*) AS total_alertas
        FROM alertas_generadas
        WHERE id_dispositivo = $1
          AND fecha_hora::date = CURRENT_DATE
    """

    async with request.app.state.pool.acquire() as connection:
        row = await connection.fetchrow(query, id_dispositivo)

    return {"total_alertas": row["total_alertas"]}
